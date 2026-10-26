import json
import unittest
from types import SimpleNamespace

from _paths import ROOT, load_module
from agent.safety import mint_approval

KEY = "k" * 32


def ctx(tool: str, target: str = "pam"):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"{target}___{tool}"}))


ENVS = {"pc-preprod": {"mode": "privilege-cloud", "production": False, "pvwa_base_url": "https://pre/PasswordVault",
                       "identity_token_url": "https://pre.id.cyberark.cloud/oauth2/platformtoken", "secret_arn": "a"},
        "pc-prod": {"mode": "privilege-cloud", "production": True, "pvwa_base_url": "https://prod/PasswordVault",
                    "identity_token_url": "https://prod.id.cyberark.cloud/oauth2/platformtoken", "secret_arn": "b"},
        "onprem": {"mode": "selfhosted", "production": True, "pvwa_base_url": "https://pvwa/PasswordVault",
                   "secret_arn": "c"}}


class PamLambdaTests(unittest.TestCase):
    def setUp(self, enable_sr="false"):
        import os
        os.environ["PAM_ENVIRONMENTS"] = json.dumps(ENVS)
        os.environ["ENABLE_SERVICE_REQUESTS"] = enable_sr
        self.h = load_module("pam_handler", ROOT / "gateway/lambda/pam_tools/handler.py")
        self.calls = []
        self.h._secret = lambda arn: {"key": KEY}
        self.h._api = lambda env, m, p, b=None: (self.calls.append((env, m, p, b)) or
                                                 {"status": 200, "data": {"id": "12_34", "password": "leak"}})
        self.h._identity = lambda env, p, b=None: (self.calls.append((env, "POST", p, b)) or {"status": 200})
        for fn, _, _ in self.h.TOOLS.values():
            fn.__globals__["_api"] = self.h._api
            fn.__globals__["_identity"] = self.h._identity

    def tok(self, tool, args):
        return mint_approval(KEY.encode(), tool, args, "C1", "jane")

    def test_unknown_tool(self):
        self.assertIn("not available", self.h.handler({"environment": "pc-prod"}, ctx("retrieve_password"))["error"])

    def test_environment_required(self):
        out = self.h.handler({"account_id": "12_34"}, ctx("get_account"))
        self.assertIn("environment is required", out["error"])
        out = self.h.handler({"account_id": "1", "environment": "dr"}, ctx("get_account"))
        self.assertIn("environment is required", out["error"])
        self.assertEqual(self.calls, [])

    def test_read_is_redacted_and_routed(self):
        out = self.h.handler({"environment": "onprem", "account_id": "12_34"}, ctx("get_account"))
        self.assertEqual(out["data"]["password"], "[REDACTED]")
        self.assertEqual(self.calls[0][:3], ("onprem", "GET", "/Accounts/12_34"))

    def test_path_injection_is_encoded(self):
        self.h.handler({"environment": "pc-preprod", "account_id": "../Users"}, ctx("get_account"))
        self.assertEqual(self.calls[0][2], "/Accounts/..%2FUsers")

    def test_preprod_l1_write_without_approval(self):
        out = self.h.handler({"environment": "pc-preprod", "account_id": "12_34"}, ctx("verify_account"))
        self.assertEqual(out["status"], 200)

    def test_prod_l1_write_needs_approval(self):
        for env in ("pc-prod", "onprem"):
            out = self.h.handler({"environment": env, "account_id": "12_34"}, ctx("verify_account"))
            self.assertEqual(out["error"], "APPROVAL_REQUIRED", env)
        self.assertEqual(self.calls, [])
        args = {"environment": "pc-prod", "account_id": "12_34"}
        out = self.h.handler({**args, "approval_token": self.tok("pam___verify_account", args)}, ctx("verify_account"))
        self.assertEqual(out["status"], 200)

    def test_approval_bound_to_environment(self):
        tok = self.tok("pam___reconcile_account", {"environment": "pc-preprod", "account_id": "12_34"})
        out = self.h.handler({"environment": "pc-prod", "account_id": "12_34", "approval_token": tok},
                             ctx("reconcile_account"))
        self.assertTrue(out["error"].startswith("APPROVAL_INVALID"))
        self.assertEqual(self.calls, [])

    def test_write_without_approval(self):
        out = self.h.handler({"environment": "pc-preprod", "account_id": "12_34"}, ctx("reconcile_account"))
        self.assertEqual(out["error"], "APPROVAL_REQUIRED")

    def test_write_with_good_approval(self):
        args = {"environment": "onprem", "account_id": "12_34"}
        out = self.h.handler({**args, "approval_token": self.tok("pam___reconcile_account", args)},
                             ctx("reconcile_account"))
        self.assertEqual(out["status"], 200)
        self.assertEqual(self.calls[0][:3], ("onprem", "POST", "/Accounts/12_34/Reconcile"))

    def test_service_requests_disabled(self):
        args = {"environment": "pc-preprod", "safe_name": "APP-X", "managing_cpm": "PasswordManager"}
        out = self.h.handler({**args, "approval_token": self.tok("pam___add_safe", args)}, ctx("add_safe"))
        self.assertIn("troubleshooting only", out["error"])
        self.assertEqual(self.calls, [])

    def test_identity_target_dispatch(self):
        out = self.h.handler({"environment": "pc-preprod", "username": "a.patel"}, ctx("get_user", "identity"))
        self.assertEqual(out["status"], 200)
        self.assertEqual(self.calls[0][2], "/CDirectoryService/GetUserByName")

    def test_identity_unlock_prod_needs_approval(self):
        out = self.h.handler({"environment": "pc-prod", "user_uuid": "u1"}, ctx("unlock_user", "identity"))
        self.assertEqual(out["error"], "APPROVAL_REQUIRED")


