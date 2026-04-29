#!/usr/bin/env python3
"""
Reworld Outreach Agent — local entrypoint.

Usage
-----
    echo '{"full_name":"Timothy Kilpatrick","organization_name":"Republic Services","state":"NJ","industryName":"Environmental Services"}' | python agent.py
    echo '{"full_name":"...","organization_name":"...","state":"...","industryName":"..."}' | python agent.py
    echo '{"company_name":"Republic Services","state":"NJ"}' | python agent.py --enrich

Exits non-zero if output cannot be coerced to valid JSON with key "email"
(or "emails" for multi-lead Path B).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

# Load .env before any module that reads os.environ
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import boto3
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from mcp_auth import get_gateway_mcp_transport  # type: ignore[import-not-found]
from templates import is_template_complete, render_email  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Logging — stderr only so stdout stays clean JSON
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s [%(name)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("reworld.agent")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are an outreach email composer for Reworld Waste Solutions.

## CRITICAL OUTPUT RULES — FOLLOW EXACTLY

1. Your ENTIRE response must be a single, valid JSON object. Nothing before it.
   Nothing after it. No markdown fences. No commentary. No "Here is...".

2. For a single lead, output EXACTLY:
   {"email": "<email body as a single string with \\n line breaks>"}

3. For multiple leads (Path B), output EXACTLY:
   {"emails": ["<email 1>", "<email 2>", ...]}

4. The email body starts with:
   Subject: Exploring compliant, sustainable waste solutions for <organization_name>

5. The email body ends with EXACTLY:
   Best regards,

   DO NOT add a name. DO NOT add a signature. DO NOT add any text after "Best regards,".
   The line is exactly "Best regards," — nothing more.

6. Substitute ONLY the four placeholders. Do NOT rewrite, paraphrase, or change
   any other word in the template.

## EMAIL TEMPLATE (substitute tokens VERBATIM)

Subject: Exploring compliant, sustainable waste solutions for {{ $json.organization_name }}

Hello {{ $json.full_name }},

I work with Reworld Waste Solutions, supporting businesses across {{ $json.state }} with compliant, sustainable waste management. We specialize in helping teams reduce landfill risk, stay ahead of regulations, and convert waste into resources like renewable energy and recovered materials.

Would you be open to a brief call to understand how you're currently handling {{ $json.industryName }} waste and see if there's an opportunity to improve compliance or reduce cost?

Best regards,

## AVAILABLE MCP TOOLS

The MCP server exposes PhantomBuster tools (all prefixed `phantombuster-poc-target___`):
- fetchOrgInfo — verify gateway connectivity (self-test)
- fetchAllAgents — list available Phantom agents
- fetchAgent — fetch details for one agent
- launchAgent — launch a Phantom (DO NOT call unless instructed via --enrich)
- fetchAgentOutput — poll agent run status
- fetchContainerOutput — fetch container output
- listContainers — list containers
- saveAgentArgument — save agent argument

When the user asks for gateway connectivity verification, call fetchOrgInfo first.
Otherwise, for Path A (all four template fields present), use the render_email
tool to compose the email — do NOT call any Phantom tools.
"""


# ---------------------------------------------------------------------------
# JSON extraction / guard
# ---------------------------------------------------------------------------
def _coerce_json(text: str) -> dict:
    """Extract and validate JSON from model output.

    Returns a dict with key 'email' (str) or 'emails' (list[str]).
    Raises ValueError if neither can be extracted.
    """
    # 1. Try direct parse
    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict) and ("email" in obj or "emails" in obj):
            return obj
    except json.JSONDecodeError:
        pass

    # 2. Try extracting first JSON object from the string
    match = re.search(r"\{[\s\S]*\}", stripped)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict) and ("email" in obj or "emails" in obj):
                return obj
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Model output could not be coerced to valid JSON with key 'email'/'emails'.\n"
        f"Raw output (first 400 chars):\n{text[:400]}"
    )


# ---------------------------------------------------------------------------
# Tool: render_email  (Strands @tool — model calls this for Path A)
# ---------------------------------------------------------------------------
from strands import tool  # noqa: E402  (after dotenv is loaded)


