#!/usr/bin/env python3
"""
Invoke the deployed AgentCore Runtime with a JSON payload.

Reads the payload from stdin (matches `agent.py`'s interface so the same
echo|pipe pattern works). Resolves the runtime ARN from `AGENT_RUNTIME_ARN`
in `.env`, falling back to a name-based lookup against the control plane.

Usage:
    cd reworld-outreach-agent

    # Single payload from stdin:
    echo '{"full_name":"Tim K","organization_name":"Republic Services",
           "state":"NJ","industryName":"Environmental Services"}' \\
        | python invoke_runtime.py

    # Or pipe a file:
    cat payload.json | python invoke_runtime.py

    # Override the runtime to invoke:
    python invoke_runtime.py --runtime-name another_runtime <payload.json
    python invoke_runtime.py --runtime-arn arn:aws:bedrock-agentcore:... <payload.json

Exits 0 on a 200 from the runtime; non-zero on any error. Prints just the
runtime's response body to stdout — pipe-friendly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import dotenv_values

RUNTIME_NAME_DEFAULT = "reworld_outreach_agent"


def load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for k, v in dotenv_values(env_path).items():
            if v is not None and k not in os.environ:
                os.environ[k] = v


def resolve_runtime_arn(runtime_name: str, region: str) -> str:
    """Return the runtime ARN. Prefers AGENT_RUNTIME_ARN env, falls back to
    looking up by name via list_agent_runtimes.
    """
    explicit = os.environ.get("AGENT_RUNTIME_ARN", "").strip()
    if explicit:
        return explicit
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    for page in client.get_paginator("list_agent_runtimes").paginate():
        for rt in page.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == runtime_name:
                return rt["agentRuntimeArn"]
    raise SystemExit(
        f"runtime '{runtime_name}' not found in {region}. "
        "Run runtime_deploy.py first, or set AGENT_RUNTIME_ARN in .env."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime-name", default=RUNTIME_NAME_DEFAULT)
    parser.add_argument("--runtime-arn", default=None, help="Override; takes priority over --runtime-name and AGENT_RUNTIME_ARN.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()

    load_env()

    arn = args.runtime_arn or resolve_runtime_arn(args.runtime_name, args.region)

    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit("error: no JSON payload on stdin (pipe or redirect one in)")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"error: invalid JSON on stdin — {exc}")

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            payload=json.dumps(payload).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        sys.exit(f"runtime invocation failed: {code}: {msg}")

    body = resp["response"].read().decode("utf-8")
    if args.pretty:
        try:
            print(json.dumps(json.loads(body), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(body)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
