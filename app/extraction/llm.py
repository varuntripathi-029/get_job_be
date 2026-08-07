"""Provider-agnostic LLM chat client.

One entry point, `complete_json`, used by every prompt in the pipeline. Groq, xAI
and OpenAI share the OpenAI chat-completions protocol and differ only by base URL;
Gemini and Anthropic would need their own branches here (only Gemini's embedding
path is implemented so far — see `app.embeddings`).

Callers get `None` on failure rather than an exception. Extraction is best-effort
across thousands of pages: one bad response should drop one document, not fail a
crawl batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.config import OPENAI_PROTOCOL_PROVIDERS, settings

logger = logging.getLogger(__name__)

# The first request to a cold Groq model has been measured at ~14s against a
# ~130ms warm path, so the timeout is sized for the cold case. Anything lower
# turns the first call after an idle period into a spurious failure.
REQUEST_TIMEOUT_SECONDS = 45.0
MAX_ATTEMPTS = 3

_clients: dict[str, AsyncOpenAI] = {}

# Models are told to emit bare JSON, but some wrap it in ```json fences anyway.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass(slots=True)
class LLMResult:
    """A parsed JSON response plus the metadata we persist alongside extractions."""

    data: dict[str, Any]
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


def _client(provider: str) -> AsyncOpenAI:
    """Cached client per provider. Reused so connections stay pooled."""
    if provider not in _clients:
        _clients[provider] = AsyncOpenAI(
            api_key=settings.api_key_for(provider),
            base_url=settings.base_url_for(provider),
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,  # retried below so backoff is visible in logs
        )
    return _clients[provider]


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text.strip()


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object parse, tolerating fences and trailing prose."""
    candidate = _strip_fences(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Fall back to the outermost braces — handles a stray preamble.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


async def complete_json(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_content: str,
    max_input_chars: int | None = None,
    temperature: float = 0.0,
) -> LLMResult | None:
    """Run one chat completion expecting a JSON object back.

    Returns None if the provider has no key, every attempt failed, or the
    response could not be parsed as a JSON object.
    """
    if provider not in OPENAI_PROTOCOL_PROVIDERS:
        logger.error(
            "provider %r has no chat implementation; use one of %s",
            provider,
            OPENAI_PROTOCOL_PROVIDERS,
        )
        return None

    if not settings.api_key_for(provider):
        logger.warning("no API key for %s — skipping LLM call", provider)
        return None

    limit = max_input_chars or settings.llm_max_input_chars
    content = user_content[:limit]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = asyncio.get_running_loop().time()
        try:
            response = await _client(provider).chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 — provider SDKs raise many types
            if attempt == MAX_ATTEMPTS:
                logger.error(
                    "%s/%s failed after %d attempts: %s",
                    provider,
                    model,
                    MAX_ATTEMPTS,
                    exc,
                )
                return None
            backoff = 2 ** (attempt - 1)
            logger.warning(
                "%s/%s attempt %d failed (%s); retrying in %ss",
                provider,
                model,
                attempt,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)
            continue

        latency_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        text = response.choices[0].message.content or ""
        data = parse_json_object(text)
        if data is None:
            logger.warning(
                "%s/%s returned unparseable JSON: %.200s", provider, model, text
            )
            return None

        usage = response.usage
        return LLMResult(
            data=data,
            model=model,
            provider=provider,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            latency_ms=latency_ms,
        )

    return None


async def classify(system_prompt: str, content: str) -> LLMResult | None:
    """Run the cheap gating model."""
    return await complete_json(
        provider=settings.classifier_provider,
        model=settings.classifier_model,
        system_prompt=system_prompt,
        user_content=content,
    )


async def extract(system_prompt: str, content: str) -> LLMResult | None:
    """Run the expensive structured-output model."""
    return await complete_json(
        provider=settings.extractor_provider,
        model=settings.extractor_model,
        system_prompt=system_prompt,
        user_content=content,
    )
