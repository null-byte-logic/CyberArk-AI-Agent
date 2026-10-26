import json
import re
import unittest

from _paths import ROOT
from agent import catalogue as cat
from agent.catalogue import Tier
from agent.guard import SessionState, check_tool_call, record


class CatalogueTests(unittest.TestCase):
    def test_knowledge_advisor_has_no_gateway_tools(self):
        self.assertEqual(cat.tools_for(cat.KNOWLEDGE), [])

    def test_diagnostics_is_read_only(self):
        self.assertTrue(all(t.access is cat.Access.READ for t in cat.tools_for(cat.DIAGNOSTICS)))

    def test_pam_writes_only_for_remediation_or_fulfiller(self):
        for t in cat.CATALOGUE:
            if t.access is cat.Access.WRITE and t.name.startswith("pam___"):
                self.assertTrue(set(t.owners) <= {cat.REMEDIATION, cat.FULFILLER}, t.name)

    def test_tier_rules(self):
        self.assertTrue(cat.allowed_for_tier("pam___activate_user", Tier.L1))
        self.assertFalse(cat.allowed_for_tier("pam___activate_user", Tier.L3))
        self.assertTrue(cat.allowed_for_tier("pam___reconcile_account", Tier.L2))
        self.assertFalse(cat.allowed_for_tier("pam___reconcile_account", Tier.L3))
        self.assertTrue(cat.allowed_for_tier("pam___add_safe", Tier.SR))
        self.assertFalse(cat.allowed_for_tier("pam___add_safe", Tier.L2))
        self.assertTrue(cat.allowed_for_tier("pam___get_account", Tier.L4))
        self.assertFalse(cat.allowed_for_tier("pam___made_up", Tier.L1))

    def test_lambda_schema_matches_catalogue(self):
        d = ROOT / "gateway/lambda/pam_tools"
        names = {f"pam___{t['name']}" for t in json.loads((d / "tool_schema.json").read_text())}
        names |= {f"identity___{t['name']}" for t in json.loads((d / "identity_tool_schema.json").read_text())}
        cat_names = {t.name for t in cat.CATALOGUE if t.name.startswith(("pam___", "identity___"))}
        self.assertEqual(names, cat_names)
        handler = (d / "handler.py").read_text()
        for n in cat_names:
            self.assertIn(f'"{n}"', handler)

    def test_every_cyberark_tool_requires_environment(self):
        for f in ("pam_tools/tool_schema.json", "pam_tools/identity_tool_schema.json", "logs_query/tool_schema.json"):
            for t in json.loads((ROOT / "gateway/lambda" / f).read_text()):
                self.assertEqual(t["inputSchema"]["required"][0], "environment", t["name"])

    def test_cedar_prod_rule_covers_all_low_risk_writes(self):
        cedar = (ROOT / "policy/cyberark_agent.cedar").read_text()
        prod_block = cedar[cedar.index("Production (Privilege Cloud prod"):]
        for t in cat.CATALOGUE:
            if t.access is cat.Access.WRITE and not t.needs_approval and t.name.split("___")[0] in ("pam", "identity"):
                self.assertIn(f'"{t.name}"', prod_block, t.name)

    def test_approval_tools_need_token_in_cedar(self):
        cedar = (ROOT / "policy/cyberark_agent.cedar").read_text()
        for t in cat.CATALOGUE:
            if t.needs_approval:
                block = cedar[cedar.index(f'"{t.name}"'):]
                block = block[:block.index(";")]
                self.assertIn("approval_token", block, t.name)

    def test_no_forbidden_endpoints_anywhere(self):
        code = (ROOT / "gateway/lambda/pam_tools/handler.py").read_text()
        for bad in cat.FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(bad, code)
        self.assertIsNone(re.search(r"Password/Retrieve", code))


class GuardTests(unittest.TestCase):
    def st(self, **kw):
        kw.setdefault("tier", Tier.L1)
        return SessionState(**kw)

    def test_unknown_tool(self):
        self.assertIn("not in the approved", check_tool_call(self.st(), "pam___delete_safe", {}))

    def test_environment_required(self):
        self.assertIn("environment", check_tool_call(self.st(), "pam___get_account", {"account_id": "1"}))

    def test_reads_ok_with_environment(self):
        self.assertIsNone(check_tool_call(self.st(), "pam___get_account", {"environment": "onprem", "account_id": "1"}))

    def test_environment_pinning(self):
        s = self.st()
        record(s, "pam___get_account", {"environment": "pc-preprod"})
        self.assertEqual(s.environment, "pc-preprod")
        self.assertIn("pinned", check_tool_call(s, "pam___get_account", {"environment": "pc-prod"}))

    def test_identity_not_onprem(self):
        self.assertIn("Privilege Cloud", check_tool_call(self.st(), "identity___get_user",
                                                         {"environment": "onprem", "username": "x"}))

    def test_write_needs_ticket(self):
        s = self.st(has_diagnosis=True)
        self.assertIn("ticket", check_tool_call(s, "pam___activate_user", {"environment": "pc-preprod"}))

    def test_write_needs_diagnosis(self):
        s = self.st(ticket_id="INC1")
        self.assertIn("diagnostics", check_tool_call(s, "pam___activate_user", {"environment": "pc-preprod"}))
        record(s, "pam___get_account_activity", {"environment": "pc-preprod"})
        self.assertIsNone(check_tool_call(s, "pam___verify_account", {"environment": "pc-preprod"}))

    def test_prod_l1_write_needs_approval(self):
        s = self.st(ticket_id="INC1", has_diagnosis=True)
        for env in ("pc-prod", "onprem"):
            self.assertIn("APPROVAL_REQUIRED", check_tool_call(s, "pam___activate_user", {"environment": env}))
        self.assertIsNone(check_tool_call(s, "pam___activate_user", {"environment": "pc-prod", "approval_token": "t"}))

    def test_tier_blocks(self):
        s = self.st(tier=Tier.L3, ticket_id="INC1", has_diagnosis=True)
        self.assertIn("not allowed", check_tool_call(s, "pam___reconcile_account",
                                                     {"environment": "pc-preprod", "approval_token": "x"}))

    def test_approval_required(self):
        s = self.st(tier=Tier.L2, ticket_id="INC1", has_diagnosis=True)
        a = {"environment": "pc-preprod", "account_id": "1"}
        self.assertIn("APPROVAL_REQUIRED", check_tool_call(s, "pam___reconcile_account", a))
        self.assertIsNone(check_tool_call(s, "pam___reconcile_account", {**a, "approval_token": "t"}))

    def test_service_requests_off_by_default(self):
        import os
        os.environ.pop("ENABLE_SERVICE_REQUESTS", None)
        s = self.st(tier=Tier.SR, ticket_id="RITM1")
        self.assertIn("troubleshooting only", check_tool_call(s, "pam___add_safe", {"environment": "pc-preprod"}))
        os.environ["ENABLE_SERVICE_REQUESTS"] = "true"
        try:
            self.assertIn("APPROVAL_REQUIRED", check_tool_call(s, "pam___add_safe", {"environment": "pc-preprod"}))
        finally:
            os.environ.pop("ENABLE_SERVICE_REQUESTS")

    def test_write_limit(self):
        s = self.st(ticket_id="INC1", has_diagnosis=True, max_write_calls=1)
        record(s, "pam___activate_user", {"environment": "pc-preprod"})
        self.assertIn("limit", check_tool_call(s, "pam___activate_user", {"environment": "pc-preprod"}))


if __name__ == "__main__":
    unittest.main()
