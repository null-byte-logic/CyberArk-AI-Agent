"""Create the AgentCore Gateway, its inbound OAuth (Cognito, client credentials),
the Lambda tool targets and the outbound credential provider the agent uses.

Creates:
  * Secrets Manager secret for the approval signing key (random)
  * Lambda functions: pam_tools, logs_query, approvals (+ IAM-auth function URL)
  * Cognito user pool + resource server + M2M app client (Gateway inbound JWT)
  * AgentCore Identity OAuth2 credential provider "gateway-oauth" for the agent
  * AgentCore Gateway (MCP) with targets: pam, logs  (itsm with --with-itsm)

Prerequisite: one Secrets Manager secret per CyberArk environment (see README,
"Create the environment secrets"). Environments whose URL or secret is missing
are skipped, so you can start with pc-preprod alone and re-run later.
"""
from __future__ import annotations

import io
import json
import secrets
import sys
import time
import zipfile

from common import ROOT, ensure_role, load_env, outputs, save_output, session, step

# (gateway target name, lambda folder, schema file). pam and identity share one function.
TARGETS = (
    ("pam", "pam_tools", "tool_schema.json"),
    ("identity", "pam_tools", "identity_tool_schema.json"),
    ("logs", "logs_query", "tool_schema.json"),
)


def build_environments(env: dict, sm) -> tuple[dict, list[str]]:
    """PAM_ENVIRONMENTS for the Lambda, from .env. Skips environments whose URL is still a placeholder."""
    specs = {
        "pc-preprod": {"mode": "privilege-cloud", "production": False,
                       "pvwa_base_url": env.get("PC_PREPROD_PVWA_URL", ""),
                       "identity_token_url": env.get("PC_PREPROD_TOKEN_URL", ""),
                       "secret": env.get("PC_PREPROD_SECRET_NAME", "")},
        "pc-prod": {"mode": "privilege-cloud", "production": True,
                    "pvwa_base_url": env.get("PC_PROD_PVWA_URL", ""),
                    "identity_token_url": env.get("PC_PROD_TOKEN_URL", ""),
                    "secret": env.get("PC_PROD_SECRET_NAME", "")},
        "onprem": {"mode": "selfhosted", "production": True,
                   "pvwa_base_url": env.get("ONPREM_PVWA_URL", ""),
                   "ca_bundle_s3": env.get("ONPREM_CA_BUNDLE_S3", ""),
                   "secret": env.get("ONPREM_SECRET_NAME", "")},
    }
    out, arns = {}, []
    for name, spec in specs.items():
        if not spec["pvwa_base_url"] or "YOUR_" in spec["pvwa_base_url"]:
            print(f"skipping {name}: URL not set in .env")
            continue
        try:
            arn = sm.describe_secret(SecretId=spec.pop("secret"))["ARN"]
        except sm.exceptions.ResourceNotFoundException:
            print(f"skipping {name}: its Secrets Manager secret does not exist yet")
            continue
        out[name] = {**{k: v for k, v in spec.items() if v != ""}, "secret_arn": arn}
        arns.append(arn)
        print(f"environment {name}: {spec['pvwa_base_url']}")
    return out, arns


def zip_lambda(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "gateway" / "lambda" / name / "handler.py", "handler.py")
        z.write(ROOT / "agent" / "safety.py", "safety.py")
    return buf.getvalue()


def ensure_secret(sm, name: str, value: dict) -> str:
    try:
        return sm.describe_secret(SecretId=name)["ARN"]
    except sm.exceptions.ResourceNotFoundException:
        return sm.create_secret(Name=name, SecretString=json.dumps(value))["ARN"]


def ensure_lambda(lam, name: str, role: str, env_vars: dict, vpc: dict | None = None) -> str:
    fn = f"cyberark-engineer-{name.replace('_', '-')}"
    code = zip_lambda(name)
    extra = {"VpcConfig": vpc} if vpc else {}
    try:
        lam.update_function_code(FunctionName=fn, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=fn)
        arn = lam.update_function_configuration(FunctionName=fn, Environment={"Variables": env_vars},
                                                **extra)["FunctionArn"]
    except lam.exceptions.ResourceNotFoundException:
        for _ in range(6):
            try:
                arn = lam.create_function(
                    FunctionName=fn, Runtime="python3.12", Handler="handler.handler", Role=role,
                    Code={"ZipFile": code}, Timeout=60, MemorySize=256, Architectures=["arm64"],
                    Environment={"Variables": env_vars}, **extra)["FunctionArn"]
                break
            except lam.exceptions.InvalidParameterValueException:
                time.sleep(10)  # role not yet assumable
        else:
            raise
    lam.get_waiter("function_active_v2").wait(FunctionName=fn)
    return arn


