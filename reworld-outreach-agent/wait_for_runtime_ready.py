#!/usr/bin/env python3
"""
Poll the AgentCore Runtime status until it reaches READY.

Used immediately after `runtime_deploy.py` so the next step (invocation) only
runs when the runtime can actually serve traffic. Idempotent — exits 0 if the
runtime is already READY, exits 1 on terminal failure (CREATE_FAILED, etc.).

Usage:
    cd reworld-outreach-agent
    python wait_for_runtime_ready.py [--runtime-name reworld_outreach_agent]
                                     [--region us-east-1] [--timeout 300]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import boto3
from dotenv import dotenv_values

RUNTIME_NAME_DEFAULT = "reworld_outreach_agent"
TERMINAL_FAILURE = {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED", "DELETING", "DELETED"}


def load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for k, v in dotenv_values(env_path).items():
            if v is not None and k not in os.environ:
                os.environ[k] = v


def find_runtime_id(client, runtime_name: str) -> tuple[str, str] | None:
    """Return (runtime_id, runtime_arn) for the runtime with the given name,
    or None if not found.
    """
    for page in client.get_paginator("list_agent_runtimes").paginate():
        for rt in page.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == runtime_name:
                return rt["agentRuntimeId"], rt["agentRuntimeArn"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime-name", default=RUNTIME_NAME_DEFAULT)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--timeout", type=int, default=300, help="Max seconds to wait (default 300).")
    parser.add_argument("--poll-every", type=int, default=5, help="Seconds between polls (default 5).")
    args = parser.parse_args()

    load_env()
    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    found = find_runtime_id(client, args.runtime_name)
    if not found:
        print(f"✗ runtime '{args.runtime_name}' not found in {args.region}.")
        print("  Run runtime_deploy.py first to create it.")
        return 1
    runtime_id, runtime_arn = found

    print(f"runtime: {runtime_id}")
    print(f"polling for READY (timeout={args.timeout}s, every {args.poll_every}s)…")

    waited = 0
    while waited <= args.timeout:
        r = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = r.get("status", "<unknown>")
        version = r.get("agentRuntimeVersion", "?")
        print(f"  [{waited:>3}s] status={status} version={version}")
        if status == "READY":
            print(f"\n✓ runtime READY — ARN:")
            print(f"  {runtime_arn}")
            print(f"\nAdd to your .env so other tools (invoke_runtime.py, streamlit GUI) pick it up:")
            print(f"  AGENT_RUNTIME_ARN={runtime_arn}")
            return 0
        if status in TERMINAL_FAILURE:
            print(f"\n✗ runtime entered terminal status: {status}")
            return 1
        time.sleep(args.poll_every)
        waited += args.poll_every

    print(f"\n✗ timeout — runtime still not READY after {args.timeout}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
