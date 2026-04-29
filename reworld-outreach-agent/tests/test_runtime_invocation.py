"""
End-to-end tests for the deployed AgentCore Runtime via invoke_runtime.py.

Each test runs the canonical CLI invocation:

    echo '{...}' | python invoke_runtime.py

…and asserts the response is strict JSON matching the template contract.

Mirrors what the workshop attendee actually types — if these pass, the
deployed runtime is reachable from this machine and producing the right
output for the documented sample payloads.

Skipped automatically when AGENT_RUNTIME_ARN isn't configured (so unit-only
runs in CI without AWS credentials don't fail).

Run:
    python -m pytest tests/test_runtime_invocation.py -v -m live
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values  # type: ignore[import-not-found]

PROJECT_ROOT = Path(__file__).parent.parent
INVOKE_SCRIPT = PROJECT_ROOT / "invoke_runtime.py"

# Push .env into os.environ so subprocess inherits AWS keys + AGENT_RUNTIME_ARN
for _k, _v in dotenv_values(PROJECT_ROOT / ".env").items():
    if _v is not None and _k not in os.environ:
        os.environ[_k] = _v


def _runtime_configured() -> bool:
    """True if invoke_runtime.py has enough config to reach a runtime."""
    if os.environ.get("AGENT_RUNTIME_ARN", "").strip():
        return True
    return bool(os.environ.get("AWS_ACCESS_KEY_ID", "").strip())


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not INVOKE_SCRIPT.exists(),
        reason="invoke_runtime.py not present in project root",
    ),
    pytest.mark.skipif(
        not _runtime_configured(),
        reason="AGENT_RUNTIME_ARN / AWS credentials not configured",
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# Sample payloads — the canonical Republic Services lead from DEPLOY.md plus
# variants exercising different template-token substitutions.
# ──────────────────────────────────────────────────────────────────────────────

CANONICAL_PAYLOAD = {
    "full_name": "Tim K",
    "organization_name": "Republic Services",
    "state": "NJ",
    "industryName": "Environmental Services",
}

VARIANT_PAYLOADS = [
    pytest.param(
        {
            "full_name": "Rebecca Reed",
            "organization_name": "Republic Services",
            "state": "NJ",
            "industryName": "Procurement",
        },
        id="republic_services_procurement",
    ),
    pytest.param(
        {
            "full_name": "Maria Gonzalez",
            "organization_name": "Waste Connections",
            "state": "TX",
            "industryName": "Operations",
        },
        id="waste_connections_tx_operations",
    ),
    pytest.param(
        {
            "full_name": "Jordan Lee",
            "organization_name": "Casella Waste Systems",
            "state": "VT",
            "industryName": "Sustainability",
        },
        id="casella_vt_sustainability",
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def invoke(payload: dict, timeout: float = 30.0) -> dict:
    """Run `echo '<payload>' | python invoke_runtime.py` and return the parsed
    JSON response. Raises a clear AssertionError if the call fails or output
    isn't parseable."""
    proc = subprocess.run(
        [sys.executable, str(INVOKE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, (
        f"invoke_runtime.py exited {proc.returncode}\n"
        f"stderr:\n{proc.stderr}\n"
        f"stdout:\n{proc.stdout}"
    )
    stdout = proc.stdout.strip()
    assert stdout, f"empty stdout from invoke_runtime.py\nstderr:\n{proc.stderr}"
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"invoke_runtime.py stdout is not JSON: {exc}\noutput: {stdout[:500]}")


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestInvokeRuntime:

    def test_canonical_payload_returns_strict_email_json(self):
        """Sanity check from the README: Tim K @ Republic Services / NJ /
        Environmental Services. Must round-trip and return {"email": "..."}."""
        result = invoke(CANONICAL_PAYLOAD)
        assert set(result.keys()) == {"email"}, (
            f"runtime response has unexpected keys: {list(result.keys())}"
        )
        assert isinstance(result["email"], str)

    def test_canonical_payload_subject_line(self):
        """First line of the email body must be the templated subject."""
        result = invoke(CANONICAL_PAYLOAD)
        first_line = result["email"].splitlines()[0]
        assert first_line == (
            f"Subject: Exploring compliant, sustainable waste solutions "
            f"for {CANONICAL_PAYLOAD['organization_name']}"
        )

    def test_canonical_payload_substitutes_all_tokens(self):
        """All four template tokens must appear verbatim in the output."""
        result = invoke(CANONICAL_PAYLOAD)
        body = result["email"]
        assert CANONICAL_PAYLOAD["full_name"] in body
        assert CANONICAL_PAYLOAD["organization_name"] in body
        assert CANONICAL_PAYLOAD["state"] in body
        assert CANONICAL_PAYLOAD["industryName"] in body

    def test_canonical_payload_no_unreplaced_tokens(self):
        """The Mustache-style placeholders must NOT survive into the output."""
        result = invoke(CANONICAL_PAYLOAD)
        for token in ("{{ $json.full_name }}", "{{ $json.organization_name }}",
                      "{{ $json.state }}", "{{ $json.industryName }}"):
            assert token not in result["email"], f"unreplaced token: {token}"

    def test_canonical_payload_no_extraneous_signature(self):
        """System prompt forbids the model from appending a sender name —
        the email must end with the bare 'Best regards,' line."""
        result = invoke(CANONICAL_PAYLOAD)
        assert result["email"].rstrip().endswith("Best regards,"), (
            f"unexpected trailing content:\n{result['email'][-200:]}"
        )

    def test_canonical_payload_no_markdown_or_preamble(self):
        """The output must be raw JSON, not wrapped in fences or with prose."""
        result = invoke(CANONICAL_PAYLOAD)
        assert "```" not in result["email"]
        # The model also shouldn't emit "Here is the email" etc.
        for forbidden in ("Here is the email", "Here's the email", "Below is"):
            assert forbidden not in result["email"]

    @pytest.mark.parametrize("payload", VARIANT_PAYLOADS)
    def test_variant_payload_round_trips(self, payload: dict):
        """Same shape contract holds across different orgs / states / industries."""
        result = invoke(payload)
        assert set(result.keys()) == {"email"}
        body = result["email"]
        for v in payload.values():
            assert v in body, f"value {v!r} missing from email"
        assert body.startswith(
            f"Subject: Exploring compliant, sustainable waste solutions "
            f"for {payload['organization_name']}"
        )
