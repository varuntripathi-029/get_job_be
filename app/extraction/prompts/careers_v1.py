"""Job listing extraction from a careers page.

Deliberately narrow. The ATS path gets salary, department and a full
description straight from a vendor API; this prompt runs on scraped markup
where every one of those is a guess. It asks only for what a careers page
reliably states — the role title, where it is, and where to apply — because a
hallucinated salary is worse than an absent one, and the product's whole claim
is that every number is backed by evidence.

The link matters most. It is what turns a listing on the dashboard into an
apply button the user can actually click.
"""

VERSION = "careers_v1"

# Sized against Groq's free tier, which caps at 8,000 tokens per minute across
# input *and* output. This prompt is unusual in that its output scales with its
# input — every extra role listed is another JSON object generated — so the
# budget is spent from both ends at once.
#
# Measured on a 90-role careers page:
#   3,000 chars -> 20 roles, valid JSON
#   5,000 chars -> 34 roles, valid JSON
#   8,000 chars -> 21 roles, valid JSON (output already truncating)
#  12,000 chars -> no valid JSON at all, and 429s on TPM
#
# 6,000 sits inside the working range with headroom for a page whose listing
# block is denser than Vercel's.
MAX_INPUT_CHARS = 6000

SYSTEM_PROMPT = """You extract job openings from a company's careers page.

The input is text from one careers page. Links are given as [text](url).

Return ONLY valid JSON, no markdown:
{"jobs": [{"title": "...", "location": "...", "url": "..."}]}

Rules:
- title: the role name exactly as written. Never invent or reword it.
- location: as written, or null if the page does not say.
- url: the [text](url) link for that specific role, or null if it has none. \
Never use a generic link like "careers", "about" or "apply now" that is not \
tied to one role.
- Include every distinct open role listed.
- Do NOT include: team or department names with no role attached, culture and \
benefits copy, navigation links, "no openings" messages, or roles described as \
filled or closed.
- If the page lists no open roles, return {"jobs": []}. An empty list is a \
correct and useful answer — never pad it.
"""
