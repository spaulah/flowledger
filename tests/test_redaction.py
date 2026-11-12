"""Tests for redaction.redact. Run from the project root: python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from redaction import redact  # noqa: E402

FLOW_GUID = "cccccccc-4444-5555-6666-dddddddddddd"
RUN_ID = "08584000000000000000000000000CU00"


class RedactTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self):
        text = f"Flow: Invoice Approval | Run ID: {RUN_ID} | Status: Failed"
        self.assertEqual(redact(text), text)

    def test_flow_and_run_ids_are_kept(self):
        text = f"/flows/{FLOW_GUID}/runs/{RUN_ID}"
        self.assertEqual(redact(text), text)

    def test_empty_input(self):
        self.assertEqual(redact(""), "")
        self.assertIsNone(redact(None))

    def test_bearer_token(self):
        out = redact("Authorization: Bearer abcDEF123456789xyz_-.~+/==")
        self.assertNotIn("abcDEF", out)
        self.assertIn("Bearer [REDACTED]", out)

    def test_jwt(self):
        jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturePART123"
        self.assertEqual(redact(f"token={jwt}"), "token=[REDACTED_JWT]")
        self.assertNotIn("signaturePART123", redact(jwt))

    def test_json_password_keeps_key(self):
        out = redact('{"username": "svc", "password": "hunter2", "other": 1}')
        self.assertNotIn("hunter2", out)
        self.assertIn('"password": "[REDACTED]"', out)
        self.assertIn('"username": "svc"', out)

    def test_client_secret_key(self):
        out = redact('client_secret = "s3cr3tValue"')
        self.assertNotIn("s3cr3tValue", out)

    def test_connection_string(self):
        out = redact("Server=db;Database=x;AccountKey=abc123==;Password=pw1;")
        self.assertNotIn("abc123", out)
        self.assertNotIn("pw1", out)
        self.assertIn("Server=db", out)

    def test_sas_signature_in_url(self):
        out = redact("https://acct.blob.core.windows.net/c/f?sv=2024&se=2026&sig=ABCdef%2B123%3D")
        self.assertNotIn("ABCdef", out)
        self.assertIn("sv=2024", out)
        self.assertIn("sig=[REDACTED]", out)

    def test_api_key(self):
        out = redact("key sk-abcdefghijklmnopqrstuvwx123456 used")
        self.assertNotIn("abcdefghij", out)
        self.assertIn("[REDACTED_API_KEY]", out)

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(redact(pem), "[REDACTED_PRIVATE_KEY]")

    def test_email_address(self):
        out = redact("Send to jane.doe@example.com today")
        self.assertNotIn("jane.doe", out)
        self.assertIn("[REDACTED_EMAIL]", out)

    def test_connection_parameter_name_does_not_hide_the_real_secret(self):
        # Real flow definitions contain keys like "token:clientSecret". The literal value must still be redacted.
        out = redact('"token:clientSecret": "abc123def456ghi789"')
        self.assertNotIn("abc123def456ghi789", out)
        self.assertIn("token:clientSecret", out)

    def test_structure_is_not_damaged(self):
        text = '"token": { "type": "string" }'
        self.assertEqual(redact(text), text)

    def test_expressions_are_not_treated_as_secrets(self):
        text = '"password": "@parameters(\'dbPassword\')"'
        self.assertEqual(redact(text), text)

    def test_query_string_token(self):
        out = redact("https://x/api?token=abc123def456&y=1")
        self.assertEqual(out, "https://x/api?token=[REDACTED]&y=1")

    def test_yaml_style_secret(self):
        self.assertEqual(redact("api_key: abc123def456"), "api_key: [REDACTED]")

    def test_token_type_field_is_not_touched(self):
        text = '{"tokenType": "Bearer", "secretName": "vault-entry"}'
        self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
