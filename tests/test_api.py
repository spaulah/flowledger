"""Tests for api.py with a fake service, so no model or index is needed.
Run from the project root: python -m unittest discover -s tests
Needs httpx (see requirements-dev.txt)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from api import create_app  # noqa: E402


class FakeRag:
    def __init__(self, ready=True):
        self.ready = ready

    def has_documents(self):
        return self.ready


class FakeService:
    def __init__(self, ready=True, error=None):
        self.rag_service = FakeRag(ready)
        self.error = error
        self.calls = []

    def ask_sync(self, question, k=8):
        self.calls.append((question, k))
        if self.error:
            raise self.error
        return "Use a Try scope.", [
            {"metadata": {"flow_name": "Invoice Approval", "type": "definition"}, "snippet": "Flow Name: ..."},
            {"metadata": {}, "snippet": "Run Summary: 5 runs"},
        ]


def make_client(service):
    return TestClient(create_app(service))


class ApiTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("FLOWLEDGER_API_KEY", None)

    def test_health_reports_index_state(self):
        with make_client(FakeService(ready=True)) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok", "index_ready": True})
        with make_client(FakeService(ready=False)) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok", "index_ready": False})

    def test_ask_returns_answer_and_sources(self):
        service = FakeService()
        with make_client(service) as client:
            r = client.post("/ask", json={"question": "Improve the flows", "k": 3})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["answer"], "Use a Try scope.")
        self.assertEqual(body["sources"][0]["flow_name"], "Invoice Approval")
        self.assertEqual(body["sources"][0]["type"], "definition")
        self.assertIsNone(body["sources"][1]["flow_name"])
        self.assertEqual(service.calls, [("Improve the flows", 3)])

    def test_ask_uses_default_k(self):
        service = FakeService()
        with make_client(service) as client:
            client.post("/ask", json={"question": "hi"})
        self.assertEqual(service.calls, [("hi", 8)])

    def test_empty_index_returns_503(self):
        with make_client(FakeService(ready=False)) as client:
            r = client.post("/ask", json={"question": "hi"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("reindex", r.json()["detail"])

    def test_invalid_input_returns_422(self):
        with make_client(FakeService()) as client:
            self.assertEqual(client.post("/ask", json={"question": ""}).status_code, 422)
            self.assertEqual(client.post("/ask", json={"question": "hi", "k": 0}).status_code, 422)
            self.assertEqual(client.post("/ask", json={"question": "hi", "k": 21}).status_code, 422)
            self.assertEqual(client.post("/ask", json={}).status_code, 422)

    def test_service_failure_returns_502_without_leaking_details(self):
        service = FakeService(error=RuntimeError("https://secret-endpoint.example/openai failed"))
        with make_client(service) as client:
            r = client.post("/ask", json={"question": "hi"})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("secret-endpoint", r.text)

    def test_api_key_is_optional_when_not_configured(self):
        with make_client(FakeService()) as client:
            self.assertEqual(client.post("/ask", json={"question": "hi"}).status_code, 200)

    def test_api_key_is_enforced_when_configured(self):
        os.environ["FLOWLEDGER_API_KEY"] = "s3cret"
        with make_client(FakeService()) as client:
            body = {"question": "hi"}
            self.assertEqual(client.post("/ask", json=body).status_code, 401)
            self.assertEqual(client.post("/ask", json=body, headers={"X-API-Key": "wrong"}).status_code, 401)
            self.assertEqual(client.post("/ask", json=body, headers={"X-API-Key": "s3cret"}).status_code, 200)

    def test_health_does_not_require_the_api_key(self):
        os.environ["FLOWLEDGER_API_KEY"] = "s3cret"
        with make_client(FakeService()) as client:
            self.assertEqual(client.get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
