"""Relevance gate prompt — the cheap half of the two-model strategy.

Runs on every page that clears the pre-filter, so it dominates LLM spend and is
deliberately terse: a boolean and one sentence, nothing else.
"""

VERSION = "classify_v1"

# Well below the model's limit. The gate only needs to know whether a signal is
# present, and the first few thousand characters of a page settle that.
MAX_INPUT_CHARS = 4000

SYSTEM_PROMPT = """You are a classifier for company hiring signals. Given text \
about a company, determine whether it contains any of these signals:
- Funding announcements (raised money, new investors)
- Office expansion (new office, new location)
- Leadership changes (new CTO, VP, CEO, key hires)
- Product launches
- Engineering team growth
- AI/ML division activity
- Infrastructure investment
- Acquisitions
- Strategic partnerships
- Career page updates (new jobs posted)
- Layoffs or restructuring

Festival greetings, culture posts, marketing copy and legal boilerplate are not \
signals.

Respond with ONLY valid JSON, no markdown:
{"is_relevant": true, "reason": "one sentence explaining why"}
or
{"is_relevant": false, "reason": "one sentence explaining why not"}"""
