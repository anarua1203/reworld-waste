#!/usr/bin/env python3
"""
Create (or refresh) the IAM execution role used by the AgentCore Runtime
container.  Idempotent — safe to re-run; existing role is reused and the
inline policy is overwritten with the latest version.

Why a script and not a heredoc snippet:
    Pasting long heredocs into a terminal frequently breaks on smart-quotes,
    em-dashes, bracketed-paste markers, and CRLF line endings.  Running this
    file directly avoids every shell-paste hazard.

Usage:
    cd reworld-outreach-agent
    python create_runtime_role.py [--region us-east-1] [--role-name ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import dotenv_values

ROLE_NAME_DEFAULT = "reworld-outreach-agent-runtime"
POLICY_NAME = "agent-runtime-permissions"


def load_env() -> None:
    """Load .env into os.environ so AWS_* vars are visible to boto3."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for k, v in dotenv_values(env_path).items():
            if v is not None and k not in os.environ:
                os.environ[k] = v


def resolve_account(region: str) -> str:
    """Pull AWS_ACCOUNT_ID from env, falling back to STS GetCallerIdentity.

    Works whether or not the user put AWS_ACCOUNT_ID in their .env, and
    always points at whichever account the active IAM credentials belong to —
    the right behaviour when porting the deploy to a new AWS account.
    """
    explicit = os.environ.get("AWS_ACCOUNT_ID", "").strip()
    if explicit:
        return explicit
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


def trust_policy() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def permissions_policy(account: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:GetInferenceProfile",
                    "bedrock:GetFoundationModel",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{account}:inference-profile/*",
                    f"arn:aws:bedrock:*:{account}:application-inference-profile/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:reworld/outreach-agent/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:GetAuthorizationToken",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetWorkloadAccessToken*"],
                "Resource": "*",
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--role-name", default=ROLE_NAME_DEFAULT)
    parser.add_argument("--dry-run", action="store_true", help="Print intended actions without applying.")
    args = parser.parse_args()

    load_env()
    account = resolve_account(args.region)
    print(f"target account: {account}")
    print(f"region:         {args.region}")
    print(f"role:           {args.role_name}")

    if args.dry_run:
        print("\n[dry-run] would create role and attach the following policy:")
        print(json.dumps(permissions_policy(account, args.region), indent=2))
        return 0

    iam = boto3.client("iam", region_name=args.region)

    try:
        iam.create_role(
            RoleName=args.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy()),
            Description="AgentCore Runtime execution role for reworld-outreach-agent",
        )
        print("✓ role created")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        print("• role exists — reusing")

    iam.put_role_policy(
        RoleName=args.role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(permissions_policy(account, args.region)),
    )
    print(f"✓ policy '{POLICY_NAME}' attached")

    print("waiting 8s for IAM propagation before AgentCore can assume the role…")
    time.sleep(8)

    print(f"\n✓ role ARN: arn:aws:iam::{account}:role/{args.role_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
