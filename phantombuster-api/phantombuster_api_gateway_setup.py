#!/usr/bin/env python3
"""
phantombuster-api — AgentCore OpenAPI Gateway setup.

Creates the AWS plumbing (IAM, Cognito, Gateway, S3-hosted OpenAPI, credential
provider, OpenAPI target) so an agent can call PhantomBuster as MCP tools.

PhantomBuster authenticates via the X-Phantombuster-Key request header.
The credential provider is registered as HEADER so AgentCore injects the key
into every upstream call automatically.

Prerequisites:
- AWS credentials configured (IAM user — AgentCore requires IAM, not SSO)
- .env populated from .env.example (set PHANTOMBUSTER_API_KEY to your real key)
- Python 3.10+  |  pip install boto3 python-dotenv requests
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

try:
    from dotenv import load_dotenv
except ImportError:
    print("Missing dependency: python-dotenv")
    print("Install it with: pip install python-dotenv")
    sys.exit(1)

# Make local utils.py importable regardless of cwd
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Load project config (.env next to this script), then load any separate secret
# file if ENV_FILE_PATH points elsewhere. Values already in the environment win.
load_dotenv(os.path.join(current_dir, ".env"))
_env_file_path = os.environ.get("ENV_FILE_PATH", "").strip()
if _env_file_path and os.path.abspath(_env_file_path) != os.path.abspath(
    os.path.join(current_dir, ".env")
):
    if not os.path.isfile(_env_file_path):
        print(f"ENV_FILE_PATH points to a missing file: {_env_file_path}")
        print("Create it (chmod 600) and add the upstream API key line, e.g.:")
        print("  PHANTOMBUSTER_API_KEY=<your-key>")
        sys.exit(1)
    load_dotenv(_env_file_path, override=False)

import utils  # type: ignore[import-not-found]  # noqa: E402  (resolved via sys.path insert above)

os.environ["AWS_DEFAULT_REGION"] = os.environ.get("AWS_REGION", "us-east-1")
REGION = os.environ["AWS_DEFAULT_REGION"]
print(f"Using AWS Region: {REGION}\n")

USER_POOL_NAME = os.environ["USER_POOL_NAME"]
RESOURCE_SERVER_ID = os.environ["RESOURCE_SERVER_ID"]
RESOURCE_SERVER_NAME = os.environ["RESOURCE_SERVER_NAME"]
CLIENT_NAME = os.environ["CLIENT_NAME"]
SCOPES = [
    {"ScopeName": "gateway:read", "ScopeDescription": "Read access to gateway"},
    {"ScopeName": "gateway:write", "ScopeDescription": "Write access to gateway"},
]
scope_string = f"{RESOURCE_SERVER_ID}/gateway:read {RESOURCE_SERVER_ID}/gateway:write"

SETUP_STATE_FILE = Path(__file__).parent / ".setup-state.json"

# Fields whose values are baked into named AWS resources on first run.
# If .env changes any of these after a successful run, we bail early to avoid
# silent stale-config reuse.
TRACKED_FIELDS = [
    "USER_POOL_NAME",
    "RESOURCE_SERVER_ID",
    "RESOURCE_SERVER_NAME",
    "CLIENT_NAME",
    "GATEWAY_NAME",
    "IAM_ROLE_NAME",
    "TARGET_NAME",
    "CREDENTIAL_PROVIDER_NAME",
    "OPENAPI_SPEC_FILE",
    "CREDENTIAL_PARAMETER_NAME",
    "CREDENTIAL_LOCATION",
    "API_KEY_ENV_VAR",
    "AWS_REGION",
]


def check_config_drift():
    """Bail if tracked .env values differ from the last successful run."""
    if not SETUP_STATE_FILE.exists():
        return
    saved = json.loads(SETUP_STATE_FILE.read_text())
    current = {f: os.environ.get(f, "") for f in TRACKED_FIELDS}
    drift = {
        f: (saved.get(f), current[f])
        for f in TRACKED_FIELDS
        if saved.get(f) != current[f]
    }
    if drift:
        print("Config drift detected vs previous setup run:")
        for field, (old, new) in drift.items():
            print(f"  {field}: {old!r} -> {new!r}")
        print()
        print("The previous run created AWS resources with the old values.")
        print("Reuse-by-name would silently keep stale config. To proceed:")
        print("  1. Manually delete stale resources (see README cleanup section).")
        print(f"  2. Delete {SETUP_STATE_FILE.name}")
        print("  3. Re-run this script.")
        sys.exit(1)


def save_setup_state():
    snapshot = {f: os.environ.get(f, "") for f in TRACKED_FIELDS}
    SETUP_STATE_FILE.write_text(json.dumps(snapshot, indent=2) + "\n")


def verify_iam_credentials():
    """Fail fast if AWS creds are missing or aren't an IAM user.

    AgentCore rejects SSO / federated / assumed-role credentials. The caller's
    STS ARN must look like `arn:aws:iam::<account>:user/<name>`.
    """
    if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get(
        "AWS_SECRET_ACCESS_KEY"
    ):
        print(
            "AWS credentials not found in environment.\n"
            "Add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to .env "
            "(see .env.example) and re-run."
        )
        sys.exit(1)
    try:
        identity = boto3.client("sts").get_caller_identity()
    except ClientError as e:
        print(f"STS GetCallerIdentity failed: {e}")
        sys.exit(1)
    arn = identity["Arn"]
    if ":user/" not in arn:
        print(
            f"Caller ARN '{arn}' is not an IAM user.\n"
            "AgentCore requires long-lived IAM user credentials, not SSO / "
            "assumed-role / federated sessions. Generate an access key for an "
            "IAM user and put it in .env."
        )
        sys.exit(1)
    print(f"AWS identity: {arn}")
    print(f"AWS account:  {identity['Account']}\n")


# ---------------------------------------------------------------------------
# Step 1 — IAM Role
# ---------------------------------------------------------------------------

def create_iam_role():
    print("=" * 80)
    print("Step 1: Creating IAM Role for Gateway")
    print("=" * 80)
    role = utils.create_agentcore_gateway_role(os.environ["IAM_ROLE_NAME"])
    role_arn = role["Role"]["Arn"]
    print(f"Gateway IAM Role ARN: {role_arn}\n")
    return role_arn


# ---------------------------------------------------------------------------
# Step 2 — Cognito
# ---------------------------------------------------------------------------

def create_cognito_resources():
    print("=" * 80)
    print("Step 2: Creating Cognito Pool for Inbound Authorization")
    print("=" * 80)
    cognito = boto3.client("cognito-idp", region_name=REGION)

    user_pool_id = utils.get_or_create_user_pool(cognito, USER_POOL_NAME)
    print(f"User Pool ID: {user_pool_id}")

    utils.get_or_create_resource_server(
        cognito, user_pool_id, RESOURCE_SERVER_ID, RESOURCE_SERVER_NAME, SCOPES
    )
    print("Resource server ensured.")

    client_id, client_secret = utils.get_or_create_m2m_client(
        cognito, user_pool_id, CLIENT_NAME, RESOURCE_SERVER_ID
    )
    print(f"Client ID: {client_id}")

    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/"
        f"{user_pool_id}/.well-known/openid-configuration"
    )
    print(f"Discovery URL: {discovery_url}\n")
    return user_pool_id, client_id, client_secret, discovery_url


# ---------------------------------------------------------------------------
# Step 3 — Gateway
# ---------------------------------------------------------------------------

def create_gateway(role_arn, client_id, discovery_url):
    print("=" * 80)
    print("Step 3: Creating AgentCore Gateway")
    print("=" * 80)
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    auth_config = {
        "customJWTAuthorizer": {
            "allowedClients": [client_id],
            "discoveryUrl": discovery_url,
        }
    }
    gateway_name = os.environ["GATEWAY_NAME"]

    try:
        resp = gateway_client.create_gateway(
            name=gateway_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            description="AgentCore Gateway for phantombuster-poc — OpenAPI-derived MCP tools",
        )
        gateway_id = resp["gatewayId"]
        gateway_url = resp["gatewayUrl"]
        print(f"Created gateway: {gateway_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
        print(f"Gateway '{gateway_name}' already exists — reusing")
        existing = None
        for page in gateway_client.get_paginator("list_gateways").paginate():
            for gw in page.get("items", []):
                if gw["name"] == gateway_name:
                    existing = gw
                    break
            if existing:
                break
        if not existing:
            raise Exception("Gateway ConflictException but not found in list_gateways")
        gateway_id = existing["gatewayId"]
        gateway_url = gateway_client.get_gateway(
            gatewayIdentifier=gateway_id
        ).get("gatewayUrl")

    print(f"Gateway URL: {gateway_url}\n")
    wait_for_gateway_ready(gateway_client, gateway_id)
    return gateway_client, gateway_id, gateway_url


def wait_for_gateway_ready(gateway_client, gateway_id, max_wait=180, poll_every=5):
    """Block until the gateway leaves CREATING. CreateGatewayTarget rejects
    calls with ValidationException while the gateway is still being provisioned.
    """
    waited = 0
    while waited < max_wait:
        status = gateway_client.get_gateway(gatewayIdentifier=gateway_id).get("status")
        if status == "READY":
            print(f"Gateway is READY (after {waited}s)\n")
            return
        if status in ("FAILED", "DELETING", "DELETED"):
            raise Exception(f"Gateway entered terminal status {status!r}")
        print(f"  waiting for gateway… status={status} ({waited}s elapsed)")
        time.sleep(poll_every)
        waited += poll_every
    raise Exception(f"gateway did not reach READY in {max_wait}s")


# ---------------------------------------------------------------------------
# Step 4 — S3 spec upload
# ---------------------------------------------------------------------------

def upload_openapi_spec():
    print("=" * 80)
    print("Step 4: Uploading OpenAPI Spec to S3")
    print("=" * 80)
    session = boto3.Session()
    s3 = session.client("s3")
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    region = session.region_name or REGION
    bucket = f"agentcore-gateway-{account_id}-{region}"
    spec_file = os.environ["OPENAPI_SPEC_FILE"]
    spec_path = os.path.join(current_dir, "openapi-specs", spec_file)

    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"Created bucket: {bucket}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"Bucket already exists: {bucket}")

    with open(spec_path) as f:
        spec = json.load(f)
    s3.put_object(Bucket=bucket, Key=spec_file, Body=json.dumps(spec, indent=2))
    uri = f"s3://{bucket}/{spec_file}"
    print(f"Uploaded: {uri}\n")
    return uri


# ---------------------------------------------------------------------------
# Step 5 — Credential provider
# PhantomBuster authenticates via X-Phantombuster-Key request header.
# The credential provider is registered as HEADER so AgentCore injects it
# automatically into every upstream call — the key never travels through
# the agent's tool-call payload.
# ---------------------------------------------------------------------------

def create_credential_provider():
    print("=" * 80)
    print("Step 5: Creating Credential Provider (X-Phantombuster-Key header)")
    print("=" * 80)
    api_key_env_var = os.environ.get("API_KEY_ENV_VAR", "PHANTOMBUSTER_API_KEY")
    api_key = os.environ.get(api_key_env_var)
    if not api_key or api_key.startswith("<"):
        print(
            f"Missing API key: env var '{api_key_env_var}' is not set or still a placeholder.\n"
            f"Set it in .env as:\n"
            f"  {api_key_env_var}=<your-real-phantombuster-api-key>\n"
            f"Obtain your key from: https://phantombuster.com/<your-workspace-id>/workspace-settings#api-keys\n"
            f"Then re-run this script."
        )
        sys.exit(1)
    arn = utils.create_api_key_credential_provider(
        name=os.environ["CREDENTIAL_PROVIDER_NAME"],
        api_key=api_key,
        region=REGION,
    )
    print(f"Credential Provider ARN: {arn}\n")
    return arn


# ---------------------------------------------------------------------------
# Step 6 — Gateway target
# ---------------------------------------------------------------------------

def create_gateway_target(gateway_client, gateway_id, spec_s3_uri, cred_provider_arn):
    print("=" * 80)
    print("Step 6: Creating OpenAPI Gateway Target")
    print("=" * 80)
    target_config = {"mcp": {"openApiSchema": {"s3": {"uri": spec_s3_uri}}}}
    # CREDENTIAL_LOCATION=HEADER tells AgentCore to inject the key as a request
    # header named CREDENTIAL_PARAMETER_NAME (X-Phantombuster-Key) on every call.
    credential_config = [
        {
            "credentialProviderType": "API_KEY",
            "credentialProvider": {
                "apiKeyCredentialProvider": {
                    "credentialParameterName": os.environ["CREDENTIAL_PARAMETER_NAME"],
                    "providerArn": cred_provider_arn,
                    "credentialLocation": os.environ["CREDENTIAL_LOCATION"],
                }
            },
        }
    ]
    target_name = os.environ["TARGET_NAME"]
    try:
        resp = gateway_client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            description="OpenAPI Target for phantombuster-poc (PhantomBuster REST API v2)",
            targetConfiguration=target_config,
            credentialProviderConfigurations=credential_config,
        )
        print(f"Created target: {target_name} ({resp['targetId']})\n")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
        print(f"Target '{target_name}' already exists — reusing")
        for page in gateway_client.get_paginator("list_gateway_targets").paginate(
            gatewayIdentifier=gateway_id
        ):
            for t in page.get("items", []):
                if t["name"] == target_name:
                    print(f"  Target ID: {t['targetId']}\n")
                    return target_name
        raise Exception(f"Target '{target_name}' ConflictException but not found in list")
    return target_name


# ---------------------------------------------------------------------------
# Step 7 — Cognito token verification
# ---------------------------------------------------------------------------

def get_cognito_token(user_pool_id, client_id, client_secret):
    print("=" * 80)
    print("Step 7: Verifying Cognito Token Retrieval")
    print("=" * 80)
    print("(First attempt may fail until Cognito domain propagates — retrying up to 5x)\n")
    transient_errors = (
        requests.ConnectionError,
        requests.Timeout,
        requests.HTTPError,
    )
    for attempt in range(5):
        try:
            token = utils.get_token(
                user_pool_id, client_id, client_secret, scope_string, REGION
            )["access_token"]
            print(f"Token retrieved (attempt {attempt + 1}): {token[:50]}...\n")
            return token
        except transient_errors:
            if attempt < 4:
                print(f"  Attempt {attempt + 1} failed — retrying in 10s...")
                time.sleep(10)
            else:
                raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\n" + "=" * 80)
    print("phantombuster-api Gateway Setup")
    print("=" * 80 + "\n")
    check_config_drift()
    verify_iam_credentials()
    try:
        role_arn = create_iam_role()
        user_pool_id, client_id, client_secret, discovery_url = create_cognito_resources()
        gateway_client, gateway_id, gateway_url = create_gateway(
            role_arn, client_id, discovery_url
        )
        spec_uri = upload_openapi_spec()
        cred_arn = create_credential_provider()
        target_name = create_gateway_target(
            gateway_client, gateway_id, spec_uri, cred_arn
        )
        get_cognito_token(user_pool_id, client_id, client_secret)
        save_setup_state()

        print("\n" + "=" * 80)
        print("Setup Complete")
        print("=" * 80)
        print(f"Gateway URL:  {gateway_url}")
        print(f"Gateway ID:   {gateway_id}")
        print(f"Target Name:  {target_name}")
        print(f"S3 Spec URI:  {spec_uri}")
        print()
        print("Run  python get_credentials.py  to retrieve Cognito client credentials.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
