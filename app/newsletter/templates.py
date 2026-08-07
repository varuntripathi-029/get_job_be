"""HTML email templates.

Plain strings with inline CSS. Email clients strip <style> blocks unpredictably
and none of them support a CSS framework, so every rule is on the element. A
templating engine would buy nothing here — there are three templates and no
inheritance between them.

All interpolated values pass through `esc()`. Company names come from crawled
pages, which makes them untrusted input reaching an HTML document.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from app.newsletter.schemas import NewsletterContent

BRAND = "HireSignal"
TAGLINE = "Hiring momentum from public signals"

# Momentum labels are colour-coded consistently across the product.
LABEL_COLORS = {
    "very_high": "#15803d",
    "high": "#4d7c0f",
    "moderate": "#a16207",
    "low": "#6b7280",
    "none": "#9ca3af",
}

EVENT_TYPE_LABELS = {
    "funding": "Funding",
    "new_office": "New office",
    "leadership_change": "Leadership change",
    "product_launch": "Product launch",
    "engineering_expansion": "Engineering expansion",
    "ai_division": "AI division",
    "infrastructure_investment": "Infrastructure investment",
    "acquisition": "Acquisition",
    "partnership": "Partnership",
    "layoff": "Layoff",
    "career_page_update": "Career page update",
}

_FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "Helvetica, Arial, sans-serif"
)
_BODY = f"margin:0;padding:0;background:#f4f4f5;font-family:{_FONT};"
_CARD = (
    "max-width:600px;margin:0 auto;background:#ffffff;"
    "border-radius:8px;overflow:hidden;"
)
_H2 = (
    "margin:0 0 12px;font-size:16px;font-weight:600;color:#111827;"
    "text-transform:uppercase;letter-spacing:0.04em;"
)
_CELL = "padding:10px 8px;border-bottom:1px solid #f1f5f9;font-size:14px;color:#374151;"
_LINK = "color:#2563eb;text-decoration:none;"
_MUTED = "font-size:12px;color:#6b7280;"


def esc(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def format_date(value: datetime | None) -> str:
    """'August 7, 2026'. Built by hand because the no-pad directive for the day
    differs by platform (%-d on glibc, %#d on Windows) and neither is portable."""
    if value is None:
        return ""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _shell(title: str, body: str, *, footer: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title></head>
<body style="{_BODY}">
<div style="padding:24px 12px;">
  <div style="{_CARD}">
    <div style="background:#111827;padding:20px 24px;">
      <div style="font-size:20px;font-weight:700;color:#ffffff;">{BRAND}</div>
      <div style="font-size:13px;color:#9ca3af;margin-top:2px;">{TAGLINE}</div>
    </div>
    <div style="padding:24px;">{body}</div>
    <div style="padding:16px 24px;background:#f9fafb;border-top:1px solid #e5e7eb;
                {_MUTED}line-height:1.6;">{footer}</div>
  </div>
</div>
</body></html>"""


CONFIRMATION_SUBJECT = f"Confirm your {BRAND} newsletter subscription"


def render_confirmation_email(confirm_url: str) -> str:
    body = f"""
<h1 style="margin:0 0 12px;font-size:20px;color:#111827;">Confirm your subscription</h1>
<p style="font-size:14px;color:#374151;line-height:1.6;margin:0 0 20px;">
  Every Monday we send one email covering which companies showed the strongest
  hiring signals that week — funding rounds, new offices, engineering expansion,
  and job posting surges. Every claim links to its source.
</p>
<p style="margin:0 0 24px;">
  <a href="{esc(confirm_url)}"
     style="display:inline-block;background:#2563eb;color:#ffffff;padding:12px 24px;
            border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">
    Confirm subscription
  </a>
</p>
<p style="{_MUTED}line-height:1.6;margin:0;">
  This link expires in 48 hours. If the button does not work, paste this into
  your browser:<br>
  <span style="word-break:break-all;color:#2563eb;">{esc(confirm_url)}</span>
</p>"""
    footer = (
        f"You received this because someone entered this address at {BRAND}. "
        "If that was not you, ignore this email — no subscription is created "
        "until the link above is clicked."
    )
    return _shell(CONFIRMATION_SUBJECT, body, footer=footer)


UNSUBSCRIBE_SUBJECT = "You've been unsubscribed"


def render_unsubscribe_email() -> str:
    body = f"""
<h1 style="margin:0 0 12px;font-size:20px;color:#111827;">You've been unsubscribed</h1>
<p style="font-size:14px;color:#374151;line-height:1.6;margin:0;">
  You will not receive any more {BRAND} newsletters. Your email address stays on
  file only to honour this preference — nothing else is sent to it.
</p>"""
    return _shell(UNSUBSCRIBE_SUBJECT, body, footer=f"{BRAND}")


def _movers_section(content: NewsletterContent, base_url: str) -> str:
    if not content.top_movers:
        return ""
    rows = []
    for mover in content.top_movers:
        color = LABEL_COLORS.get(mover.momentum_label, "#6b7280")
        label = esc(mover.momentum_label.replace("_", " "))
        sign = "+" if mover.delta >= 0 else ""
        rows.append(
            f"""<tr>
<td style="{_CELL}">
  <a href="{base_url}/companies/{esc(mover.slug)}" style="{_LINK}font-weight:600;">
    {esc(mover.name)}</a><br>
  <span style="font-size:12px;color:{color};font-weight:600;">{label}</span>
</td>
<td style="{_CELL}text-align:right;white-space:nowrap;">
  <span style="font-weight:600;color:#111827;">{mover.momentum_score:.0f}</span>
  <span style="{_MUTED}"> ({sign}{mover.delta:.0f})</span>
</td></tr>"""
        )
    return f"""<div style="margin-bottom:28px;">
<h2 style="{_H2}">Top movers this week</h2>
<table style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>
</div>"""


def _hotspots_section(content: NewsletterContent, base_url: str) -> str:
    if not content.hiring_hotspots:
        return ""
    rows = [
        f"""<tr>
<td style="{_CELL}">
  <a href="{base_url}/companies/{esc(h.slug)}" style="{_LINK}font-weight:600;">
    {esc(h.name)}</a>
</td>
<td style="{_CELL}text-align:right;white-space:nowrap;color:#111827;font-weight:600;">
  {h.new_jobs} new {"role" if h.new_jobs == 1 else "roles"}
</td></tr>"""
        for h in content.hiring_hotspots
    ]
    return f"""<div style="margin-bottom:28px;">
<h2 style="{_H2}">Hiring hotspots</h2>
<table style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>
</div>"""


def _events_section(content: NewsletterContent, base_url: str) -> str:
    if not content.notable_events:
        return ""
    grouped: dict[str, list] = {}
    for event in content.notable_events:
        grouped.setdefault(event.event_type, []).append(event)

    blocks = []
    for event_type, events in grouped.items():
        items = []
        for event in events:
            link = (
                f'<a href="{esc(event.evidence_url)}" style="{_LINK}">source</a>'
                if event.evidence_url
                else ""
            )
            items.append(
                f"""<li style="margin-bottom:8px;font-size:14px;color:#374151;
                               line-height:1.5;">
  <a href="{base_url}/companies/{esc(event.company_slug)}"
     style="{_LINK}font-weight:600;">{esc(event.company_name)}</a>
  — {esc(event.title)} {link}
</li>"""
            )
        blocks.append(
            f"""<div style="margin-bottom:16px;">
<div style="font-size:13px;font-weight:600;color:#6b7280;margin-bottom:6px;">
  {esc(EVENT_TYPE_LABELS.get(event_type, event_type))}
</div>
<ul style="margin:0;padding-left:18px;">{"".join(items)}</ul>
</div>"""
        )
    return f"""<div style="margin-bottom:28px;">
<h2 style="{_H2}">Notable events</h2>{"".join(blocks)}</div>"""


def _entrants_section(content: NewsletterContent, base_url: str) -> str:
    if not content.new_entrants:
        return ""
    items = [
        f"""<li style="margin-bottom:6px;font-size:14px;">
<a href="{base_url}/companies/{esc(c.slug)}" style="{_LINK}font-weight:600;">
  {esc(c.name)}</a>
{f'<span style="{_MUTED}"> — {esc(c.industry)}</span>' if c.industry else ""}
</li>"""
        for c in content.new_entrants
    ]
    return f"""<div style="margin-bottom:8px;">
<h2 style="{_H2}">Now tracking</h2>
<ul style="margin:0;padding-left:18px;">{"".join(items)}</ul></div>"""


def render_newsletter_html(
    content: NewsletterContent, unsubscribe_url: str, base_url: str
) -> str:
    """Render the weekly digest for one recipient.

    `unsubscribe_url` is per-subscriber, so this is called once per recipient
    rather than rendered a single time and reused.
    """
    base = base_url.rstrip("/")
    body = "".join(
        (
            f"""<p style="{_MUTED}margin:0 0 20px;">
Edition #{content.edition_number}
{f"· {esc(format_date(content.generated_at))}" if content.generated_at else ""}
</p>""",
            _movers_section(content, base),
            _hotspots_section(content, base),
            _events_section(content, base),
            _entrants_section(content, base),
        )
    )

    footer = f"""
<a href="{esc(unsubscribe_url)}" style="color:#6b7280;text-decoration:underline;">
  Unsubscribe</a>
&nbsp;·&nbsp; Powered by {BRAND}<br>
<span style="color:#9ca3af;">
  Scores reflect recent public activity, not predictions. {BRAND} does not claim
  any company will hire.
</span>"""
    return _shell(content.subject, body, footer=footer)
