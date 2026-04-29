#!/usr/bin/env python3
"""
End-to-end test for the PhantomBuster AgentCore Gateway.

Two independent layers, each runnable on its own:

  Layer 1 (direct):   call PhantomBuster's REST API directly with the workspace
                      API key. Validates that the endpoints in our OpenAPI spec
                      and the X-Phantombuster-Key header are correct. Requires
                      PHANTOMBUSTER_API_KEY in .env. No AWS needed.

  Layer 2 (gateway):  go through AgentCore Gateway over MCP (JSON-RPC) using a
                      Cognito M2M token. Validates the full chain — Cognito JWT
                      authorizer, gateway target, OpenAPI tool generation, and
                      outbound API_KEY credential injection. Requires
                      credentials.env (produced by get_credentials.py).

Read-only operations only — never calls launchAgent or saveAgentArgument so the
test cannot consume PhantomBuster credits or mutate workspace state.

Usage:
    python test_gateway.py              # both layers
    python test_gateway.py --direct     # PhantomBuster only
    python test_gateway.py --gateway    # AgentCore Gateway only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values

HERE = Path(__file__).parent
SPEC_PATH = HERE / "openapi-specs" / "phantombuster-v2.json"
PB_BASE = "https://api.phantombuster.com/api/v2"
TIMEOUT = 30


# ──────────────────────────────────────────────────────────────────────────────
# Tiny test runner — no pytest dep, keeps the project's "single-file scripts"
# style. Each test is a function returning None on pass or raising on fail.
# ──────────────────────────────────────────────────────────────────────────────

class TestResult:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def run(self, name: str, fn, *, skip_reason: str | None = None) -> None:
        if skip_reason:
            print(f"  ⤳ SKIP  {name}  ({skip_reason})")
            self.skipped += 1
            return
        try:
            fn()
        except AssertionError as e:
            print(f"  ✗ FAIL  {name}\n          {e}")
            self.failed += 1
        except Exception as e:  # noqa: BLE001 — surface anything else as a failure
            print(f"  ✗ FAIL  {name}\n          {type(e).__name__}: {e}")
            self.failed += 1
        else:
            print(f"  ✓ PASS  {name}")
            self.passed += 1

    def summary(self) -> int:
        total = self.passed + self.failed + self.skipped
        print(f"\n{self.passed}/{total} passed · {self.failed} failed · {self.skipped} skipped")
        return 1 if self.failed else 0


# ──────────────────────────────────────────────────────────────────────────────
# Spec loader — single source of truth for expected operationIds and paths
# ──────────────────────────────────────────────────────────────────────────────

def load_spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text())


def expected_operations(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Return [{operationId, method, path}] for every operation in the spec."""
    ops = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            ops.append({
                "operationId": op["operationId"],
                "method": method.upper(),
                "path": path,
            })
    return ops


# ──────────────────────────────────────────────────────────────────────────────
# Layer 1 — direct PhantomBuster calls
# ──────────────────────────────────────────────────────────────────────────────

def pb_get(api_key: str, path: str, params: dict | None = None) -> requests.Response:
    return requests.get(
        f"{PB_BASE}{path}",
        headers={"X-Phantombuster-Key": api_key, "Accept": "application/json"},
        params=params or {},
        timeout=TIMEOUT,
    )


