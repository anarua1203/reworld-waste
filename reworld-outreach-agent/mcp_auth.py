"""
Cognito M2M token fetch with in-process cache, and a factory that produces a
Strands-compatible ``MCPTransport`` callable for the AgentCore Gateway.

Usage
-----
    from mcp_auth import get_gateway_mcp_transport
    from strands.tools.mcp import MCPClient

    with MCPClient(get_gateway_mcp_transport()) as mcp:
        agent = Agent(model=model, tools=[mcp], ...)
        result = agent("...")
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

import requests
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — pulled from environment at import time (loaded by agent.py via
# python-dotenv).  Fall back to empty strings so we get a clear error later.
# ---------------------------------------------------------------------------
_GATEWAY_URL: str = os.environ.get("GATEWAY_URL", "")
_TOKEN_URL: str = os.environ.get("TOKEN_URL", "")
_CLIENT_ID: str = os.environ.get("CLIENT_ID", "")
_CLIENT_SECRET: str = os.environ.get("CLIENT_SECRET", "")
_SCOPE: str = os.environ.get(
    "SCOPE",
    "phantombuster-api-gateway-id/gateway:read phantombuster-api-gateway-id/gateway:write",
)

# ---------------------------------------------------------------------------
# Token cache — one entry, refreshed ~5 minutes before expiry
# ---------------------------------------------------------------------------
_TOKEN_CACHE: dict[str, str | float] = {}
_CACHE_TTL_SECONDS = 55 * 60  # JWT TTL is 60 min; refresh at 55


def _fetch_token() -> str:
    """Exchange client credentials for a Cognito JWT (client_credentials grant)."""
    now = time.monotonic()
    cached_token = _TOKEN_CACHE.get("token")
    expires_at = _TOKEN_CACHE.get("expires_at", 0.0)

    if cached_token and now < float(expires_at):  # type: ignore[arg-type]
        logger.debug("Using cached Cognito token (expires in %.0fs)", float(expires_at) - now)
        return str(cached_token)

    logger.debug("Fetching new Cognito token from %s", _TOKEN_URL)
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "scope": _SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()

    token: str = body["access_token"]
    # Use expires_in from response if present; default to our TTL assumption.
    ttl: int = body.get("expires_in", 3600)
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + min(ttl - 60, _CACHE_TTL_SECONDS)

    logger.debug("New token obtained, valid for %ds", ttl)
    return token


def get_gateway_mcp_transport() -> Callable:
    """Return a transport callable that ``MCPClient`` will invoke.

    MCPClient expects a zero-argument callable that returns an async context
    manager yielding ``(read_stream, write_stream)``.  ``streamablehttp_client``
    returns exactly that.
    """
    def _transport_factory():
        token = _fetch_token()
        return streamablehttp_client(
            url=_GATEWAY_URL,
            headers={"Authorization": f"Bearer {token}"},
        )

    return _transport_factory
