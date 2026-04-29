"""
AgentCore Runtime entrypoint.

The BedrockAgentCoreApp framework receives POST /invocations with a JSON body,
calls the @app.entrypoint decorated function, and serialises the return value
back as JSON.

Expected invocation payload (same shape as the local CLI):
    {
        "full_name": "Timothy Kilpatrick",
        "organization_name": "Republic Services",
        "state": "NJ",
        "industryName": "Environmental Services"
    }

Optional field:
    "enrich": true    — trigger Path B (Phantom lead enrichment)

Response: same as agent.run_agent — {"email": "..."} or {"emails": [...]}
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Load .env if present (local testing); in the Runtime container env vars
# are injected directly so this is a no-op there.
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from bedrock_agentcore import BedrockAgentCoreApp

from agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("reworld.entrypoint")

app = BedrockAgentCoreApp()


@app.entrypoint
async def handler(payload: dict) -> dict:
    """Main invocation handler.

    The Runtime framework passes the parsed JSON body as ``payload``.
    Returns a dict which the framework serialises to JSON and returns
    to the caller.
    """
    logger.info("Received invocation: keys=%s", list(payload.keys()))
    enrich: bool = bool(payload.pop("enrich", False))
    result = run_agent(payload, enrich=enrich)
    logger.info("Invocation complete: result_keys=%s", list(result.keys()))
    return result


if __name__ == "__main__":
    # For local testing with uvicorn:
    #   python entrypoint.py
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
