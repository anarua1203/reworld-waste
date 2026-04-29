"""
AgentCore OpenAPI Gateway — shared AWS helpers.

Idempotent creators for Cognito user pool / resource server / M2M client,
AgentCore gateway IAM role, API-key credential provider, and the Cognito
token fetch used to verify the pool end-to-end.
"""

import json
import time

import boto3
import requests
from boto3.session import Session
from botocore.exceptions import ClientError


def get_or_create_user_pool(cognito, user_pool_name):
    """Return an existing Cognito pool id by name, or create one (with a hosted UI domain)."""
    response = cognito.list_user_pools(MaxResults=60)
    for pool in response["UserPools"]:
        if pool["Name"] == user_pool_name:
            return pool["Id"]

    created = cognito.create_user_pool(PoolName=user_pool_name)
    user_pool_id = created["UserPool"]["Id"]
    # Hosted UI domain is required for the /oauth2/token endpoint.
    # Domain name must be lowercase alphanumeric; strip underscores from pool id.
    domain = user_pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
    return user_pool_id


def get_or_create_resource_server(
    cognito, user_pool_id, resource_server_id, resource_server_name, scopes
):
    """Ensure the resource server exists; return its identifier."""
    try:
        cognito.describe_resource_server(
            UserPoolId=user_pool_id, Identifier=resource_server_id
        )
    except cognito.exceptions.ResourceNotFoundException:
        cognito.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=resource_server_id,
            Name=resource_server_name,
            Scopes=scopes,
        )
    return resource_server_id


def get_or_create_m2m_client(
    cognito, user_pool_id, client_name, resource_server_id, scopes=None
):
    """Return (client_id, client_secret) for a client_credentials M2M app client."""
    for client in cognito.list_user_pool_clients(
        UserPoolId=user_pool_id, MaxResults=60
    )["UserPoolClients"]:
        if client["ClientName"] == client_name:
            describe = cognito.describe_user_pool_client(
                UserPoolId=user_pool_id, ClientId=client["ClientId"]
            )
            return client["ClientId"], describe["UserPoolClient"]["ClientSecret"]

    if scopes is None:
        scopes = [
            f"{resource_server_id}/gateway:read",
            f"{resource_server_id}/gateway:write",
        ]

    created = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=scopes,
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
    )
    return (
        created["UserPoolClient"]["ClientId"],
        created["UserPoolClient"]["ClientSecret"],
    )


def get_token(user_pool_id, client_id, client_secret, scope_string, region):
    """Fetch an M2M access token from the Cognito /oauth2/token endpoint."""
    url = (
        f"https://{user_pool_id.replace('_', '')}"
        f".auth.{region}.amazoncognito.com/oauth2/token"
    )
    response = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope_string,
        },
    )
    response.raise_for_status()
    return response.json()


def create_api_key_credential_provider(name, api_key, region=None):
    """Create (or reuse) an API-key credential provider and return its ARN."""
    if region is None:
        region = Session().region_name
    acps = boto3.client("bedrock-agentcore-control", region_name=region)

    try:
        return acps.create_api_key_credential_provider(
            name=name, apiKey=api_key
        )["credentialProviderArn"]
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("ResourceInUseException", "ValidationException"):
            raise
        for provider in acps.list_api_key_credential_providers(maxResults=100).get(
            "credentialProviders", []
        ):
            if provider["name"] == name:
                return provider["credentialProviderArn"]
        raise Exception(
            f"Credential provider '{name}' exists but was not found in list_api_key_credential_providers"
        )


def create_agentcore_gateway_role(gateway_name):
    """Create (or recreate) the IAM execution role assumed by the AgentCore gateway."""
    iam = boto3.client("iam")
    role_name = f"agentcore-{gateway_name}-role"
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    region = Session().region_name or "us-east-1"

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                ],
                "Resource": "*",
            }
        ],
    }
    # Trust policy with confused-deputy mitigation (aws:SourceAccount + aws:SourceArn)
    assume_role = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role),
        )
        time.sleep(10)  # allow IAM propagation before the role is assumed
    except iam.exceptions.EntityAlreadyExistsException:
        # Delete inline policies then the role, then recreate cleanly
        for p in iam.list_role_policies(RoleName=role_name, MaxItems=100)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role),
        )
        time.sleep(10)

    iam.put_role_policy(
        PolicyDocument=json.dumps(role_policy),
        PolicyName="AgentCorePolicy",
        RoleName=role_name,
    )
    return role
