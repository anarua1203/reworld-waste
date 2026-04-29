#!/usr/bin/env python3
"""
Retrieve Cognito client credentials + gateway URL for phantombuster-api.

Run after phantombuster_api_gateway_setup.py to get the values your MCP client needs.
CLIENT_SECRET is written to ./credentials.env (chmod 600) to avoid leaking
into shell history, CI logs, or screen-shares. Do not commit credentials.env.
"""

import os
import sys
from pathlib import Path

import boto3

try:
    from dotenv import load_dotenv
except ImportError:
    print("Missing dependency: python-dotenv")
    print("Install it with: pip install python-dotenv")
    sys.exit(1)

load_dotenv()

USER_POOL_NAME = os.environ["USER_POOL_NAME"]
CLIENT_NAME = os.environ["CLIENT_NAME"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
REGION = os.environ.get("AWS_REGION", "us-east-1")


def main():
    print("=" * 80)
    print("Retrieving credentials for phantombuster-api")
    print("=" * 80 + "\n")

    cognito = boto3.client("cognito-idp", region_name=REGION)

    # Locate user pool by name
    user_pool_id = None
    for pool in cognito.list_user_pools(MaxResults=60)["UserPools"]:
        if pool["Name"] == USER_POOL_NAME:
            user_pool_id = pool["Id"]
            break
    if not user_pool_id:
        print(
            f"User pool '{USER_POOL_NAME}' not found — "
            "run phantombuster_api_gateway_setup.py first."
        )
        sys.exit(1)

    # Locate app client by name
    client_id = None
    for client in cognito.list_user_pool_clients(
        UserPoolId=user_pool_id, MaxResults=60
    )["UserPoolClients"]:
        if client["ClientName"] == CLIENT_NAME:
            client_id = client["ClientId"]
            break
    if not client_id:
        print(f"App client '{CLIENT_NAME}' not found in pool '{USER_POOL_NAME}'.")
        sys.exit(1)

    describe = cognito.describe_user_pool_client(
        UserPoolId=user_pool_id, ClientId=client_id
    )
    client_secret = describe["UserPoolClient"]["ClientSecret"]

    token_url = (
        f"https://{user_pool_id.replace('_', '').lower()}"
        f".auth.{REGION}.amazoncognito.com/oauth2/token"
    )

    # Locate gateway by name
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gateway_id = None
    for page in gateway_client.get_paginator("list_gateways").paginate():
        for gw in page.get("items", []):
            if gw["name"] == GATEWAY_NAME:
                gateway_id = gw["gatewayId"]
                break
        if gateway_id:
            break

    gateway_url = "GATEWAY_NOT_FOUND"
    if gateway_id:
        gateway_url = gateway_client.get_gateway(
            gatewayIdentifier=gateway_id
        ).get("gatewayUrl", "GATEWAY_URL_UNAVAILABLE")

    # Write sensitive credentials to a chmod-600 file; non-secrets go to stdout
    creds_path = Path(__file__).parent / "credentials.env"
    creds_path.write_text(
        f'GATEWAY_URL="{gateway_url}"\n'
        f'CLIENT_ID="{client_id}"\n'
        f'CLIENT_SECRET="{client_secret}"\n'
        f'TOKEN_URL="{token_url}"\n'
    )
    creds_path.chmod(0o600)

    print("=" * 80)
    print("CREDENTIALS — copy non-secret values into your MCP client config")
    print("=" * 80 + "\n")
    print(f'GATEWAY_URL   = "{gateway_url}"')
    print(f'CLIENT_ID     = "{client_id}"')
    print(f'TOKEN_URL     = "{token_url}"')
    print()
    print(f"CLIENT_SECRET written to {creds_path.resolve()} (chmod 600)")
    print("  SENSITIVE — do not commit, do not paste into shared logs.")
    print()


if __name__ == "__main__":
    main()
