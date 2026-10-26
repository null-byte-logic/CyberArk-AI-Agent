import unittest

from _paths import ROOT, load_module

pf = load_module("preflight", ROOT / "scripts/preflight.py")
sb = load_module("support_bundle", ROOT / "scripts/support_bundle.py")

GOOD = {
    "AWS_REGION": "ap-southeast-2", "AWS_ACCOUNT_ID": "058264514744", "PROJECT": "cyberark-engineer",
    "KB_BUCKET": "b", "MODEL_SUPERVISOR": "apac.model-a", "MODEL_FAST": "apac.model-b", "MODEL_DEEP": "apac.model-c",
    "PC_PREPROD_PVWA_URL": "https://acme-pre.privilegecloud.cyberark.cloud/PasswordVault",
    "PC_PREPROD_TOKEN_URL": "https://abc1234.id.cyberark.cloud/oauth2/platformtoken",
    "PC_PREPROD_SECRET_NAME": "cyberark-engineer/pc-preprod",
    "PC_PROD_PVWA_URL": "https://YOUR_PROD_SUBDOMAIN.privilegecloud.cyberark.cloud/PasswordVault",
    "ONPREM_PVWA_URL": "https://pvwa.YOUR_DOMAIN.internal/PasswordVault",
}


class PreflightTests(unittest.TestCase):
    def statuses(self, env):
        return {r.check: r.status for r in pf.check_env_values(env)}

    def test_good_env(self):
        s = self.statuses(GOOD)
        self.assertEqual(s[".env required values"], "PASS")
        self.assertEqual(pf.configured_environments(GOOD), ["pc-preprod"])
        self.assertNotIn("FAIL", s.values())

    def test_placeholders_fail(self):
        env = {**GOOD, "MODEL_FAST": "REPLACE_with_x"}
        self.assertEqual(self.statuses(env)[".env required values"], "FAIL")

    def test_no_environment_fails(self):
        env = {**GOOD, "PC_PREPROD_PVWA_URL": "https://YOUR_PREPROD_SUBDOMAIN.x/PasswordVault"}
        self.assertEqual(self.statuses(env)["CyberArk environments"], "FAIL")

    def test_bad_url_shape(self):
        env = {**GOOD, "PC_PREPROD_PVWA_URL": "https://acme.privilegecloud.cyberark.cloud/PasswordVault/API/"}
        self.assertEqual(self.statuses(env)["pc-preprod PVWA URL format"], "FAIL")

    def test_onprem_without_subnets_warns(self):
        env = {**GOOD, "ONPREM_PVWA_URL": "https://pvwa.corp.internal/PasswordVault"}
        self.assertEqual(self.statuses(env)["onprem connectivity"], "WARN")

    def test_secret_shape(self):
        self.assertEqual(pf.check_secret_shape("s", '{"client_id":"a","client_secret":"b"}',
                                               ("client_id", "client_secret")).status, "PASS")
        self.assertEqual(pf.check_secret_shape("s", '{"client_id":"a"}', ("client_id", "client_secret")).status, "FAIL")
        self.assertEqual(pf.check_secret_shape("s", "plain", ("username",)).status, "FAIL")

    def test_secret_values_never_in_result(self):
        r = pf.check_secret_shape("s", '{"client_id":"svc","client_secret":"S3cr3t!"}', ("client_id", "client_secret"))
        self.assertNotIn("S3cr3t!", r.line())

    def test_model_error_hints(self):
        self.assertIn("inference-profile", pf.explain_model_error("ValidationException", "on-demand throughput isn't supported"))
        self.assertIn("Model access", pf.explain_model_error("AccessDeniedException", ""))


class SupportBundleTests(unittest.TestCase):
    def test_env_values_hidden(self):
        rows = "\n".join(sb.describe_env({"MODEL_FAST": "apac.x", "PC_PROD_TOKEN_URL": "https://t.id.cyberark.cloud/x",
                                          "ONPREM_SECRET_NAME": "", "PC_PROD_PVWA_URL": "https://YOUR_x"}))
        self.assertIn("| MODEL_FAST | apac.x |", rows)
        self.assertIn("| PC_PROD_TOKEN_URL | (set) |", rows)
        self.assertIn("| ONPREM_SECRET_NAME | (empty) |", rows)
        self.assertIn("| PC_PROD_PVWA_URL | (placeholder) |", rows)
        self.assertNotIn("t.id.cyberark.cloud", rows)

    def test_build_masks_everything(self):
        env = {**GOOD}
        resources = {"gateway_role": "arn:aws:iam::058264514744:role/x"}
        errors = ['### /aws/lambda/x\n```\nlogon failed {"password": "Hunter22!"} Bearer abcdefghijklmnopqrstuvwxyz0123\n'
                  'calling https://acme-prod.privilegecloud.cyberark.cloud/PasswordVault/API and '
                  'https://zz9999.id.cyberark.cloud/oauth2/platformtoken\n```']
        out = sb.build(env, resources, ["[PASS] x"], "deploy ok 058264514744", errors, "v")
        for leaked in ("Hunter22!", "abcdefghijklmnopqrstuvwxyz0123", "058264514744", "acme-prod", "zz9999", "acme-pre"):
            self.assertNotIn(leaked, out)
        self.assertIn("********4744", out)
        self.assertIn("<pc-tenant>.privilegecloud.cyberark.cloud", out)


class AwsOnlyTests(unittest.TestCase):
    def test_no_third_party_ai_references(self):
        banned = ("openai", "chatgpt", "azure", "gemini", "anthropic.com/api")
        for p in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.txt")):
            if ".venv" in p.parts or ".git" in p.parts or p.name == "test_preflight_and_bundle.py":
                continue
            text = p.read_text(errors="ignore").lower()
            for b in banned:
                self.assertNotIn(b, text, f"{b} in {p.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
