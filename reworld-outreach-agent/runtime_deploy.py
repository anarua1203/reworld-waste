#!/usr/bin/env python3
"""
AgentCore Runtime deployment script for the Reworld Outreach Agent.

This script:
1. Builds and pushes the Docker image to ECR.
2. Creates (or updates) the AgentCore Runtime via boto3.

DO NOT run this script without:
  - A running Docker daemon
  - ECR repository already created (or --create-ecr flag)
  - IAM role with correct trust policy (see docs/iam_role_policy.json below)
  - .env loaded or env vars set

Usage
-----
    python runtime_deploy.py [--dry-run] [--create-ecr]

Flags
-----
    --dry-run      Print all commands/API calls without executing them.
    --create-ecr   Create the ECR repository if it doesn't exist.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import boto3

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ECR_REPO_NAME = os.environ.get("ECR_REPO_NAME", "reworld-outreach-agent")
IMAGE_TAG = os.environ.get("IMAGE_TAG", "latest")
RUNTIME_NAME = os.environ.get("RUNTIME_NAME", "reworld_outreach_agent")  # AgentCore regex disallows hyphens
IAM_ROLE_NAME = os.environ.get("IAM_ROLE_NAME", "reworld-outreach-agent-runtime")


def _get_account_id() -> str:
    """Resolve the active AWS account ID from STS, falling back to the
    AWS_ACCOUNT_ID env var. Never hardcode — keeps the script account-agnostic.
    """
    explicit = os.environ.get("AWS_ACCOUNT_ID", "").strip()
    if explicit:
        return explicit
    import boto3 as _boto3
    return _boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Account"]


AWS_ACCOUNT_ID = _get_account_id()
ECR_URI = f"{AWS_ACCOUNT_ID}.dkr.ecr.{AWS_REGION}.amazonaws.com/{ECR_REPO_NAME}:{IMAGE_TAG}"

# ---------------------------------------------------------------------------
# IAM role trust and permissions policies (reference — create manually or
# via the snippet in README.md)
# ---------------------------------------------------------------------------
TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

PERMISSIONS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        # Bedrock InvokeModel for the cross-region inference profile
        {
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                f"arn:aws:bedrock:{AWS_REGION}::foundation-model/*",
                f"arn:aws:bedrock:us-east-1:{AWS_ACCOUNT_ID}:inference-profile/*",
            ],
        },
        # Allow reading the Cognito CLIENT_SECRET from Secrets Manager
        {
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": [
                f"arn:aws:secretsmanager:{AWS_REGION}:{AWS_ACCOUNT_ID}:secret:reworld/outreach-agent/*"
            ],
        },
        # ECR pull for the runtime image
        {
            "Effect": "Allow",
            "Action": [
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:GetAuthorizationToken",
            ],
            "Resource": "*",
        },
        # CloudWatch Logs
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            "Resource": "*",
        },
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], dry_run: bool) -> None:
    print("$", " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def ecr_login(dry_run: bool) -> None:
    """Get an ECR auth token via boto3 (uses the same creds as the rest of
    the script — no dependency on the `aws` CLI's shell environment) and pipe
    it to `docker login` over stdin.
    """
    registry = f"{AWS_ACCOUNT_ID}.dkr.ecr.{AWS_REGION}.amazonaws.com"
    print(f"\n--- ECR login ({registry}) ---")
    if dry_run:
        print(f"$ <boto3 GetAuthorizationToken> | docker login --username AWS --password-stdin {registry}")
        return

    import base64
    ecr = boto3.client("ecr", region_name=AWS_REGION)
    auth = ecr.get_authorization_token()["authorizationData"][0]
    token = base64.b64decode(auth["authorizationToken"]).decode()
    _, password = token.split(":", 1)

    proc = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", registry],
        input=password.encode(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker login failed (exit {proc.returncode}): "
            f"{proc.stderr.decode()[:500]}"
        )
    print(proc.stdout.decode().strip())


def build_and_push(dry_run: bool) -> None:
    project_dir = str(Path(__file__).parent)
    # AgentCore Runtime requires linux/arm64 (Graviton). buildx with --load
    # produces a single-arch image we can tag and push normally.
    print("\n--- Build Docker image (linux/arm64) ---")
    run([
        "docker", "buildx", "build",
        "--platform", "linux/arm64",
        "--load",
        "-t", f"{ECR_REPO_NAME}:latest",
        project_dir,
    ], dry_run)

    print("\n--- Tag for ECR ---")
    run(["docker", "tag", f"{ECR_REPO_NAME}:latest", ECR_URI], dry_run)

    ecr_login(dry_run)

    print("\n--- Push to ECR ---")
    run(["docker", "push", ECR_URI], dry_run)


def create_or_update_runtime(dry_run: bool) -> None:
    """Register the AgentCore Runtime via bedrock-agentcore-control plane.

    The boto3 service model for CreateAgentRuntime puts containerUri inside
    agentRuntimeArtifact.containerConfiguration, but roleArn,
    networkConfiguration, environmentVariables, and protocolConfiguration are
    all top-level parameters. protocolConfiguration.serverProtocol is required.
    """
    print("\n--- Create AgentCore Runtime ---")

    role_arn = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{IAM_ROLE_NAME}"
    environment_variables = {
        "GATEWAY_URL": os.environ.get("GATEWAY_URL", ""),
        "TOKEN_URL": os.environ.get("TOKEN_URL", ""),
        "CLIENT_ID": os.environ.get("CLIENT_ID", ""),
        # CLIENT_SECRET should move to Secrets Manager before production —
        # reference via the secret ARN and resolve in the entrypoint.
        "CLIENT_SECRET": os.environ.get("CLIENT_SECRET", ""),
        "SCOPE": os.environ.get("SCOPE", ""),
        "AWS_REGION": AWS_REGION,
        "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    }

    create_kwargs = {
        "agentRuntimeName": RUNTIME_NAME,
        "agentRuntimeArtifact": {
            "containerConfiguration": {"containerUri": ECR_URI},
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "environmentVariables": environment_variables,
        "description": "Reworld outreach email agent — composes personalised waste-management emails.",
    }

    print("create_agent_runtime kwargs:")
    redacted = {**create_kwargs, "environmentVariables": {
        **environment_variables, "CLIENT_SECRET": "<redacted>"
    }}
    print(json.dumps(redacted, indent=2))

    if dry_run:
        print("[dry-run] Would call bedrock-agentcore-control.create_agent_runtime(...)")
        return

    client = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)

    try:
        resp = client.create_agent_runtime(**create_kwargs)
        print("Runtime created:")
        print(json.dumps(resp, indent=2, default=str))
    except client.exceptions.ConflictException:
        print(f"Runtime '{RUNTIME_NAME}' already exists — updating container URI.")
        # update_agent_runtime needs the runtime ID, not name — look it up.
        runtime_id = None
        for page in client.get_paginator("list_agent_runtimes").paginate():
            for rt in page.get("agentRuntimes", []):
                if rt.get("agentRuntimeName") == RUNTIME_NAME:
                    runtime_id = rt["agentRuntimeId"]
                    break
            if runtime_id:
                break
        if not runtime_id:
            raise Exception(f"Runtime '{RUNTIME_NAME}' conflict but not found in list_agent_runtimes")

        update_kwargs = {
            "agentRuntimeId": runtime_id,
            "agentRuntimeArtifact": {
                "containerConfiguration": {"containerUri": ECR_URI},
            },
            "roleArn": role_arn,
            "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": "HTTP"},
            "environmentVariables": environment_variables,
        }
        resp = client.update_agent_runtime(**update_kwargs)
        print("Runtime updated:")
        print(json.dumps(resp, indent=2, default=str))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Reworld Outreach Agent to AgentCore Runtime")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--create-ecr", action="store_true", help="Create ECR repo if missing")
    parser.add_argument("--skip-docker", action="store_true", help="Skip image build/push (re-deploy existing image)")
    args = parser.parse_args()

    if args.create_ecr:
        print(f"\n--- Ensure ECR repository '{ECR_REPO_NAME}' exists ---")
        if not args.dry_run:
            ecr = boto3.client("ecr", region_name=AWS_REGION)
            try:
                ecr.create_repository(repositoryName=ECR_REPO_NAME)
                print(f"Created repository: {ECR_REPO_NAME}")
            except ecr.exceptions.RepositoryAlreadyExistsException:
                print(f"Repository already exists: {ECR_REPO_NAME}")
        else:
            print(f"[dry-run] Would create ECR repo: {ECR_REPO_NAME}")

    if not args.skip_docker:
        build_and_push(args.dry_run)

    create_or_update_runtime(args.dry_run)

    print("\nDone.")
    if args.dry_run:
        print("(dry-run — nothing was actually executed)")


if __name__ == "__main__":
    main()