class PamLambdaServiceRequestTests(PamLambdaTests):
    def setUp(self):
        super().setUp(enable_sr="true")

    def test_tier0_blocked(self):
        args = {"environment": "pc-preprod", "safe_name": "T0-DOMAIN", "member_name": "x",
                "permission_profile": "EndUser"}
        out = self.h.handler({**args, "approval_token": self.tok("pam___add_safe_member", args)},
                             ctx("add_safe_member"))
        self.assertIn("Tier-0", out["error"])
        self.assertEqual(self.calls, [])

    def test_unknown_permission_profile(self):
        args = {"environment": "pc-preprod", "safe_name": "APP-X", "member_name": "G", "permission_profile": "FullAdmin"}
        out = self.h.handler({**args, "approval_token": self.tok("pam___add_safe_member", args)}, ctx("add_safe_member"))
        self.assertEqual(out["status"], 400)

    def test_add_account_sends_no_secret(self):
        args = {"environment": "pc-preprod", "safe_name": "LNX", "platform_id": "UnixSSH",
                "address": "h", "user_name": "root"}
        self.h.handler({**args, "approval_token": self.tok("pam___add_account", args)}, ctx("add_account"))
        body = self.calls[0][3]
        self.assertNotIn("secret", body)
        self.assertTrue(body["secretManagement"]["automaticManagementEnabled"])

    def test_service_requests_disabled(self):
        pass  # enabled in this class


class LogsLambdaTests(unittest.TestCase):
    def setUp(self):
        self.h = load_module("logs_handler", ROOT / "gateway/lambda/logs_query/handler.py")

    def test_bad_component(self):
        self.assertIn("component", self.h.handler({"environment": "onprem", "component": "etc"}, None)["error"])

    def test_environment_required(self):
        self.assertIn("environment", self.h.handler({"component": "padr"}, None)["error"])

    def test_injection_rejected(self):
        out = self.h.handler({"environment": "pc-prod", "component": "cpm", "contains": "x/ | stats count(*)"}, None)
        self.assertIn("error", out)

    def test_query_escapes(self):
        q = self.h.build_query("CACPM410E", "cpm01.corp", 50)
        self.assertIn("CACPM410E", q)
        self.assertIn("cpm01\\.corp", q)
        self.assertTrue(q.endswith("limit 50"))


class ApprovalsLambdaTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ["APPROVAL_KEY_ARN"] = "arn:test"
        self.h = load_module("approvals_handler", ROOT / "gateway/lambda/approvals/handler.py")
        self.h._signing_key = lambda: KEY.encode()

    def test_rejects_unapprovable_tool(self):
        out = self.h.handler({"tool": "pam___activate_user", "ticket": "T", "approver": "a", "args": {"x": 1}}, None)
        self.assertEqual(out["statusCode"], 400)

    def test_mints(self):
        out = self.h.handler({"body": json.dumps({"tool": "pam___reconcile_account", "ticket": "C1",
                                                   "approver": "jane", "args": {"account_id": "1"}})}, None)
        self.assertEqual(out["statusCode"], 200)
        self.assertIn("approval_token", json.loads(out["body"]))


if __name__ == "__main__":
    unittest.main()