def run_direct_layer(env: dict[str, str], spec: dict[str, Any], r: TestResult) -> None:
    print("\n── Layer 1: direct PhantomBuster calls ──────────────────────────────")
    api_key = env.get("PHANTOMBUSTER_API_KEY", "").strip()

    placeholder = not api_key or api_key == "placeholder"
    skip = "PHANTOMBUSTER_API_KEY missing or still 'placeholder'" if placeholder else None

    def reachable() -> None:
        # No auth — should still get a structured 401 from the live service,
        # never a connection error or 5xx.
        resp = requests.get(f"{PB_BASE}/orgs/fetch", timeout=TIMEOUT)
        assert resp.status_code == 401, f"expected 401 without auth, got {resp.status_code}"

    def auth_works() -> None:
        resp = pb_get(api_key, "/orgs/fetch")
        assert resp.status_code == 200, (
            f"GET /orgs/fetch with key returned {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        assert isinstance(body, dict), f"expected JSON object, got {type(body).__name__}"
        # Loose schema check — PhantomBuster has been known to return either
        # the org object directly or a wrapper. Accept anything object-shaped.
        assert body, "empty response from /orgs/fetch"

    def list_agents() -> None:
        resp = pb_get(api_key, "/agents/fetch-all")
        assert resp.status_code == 200, (
            f"GET /agents/fetch-all returned {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        # API may return either a list directly or {"agents": [...]} — both fine
        agents = body if isinstance(body, list) else body.get("agents", body)
        assert isinstance(agents, (list, dict)), (
            f"unexpected /agents/fetch-all shape: {type(agents).__name__}"
        )

    def all_spec_paths_resolve() -> None:
        # Probe every documented path with a known-bad key. 401 means the route
        # exists; 404 means our spec drifted from the real API.
        bad_key_404s = []
        for op in expected_operations(spec):
            if op["method"] != "GET":
                continue  # POST routes (launchAgent, saveAgentArgument) — never invoke for real
            resp = requests.request(
                op["method"],
                f"{PB_BASE}{op['path']}",
                headers={"X-Phantombuster-Key": "intentionally-invalid-for-probe"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 404:
                bad_key_404s.append(f"{op['method']} {op['path']}")
        assert not bad_key_404s, f"spec contains paths not on the real API: {bad_key_404s}"

    r.run("PhantomBuster API is reachable (no-auth → 401)", reachable, skip_reason=None)
    r.run("API key authenticates against /orgs/fetch", auth_works, skip_reason=skip)
    r.run("/agents/fetch-all returns a usable shape", list_agents, skip_reason=skip)
    r.run("every GET path in our OpenAPI spec exists upstream", all_spec_paths_resolve)


# ──────────────────────────────────────────────────────────────────────────────
# Layer 2 — AgentCore Gateway over MCP
# ──────────────────────────────────────────────────────────────────────────────

def cognito_token(env: dict[str, str]) -> str:
    resource_id = env.get("RESOURCE_SERVER_ID") or "phantombuster-api-gateway-id"
    scope = f"{resource_id}/gateway:read {resource_id}/gateway:write"
    resp = requests.post(
        env["TOKEN_URL"],
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(env["CLIENT_ID"], env["CLIENT_SECRET"]),
        data={"grant_type": "client_credentials", "scope": scope},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def mcp_call(env: dict[str, str], token: str, method: str, params: dict | None = None) -> dict:
    resp = requests.post(
        env["GATEWAY_URL"],
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def parse_mcp_text_payload(mcp_response: dict) -> dict:
    """Extract the JSON the upstream API returned, given a tools/call response."""
    if "error" in mcp_response:
        raise AssertionError(f"MCP error: {mcp_response['error']}")
    content = mcp_response.get("result", {}).get("content", [])
    assert content and content[0].get("type") == "text", (
        f"unexpected MCP content shape: {mcp_response.get('result')}"
    )
    return json.loads(content[0]["text"])


def run_gateway_layer(env: dict[str, str], spec: dict[str, Any], r: TestResult) -> None:
    print("\n── Layer 2: AgentCore Gateway via MCP ───────────────────────────────")
    needed = ("GATEWAY_URL", "CLIENT_ID", "CLIENT_SECRET", "TOKEN_URL")
    missing = [k for k in needed if not env.get(k)]
    skip_all = (
        f"missing {missing} — run get_credentials.py first" if missing else None
    )
    if skip_all:
        for name in (
            "Cognito client_credentials grant returns a JWT",
            "MCP tools/list exposes every operationId in the spec",
            "fetchOrgInfo via gateway matches direct API response",
        ):
            r.run(name, lambda: None, skip_reason=skip_all)
        return

    target_name = env.get("TARGET_NAME") or "phantombuster-poc-target"
    tool_prefix = f"{target_name}___"
    state: dict[str, Any] = {}

    def get_token() -> None:
        token = cognito_token(env)
        assert token and len(token) > 50, "token looks malformed"
        state["token"] = token

    def list_tools() -> None:
        assert "token" in state, "token step did not run"
        resp = mcp_call(env, state["token"], "tools/list")
        tools = resp.get("result", {}).get("tools", [])
        assert tools, f"gateway returned no tools: {resp}"
        tool_names = {t["name"] for t in tools}
        expected = {tool_prefix + op["operationId"] for op in expected_operations(spec)}
        missing_tools = expected - tool_names
        assert not missing_tools, (
            f"gateway is missing {len(missing_tools)} expected tools: "
            f"{sorted(missing_tools)}\n          got: {sorted(tool_names)}"
        )
        state["tools"] = tools

    def org_info_matches_direct() -> None:
        api_key = env.get("PHANTOMBUSTER_API_KEY", "").strip()
        if not api_key or api_key == "placeholder":
            raise AssertionError(
                "cannot cross-check gateway vs direct without a real "
                "PHANTOMBUSTER_API_KEY (gateway round-trip alone says nothing "
                "about whether the upstream key was actually injected)"
            )
        gw_resp = mcp_call(env, state["token"], "tools/call", {
            "name": tool_prefix + "fetchOrgInfo",
            "arguments": {},
        })
        gw_payload = parse_mcp_text_payload(gw_resp)

        direct = pb_get(api_key, "/orgs/fetch").json()

        # The gateway forwards the upstream JSON verbatim. If both calls
        # succeeded, the org id (or name) should be identical.
        gw_id = gw_payload.get("id") or gw_payload.get("data", {}).get("id")
        direct_id = direct.get("id") or direct.get("data", {}).get("id")
        if gw_id is not None and direct_id is not None:
            assert gw_id == direct_id, (
                f"org id mismatch — gateway: {gw_id!r}, direct: {direct_id!r}. "
                "The gateway may be hitting a different workspace than your key."
            )
        else:
            # Fall back to a structural check: both responses must be non-empty
            # objects. Anything else means one of the two paths failed silently.
            assert gw_payload and isinstance(gw_payload, dict), (
                f"gateway payload empty/non-object: {gw_payload!r}"
            )
            assert direct and isinstance(direct, dict), (
                f"direct payload empty/non-object: {direct!r}"
            )

    r.run("Cognito client_credentials grant returns a JWT", get_token)
    r.run("MCP tools/list exposes every operationId in the spec", list_tools)
    r.run(
        "fetchOrgInfo via gateway matches direct API response",
        org_info_matches_direct,
        skip_reason=(
            None
            if env.get("PHANTOMBUSTER_API_KEY", "").strip() not in ("", "placeholder")
            else "no real PHANTOMBUSTER_API_KEY to cross-check against"
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    """Merge .env, credentials.env, and the process environment.

    credentials.env wins over .env (same convention as streamlit_app.py),
    real env vars win over both so users can override on the command line.
    """
    merged: dict[str, str] = {}
    for path in (HERE / ".env", HERE / "credentials.env"):
        if path.exists():
            merged.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    for key in (
        "GATEWAY_URL", "CLIENT_ID", "CLIENT_SECRET", "TOKEN_URL",
        "RESOURCE_SERVER_ID", "TARGET_NAME", "PHANTOMBUSTER_API_KEY",
    ):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--direct", action="store_true", help="run only Layer 1")
    parser.add_argument("--gateway", action="store_true", help="run only Layer 2")
    args = parser.parse_args()
    run_direct = args.direct or not args.gateway
    run_gw = args.gateway or not args.direct

    if not SPEC_PATH.exists():
        print(f"OpenAPI spec not found at {SPEC_PATH}")
        return 1

    env = load_env()
    spec = load_spec()
    results = TestResult()

    print("PhantomBuster Gateway — end-to-end tests")
    print(f"  spec: {SPEC_PATH.name}  ({len(expected_operations(spec))} operations)")

    if run_direct:
        run_direct_layer(env, spec, results)
    if run_gw:
        run_gateway_layer(env, spec, results)

    return results.summary()


if __name__ == "__main__":
    sys.exit(main())
