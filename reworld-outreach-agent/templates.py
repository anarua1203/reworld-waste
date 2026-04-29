"""
Email template for the Reworld outreach agent.

The template text is the source of truth — only the four {{ $json.X }} tokens
are substituted.  Everything else (whitespace, line breaks, "Best regards,") is
intentionally verbatim and must not be changed by the model.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Verbatim template.  Do NOT reformat — trailing newlines are load-bearing.
# ---------------------------------------------------------------------------
OUTREACH_TEMPLATE = (
    "Subject: Exploring compliant, sustainable waste solutions for {{ $json.organization_name }}\n"
    "\n"
    "Hello {{ $json.full_name }},\n"
    "\n"
    "I work with Reworld Waste Solutions, supporting businesses across {{ $json.state }} with compliant, "
    "sustainable waste management. We specialize in helping teams reduce landfill risk, stay ahead of "
    "regulations, and convert waste into resources like renewable energy and recovered materials.\n"
    "\n"
    "Would you be open to a brief call to understand how you're currently handling {{ $json.industryName }} "
    "waste and see if there's an opportunity to improve compliance or reduce cost?\n"
    "\n"
    "Best regards,"
)

# Human-readable token names that appear in the template
_TOKENS = {
    "full_name": "{{ $json.full_name }}",
    "organization_name": "{{ $json.organization_name }}",
    "state": "{{ $json.state }}",
    "industryName": "{{ $json.industryName }}",
}


def render_email(
    full_name: str,
    organization_name: str,
    state: str,
    industry_name: str,
) -> str:
    """Return the verbatim outreach email with tokens substituted.

    The result is a plain string (no JSON wrapping).  The caller must wrap it
    in ``{"email": ...}`` if needed.
    """
    text = OUTREACH_TEMPLATE
    text = text.replace(_TOKENS["full_name"], full_name)
    text = text.replace(_TOKENS["organization_name"], organization_name)
    text = text.replace(_TOKENS["state"], state)
    text = text.replace(_TOKENS["industryName"], industry_name)
    return text


def is_template_complete(data: dict) -> bool:
    """Return True when all four template fields are present and non-empty."""
    required = ("full_name", "organization_name", "state", "industryName")
    return all(data.get(k, "").strip() for k in required)