def ensure_cognito(env: dict) -> dict:
    cog = session().client("cognito-idp")
    region = env["AWS_REGION"]
    name = f"{env['PROJECT']}-gateway"
    pools = [p for p in cog.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == name]
    pool_id = pools[0]["Id"] if pools else cog.create_user_pool(PoolName=name)["UserPool"]["Id"]
    domain = f"{env['PROJECT']}-{env['AWS_ACCOUNT_ID']}"
    try:
        cog.create_user_pool_domain(Domain=domain, UserPoolId=pool_id)
    except cog.exceptions.InvalidParameterException:
        pass
    try:
        cog.create_resource_server(UserPoolId=pool_id, Identifier="cyberark-agent", Name="cyberark-agent",
                                   Scopes=[{"ScopeName": "invoke", "ScopeDescription": "Call gateway tools"}])
    except cog.exceptions.InvalidParameterException:
        pass
    clients = [c for c in cog.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]
               if c["ClientName"] == "agent-m2m"]
    if clients:
        client = cog.describe_user_pool_client(UserPoolId=pool_id, ClientId=clients[0]["ClientId"])["UserPoolClient"]
    else:
        client = cog.create_user_pool_client(
            UserPoolId=pool_id, ClientName="agent-m2m", GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"], AllowedOAuthScopes=["cyberark-agent/invoke"],
            AllowedOAuthFlowsUserPoolClient=True, SupportedIdentityProviders=["COGNITO"])["UserPoolClient"]
    return {
        "pool_id": pool_id, "client_id": client["ClientId"], "client_secret": client["ClientSecret"],
        "discovery_url": f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
    }