@tool
def render_email_tool(
    full_name: str,
    organization_name: str,
    state: str,
    industry_name: str,
) -> str:
    """Render the verbatim Reworld outreach email template.

    Call this tool when you have all four fields (full_name, organization_name,
    state, industryName).  Returns the complete email body as a plain string
    — you must wrap it in {"email": "<result>"} in your final response.

    Args:
        full_name: Lead's full name (e.g. "Timothy Kilpatrick").
        organization_name: Company name (e.g. "Republic Services").
        state: US state abbreviation or full name (e.g. "NJ").
        industry_name: Industry descriptor used in the template body
                       (e.g. "Environmental Services").
    """
    return render_email(
        full_name=full_name,
        organization_name=organization_name,
        state=state,
        industry_name=industry_name,
    )


# ---------------------------------------------------------------------------
# Core agent runner
# ---------------------------------------------------------------------------
def run_agent(payload: dict, *, enrich: bool = False) -> dict:
    """Run the outreach agent and return a validated dict.

    Path A (default): payload has all four template fields → compose email.
    Path B (--enrich): payload has company_name + state → enrich via Phantom.
    """
    import os

    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    region = os.environ.get("AWS_REGION", "us-east-1")

    boto_session = boto3.Session(region_name=region)
    model = BedrockModel(
        boto_session=boto_session,
        model_id=model_id,
        temperature=0.0,
        max_tokens=1024,
    )

    # Decide which prompt to send
    if enrich:
        # Path B: ask agent to use Phantom to find leads first
        company = payload.get("company_name", "")
        state = payload.get("state", "")
        phantom_agent_id = os.environ.get("PHANTOM_AGENT_ID", "").strip()
        if not phantom_agent_id:
            raise ValueError(
                "PHANTOM_AGENT_ID env var is required when enrich=True. "
                "Set it to the agent ID of the LinkedIn Sales Navigator Search "
                "Export Phantom in your PhantomBuster workspace."
            )
        user_message = (
            f"Enrich leads for company='{company}', state='{state}'. "
            f"Use the launchAgent MCP tool (agent id {phantom_agent_id}) with a "
            "salesNavigatorSearchUrl, poll fetchAgentOutput until status='finished', "
            "then call fetchContainerOutput. Parse the resultObject JSON array and "
            "produce one email per lead using the template. "
            "Return ONLY: {\"emails\": [\"...\", ...]}"
        )
    elif is_template_complete(payload):
        # Path A: direct compose via render_email_tool
        user_message = (
            f"Compose an outreach email using the render_email_tool with these values:\n"
            f"  full_name={json.dumps(payload['full_name'])}\n"
            f"  organization_name={json.dumps(payload['organization_name'])}\n"
            f"  state={json.dumps(payload['state'])}\n"
            f"  industry_name={json.dumps(payload['industryName'])}\n\n"
            "Return ONLY the JSON object: {\"email\": \"<result of render_email_tool>\"}\n"
            "No markdown. No commentary. No text before or after the JSON."
        )
    else:
        raise ValueError(
            "Payload missing required fields. Provide all four of: "
            "full_name, organization_name, state, industryName. "
            "Or pass --enrich with company_name + state."
        )

    # Pass MCPClient directly as a ToolProvider — do NOT wrap in a context
    # manager here.  The Strands Agent's tool-loading machinery calls
    # load_tools() → start() and manages the session lifetime.  Using both
    # `with MCPClient(...)` AND tools=[mcp] causes a double-start error
    # because __enter__ calls start() but does not set _tool_provider_started,
    # so load_tools() tries to start() again.
    mcp = MCPClient(get_gateway_mcp_transport())

    agent = Agent(
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        tools=[mcp, render_email_tool],
        # Suppress the streaming output to stderr; only JSON goes to stdout
        callback_handler=None,
    )
    result = agent(user_message)

    raw = str(result)
    return _coerce_json(raw)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Reworld Outreach Agent")
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Path B: enrich leads via PhantomBuster (consumes Phantom credits).",
    )
    args = parser.parse_args()

    # Read JSON from stdin
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid JSON on stdin: {exc}"}), file=sys.stdout)
        sys.exit(1)

    try:
        result = run_agent(payload, enrich=args.enrich)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stdout)
        sys.exit(1)
    except Exception as exc:
        logger.exception("Unexpected error")
        print(json.dumps({"error": str(exc)}), file=sys.stdout)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
