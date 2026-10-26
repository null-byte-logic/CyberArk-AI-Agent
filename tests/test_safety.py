import unittest

import _paths  # noqa: F401
from agent.safety import ApprovalError, mint_approval, redact, redact_obj, verify_approval

KEY = b"test-key-0123456789"


class RedactTests(unittest.TestCase):
    def test_password_field(self):
        out = redact('{"username":"svc","password":"Winter2026!"}')
        self.assertNotIn("Winter2026!", out)
        self.assertIn("REDACTED", out)

    def test_inline_assignment(self):
        self.assertNotIn("hunter2", redact("logon failed pwd=hunter2 for user x"))

    def test_bearer_and_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        out = redact(f"Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456 and {jwt}")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", out)
        self.assertNotIn(jwt, out)

    def test_pem(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(redact(pem), "[REDACTED:PEM_PRIVATE_KEY]")

    def test_aws_key(self):
        self.assertIn("[REDACTED:AWS_ACCESS_KEY]", redact("key AKIAABCDEFGHIJKLMNOP here"))

    def test_plain_text_untouched(self):
        s = "CPM verify failed for account 12_34 on db01 (CACPM410E)"
        self.assertEqual(redact(s), s)

    def test_redact_obj_drops_secret_keys(self):
        out = redact_obj({"data": {"secret": "x", "items": [{"password": "p", "name": "ok"}]}})
        self.assertEqual(out["data"]["secret"], "[REDACTED]")
        self.assertEqual(out["data"]["items"][0]["password"], "[REDACTED]")
        self.assertEqual(out["data"]["items"][0]["name"], "ok")


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.args = {"account_id": "12_34"}
        self.token = mint_approval(KEY, "pam___reconcile_account", self.args, "CHG001", "jane", 600, now=1000)

    def test_valid(self):
        h = verify_approval(KEY, self.token, "pam___reconcile_account", self.args, now=1100)
        self.assertEqual(h["ticket"], "CHG001")
        self.assertEqual(h["approver"], "jane")

    def test_args_changed(self):
        with self.assertRaises(ApprovalError):
            verify_approval(KEY, self.token, "pam___reconcile_account", {"account_id": "99_1"}, now=1100)

    def test_other_tool(self):
        with self.assertRaises(ApprovalError):
            verify_approval(KEY, self.token, "pam___change_account", self.args, now=1100)

    def test_expired(self):
        with self.assertRaises(ApprovalError):
            verify_approval(KEY, self.token, "pam___reconcile_account", self.args, now=5000)

    def test_wrong_key(self):
        with self.assertRaises(ApprovalError):
            verify_approval(b"other", self.token, "pam___reconcile_account", self.args, now=1100)

    def test_malformed(self):
        with self.assertRaises(ApprovalError):
            verify_approval(KEY, "abc.def", "pam___reconcile_account", self.args, now=1100)

    def test_arg_order_irrelevant(self):
        args = {"safe_name": "S", "member_name": "G", "permission_profile": "EndUser"}
        tok = mint_approval(KEY, "pam___add_safe_member", args, "R1", "bob", now=0)
        reordered = {"permission_profile": "EndUser", "member_name": "G", "safe_name": "S"}
        verify_approval(KEY, tok, "pam___add_safe_member", reordered, now=10)


if __name__ == "__main__":
    unittest.main()