def main() -> None:
    env = load_env()
    with_itsm = "--with-itsm" in sys.argv
    sess = session()
    region, account, project = env["AWS_REGION"], env["AWS_ACCOUNT_ID"], env["PROJECT"]
    sm, lam = sess.client("secretsmanager"), sess.client("lambda")
    ctl = sess.client("bedrock-agentcore-control")

    step("Approval signing key")
    approval_arn = ensure_secret(sm, f"{project}/approval-signing-key", {"key": secrets.token_urlsafe(48)})

    step("CyberArk environments")
    environments, env_secret_arns = build_environments(env, sm)
    ca_s3 = env.get("ONPREM_CA_BUNDLE_S3", "")

    step("Lambda execution role")
    lambda_role = ensure_role(f"{project}-tools-lambda", "lambda.amazonaws.com", {
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"},
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
             "Resource": [approval_arn] + env_secret_arns},
            {"Effect": "Allow", "Action": ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces",
                                           "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses",
                                           "ec2:UnassignPrivateIpAddresses"], "Resource": "*"},
        ] + ([{"Effect": "Allow", "Action": ["s3:GetObject"],
               "Resource": "arn:aws:s3:::" + ca_s3.removeprefix("s3://")}] if ca_s3 else []) + [
            {"Effect": "Allow", "Action": ["logs:StartQuery", "logs:GetQueryResults"],
             "Resource": [f"arn:aws:logs:{region}:{account}:log-group:/cyberark/*",
                          f"arn:aws:logs:{region}:{account}:log-group:/cyberark/*:*"]},
            {"Effect": "Allow", "Action": ["logs:GetQueryResults"], "Resource": "*"},
        ]})

    step("Lambda tool functions")
    common_env = {"APPROVAL_KEY_ARN": approval_arn}
    vpc = None
    if env.get("PAM_LAMBDA_SUBNET_IDS"):
        vpc = {"SubnetIds": env["PAM_LAMBDA_SUBNET_IDS"].split(","),
               "SecurityGroupIds": env["PAM_LAMBDA_SECURITY_GROUP_IDS"].split(",")}
    elif "onprem" in environments:
        print("NOTE: no PAM_LAMBDA_SUBNET_IDS set, so onprem calls will fail until the Lambda "
              "runs in subnets that can reach PVWA.")
    arns = {
        "pam_tools": ensure_lambda(lam, "pam_tools", lambda_role, {
            **common_env, "PAM_ENVIRONMENTS": json.dumps(environments),
            "ENABLE_SERVICE_REQUESTS": env.get("ENABLE_SERVICE_REQUESTS", "false")}, vpc=vpc),
        "logs_query": ensure_lambda(lam, "logs_query", lambda_role, {}),
        "approvals": ensure_lambda(lam, "approvals", lambda_role, common_env),
    }
    try:
        url = lam.create_function_url_config(FunctionName=arns["approvals"], AuthType="AWS_IAM")["FunctionUrl"]
    except lam.exceptions.ResourceConflictException:
        url = lam.get_function_url_config(FunctionName=arns["approvals"])["FunctionUrl"]

    step("Gateway inbound auth (Cognito M2M)")
    cog = ensure_cognito(env)

    step("Gateway service role")
    gw_role = ensure_role(f"{project}-gateway", "bedrock-agentcore.amazonaws.com", {
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["lambda:InvokeFunction"],
             "Resource": [arns["pam_tools"], arns["logs_query"]]},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                                           "bedrock-agentcore:GetResourceOauth2Token",
                                           "secretsmanager:GetSecretValue"], "Resource": "*"}]})

    out = outputs()
    if out.get("gateway_id"):
        gw_id, gw_url = out["gateway_id"], out["gateway_mcp_url"]
        print("gateway exists:", gw_id)
    else:
        step("Creating AgentCore Gateway")
        gw = ctl.create_gateway(
            name=f"{project}-gateway", roleArn=gw_role, protocolType="MCP",
            protocolConfiguration={"mcp": {"searchType": "SEMANTIC"}},
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={"customJWTAuthorizer": {
                "discoveryUrl": cog["discovery_url"], "allowedClients": [cog["client_id"]]}},
            description="CyberArk Engineer tools")
        gw_id, gw_url = gw["gatewayId"], gw["gatewayUrl"]
        for _ in range(40):
            if ctl.get_gateway(gatewayIdentifier=gw_id)["status"] == "READY":
                break
            time.sleep(5)

    step("Gateway targets")
    existing = {t["name"] for t in ctl.list_gateway_targets(gatewayIdentifier=gw_id)["items"]}
    for target, fn_name, schema_file in TARGETS:
        if target in existing:
            continue
        schema = json.loads((ROOT / "gateway" / "lambda" / fn_name / schema_file).read_text())
        ctl.create_gateway_target(
            gatewayIdentifier=gw_id, name=target,
            targetConfiguration={"mcp": {"lambda": {"lambdaArn": arns[fn_name],
                                                    "toolSchema": {"inlinePayload": schema}}}},
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}])
        print("target", target)
    if with_itsm and "itsm" not in existing:
        print("ITSM: upload gateway/openapi/itsm-servicenow.yaml to S3 and create an OAuth credential "
              "provider for ServiceNow, then add the target (see docs/deployment.md).")

    step("AgentCore Identity: OAuth2 provider the agent uses to call the Gateway")
    try:
        ctl.create_oauth2_credential_provider(
            name="gateway-oauth", credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={"customOauth2ProviderConfig": {
                "oauthDiscovery": {"discoveryUrl": cog["discovery_url"]},
                "clientId": cog["client_id"], "clientSecret": cog["client_secret"]}})
    except ctl.exceptions.ValidationException as exc:
        if "already exists" not in str(exc):
            raise

    save_output(gateway_id=gw_id, gateway_mcp_url=gw_url, gateway_role=gw_role,
                approvals_url=url, cognito_pool_id=cog["pool_id"],
                lambda_arns=arns, approval_key_arn=approval_arn)
    print("Gateway MCP URL:", gw_url)


def _secret_exists(sm, name: str) -> bool:
    try:
        sm.describe_secret(SecretId=name)
        return True
    except sm.exceptions.ResourceNotFoundException:
        return False


if __name__ == "__main__":
    main()
