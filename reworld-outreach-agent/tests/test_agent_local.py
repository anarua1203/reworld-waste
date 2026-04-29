"""
Local integration tests for the Reworld Outreach Agent.

These tests exercise the full agent pipeline against the live AgentCore Gateway
(Path A — direct compose).  They require valid credentials in .env.

Run with:
    cd /Users/anarua/Documents/work-env/poc_aws/reworld-outreach-agent
    python -m pytest tests/test_agent_local.py -v

To skip the live gateway round-trip and test only the template/guard logic:
    python -m pytest tests/test_agent_local.py -v -k "not live"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so we can import agent modules
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env before any module that reads os.environ
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from templates import render_email, is_template_complete  # type: ignore[import-not-found]
from agent import _coerce_json, run_agent  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Sample inputs
# ---------------------------------------------------------------------------
SAMPLE_INPUT = {
    "full_name": "Timothy Kilpatrick",
    "organization_name": "Republic Services",
    "state": "NJ",
    "industryName": "Environmental Services",
}

EXPECTED_EMAIL = (
    "Subject: Exploring compliant, sustainable waste solutions for Republic Services\n"
    "\n"
    "Hello Timothy Kilpatrick,\n"
    "\n"
    "I work with Reworld Waste Solutions, supporting businesses across NJ with compliant, "
    "sustainable waste management. We specialize in helping teams reduce landfill risk, stay ahead of "
    "regulations, and convert waste into resources like renewable energy and recovered materials.\n"
    "\n"
    "Would you be open to a brief call to understand how you're currently handling Environmental Services "
    "waste and see if there's an opportunity to improve compliance or reduce cost?\n"
    "\n"
    "Best regards,"
)


# ---------------------------------------------------------------------------
# Unit tests — template & guard (no network calls)
# ---------------------------------------------------------------------------

class TestTemplate:
    def test_render_email_substitutes_all_tokens(self):
        result = render_email(
            full_name="Timothy Kilpatrick",
            organization_name="Republic Services",
            state="NJ",
            industry_name="Environmental Services",
        )
        assert result == EXPECTED_EMAIL

    def test_render_email_subject_line(self):
        result = render_email("A", "Acme Corp", "CA", "Tech")
        assert result.startswith("Subject: Exploring compliant, sustainable waste solutions for Acme Corp")

    def test_render_email_ends_with_best_regards(self):
        result = render_email("A", "B", "C", "D")
        # Must end exactly with "Best regards," — no trailing name or newline
        assert result.endswith("Best regards,")

    def test_render_email_no_unreplaced_tokens(self):
        result = render_email("A", "B", "C", "D")
        assert "{{ $json." not in result

    def test_is_template_complete_true(self):
        assert is_template_complete(SAMPLE_INPUT) is True

    def test_is_template_complete_missing_field(self):
        incomplete = {k: v for k, v in SAMPLE_INPUT.items() if k != "industryName"}
        assert is_template_complete(incomplete) is False

    def test_is_template_complete_empty_field(self):
        data = dict(SAMPLE_INPUT, full_name="")
        assert is_template_complete(data) is False


class TestJsonGuard:
    def test_coerce_clean_json(self):
        raw = '{"email": "hello world"}'
        result = _coerce_json(raw)
        assert result == {"email": "hello world"}

    def test_coerce_json_with_preamble(self):
        raw = 'Here is the email:\n{"email": "hello world"}'
        result = _coerce_json(raw)
        assert result == {"email": "hello world"}

    def test_coerce_json_with_markdown_fence(self):
        raw = '```json\n{"email": "hello"}\n```'
        result = _coerce_json(raw)
        assert result == {"email": "hello"}

    def test_coerce_json_emails_key(self):
        raw = '{"emails": ["a", "b"]}'
        result = _coerce_json(raw)
        assert result == {"emails": ["a", "b"]}

    def test_coerce_json_raises_on_invalid(self):
        with pytest.raises(ValueError, match="coerced"):
            _coerce_json("This is just prose, no JSON at all.")

    def test_coerce_json_raises_on_wrong_key(self):
        with pytest.raises(ValueError):
            _coerce_json('{"wrong_key": "value"}')


# ---------------------------------------------------------------------------
# Live integration test — requires gateway connectivity
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestAgentLive:
    """Requires valid GATEWAY_URL / CLIENT_SECRET in .env."""

    def test_path_a_sample_input(self):
        """Feed the Timothy Kilpatrick sample through the full agent pipeline."""
        result = run_agent(SAMPLE_INPUT)

        # Must be a dict with key "email"
        assert isinstance(result, dict), f"Expected dict, got {type(result)}: {result}"
        assert "email" in result, f"Missing 'email' key: {result}"

        email_body: str = result["email"]

        # Subject line check
        assert email_body.startswith(
            "Subject: Exploring compliant, sustainable waste solutions for Republic Services"
        ), f"Subject line mismatch:\n{email_body[:200]}"

        # Name substitution
        assert "Timothy Kilpatrick" in email_body

        # State substitution
        assert " NJ " in email_body or email_body.count("NJ") >= 1

        # Industry substitution
        assert "Environmental Services" in email_body

        # Ends exactly with "Best regards," — no extra name/signature
        assert email_body.strip().endswith("Best regards,"), (
            f"Email must end with 'Best regards,' but got: ...{email_body[-100:]!r}"
        )

        # No unreplaced template tokens
        assert "{{ $json." not in email_body

        # No markdown fences leaked through
        assert "```" not in email_body

    def test_path_a_output_matches_template_token_for_token(self):
        """Token-for-token comparison against the deterministic render_email result."""
        result = run_agent(SAMPLE_INPUT)
        expected = EXPECTED_EMAIL
        actual = result["email"]

        assert actual == expected, (
            f"Token-for-token mismatch.\n"
            f"Expected:\n{expected!r}\n\n"
            f"Got:\n{actual!r}"
        )

    def test_path_a_output_is_strict_json_serialisable(self):
        """Verify the full output round-trips through json.loads."""
        result = run_agent(SAMPLE_INPUT)
        serialised = json.dumps(result, ensure_ascii=False)
        reloaded = json.loads(serialised)
        assert reloaded == result


# ---------------------------------------------------------------------------
# Allow running directly: python tests/test_agent_local.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
