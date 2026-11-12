"""Tests for the definition download in jobs/download_flows.py, with requests mocked.
Run from the project root: python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobs import download_flows  # noqa: E402

ENV = "env-1"
FLOW = "flow-1"


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def fake_get(routes):
    """requests.get stand-in: `routes` maps a URL fragment to a FakeResponse. Records every URL called."""
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        for fragment, response in routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected URL {url}")

    get.calls = calls
    return get


SCOPED = f"/environments/{ENV}/flows/{FLOW}"
ADMIN = f"/scopes/admin/environments/{ENV}/flows/{FLOW}"
FULL = {"properties": {"definition": {"actions": {"A": {}}}}}
SUMMARY_ONLY = {"properties": {"definitionSummary": {"actions": []}}}


class GetFlowTests(unittest.TestCase):
    def test_uses_the_environment_scoped_endpoint_first(self):
        get = fake_get({ADMIN: FakeResponse(200, SUMMARY_ONLY), SCOPED: FakeResponse(200, FULL)})
        with mock.patch.object(download_flows.requests, "get", get):
            flow = download_flows.get_flow(ENV, FLOW, {})
        self.assertEqual(flow, FULL)
        self.assertEqual(len(get.calls), 1)
        self.assertNotIn("scopes/admin", get.calls[0])

    def test_falls_back_to_the_admin_endpoint_when_refused(self):
        get = fake_get({ADMIN: FakeResponse(200, SUMMARY_ONLY), SCOPED: FakeResponse(403)})
        with mock.patch.object(download_flows.requests, "get", get):
            flow = download_flows.get_flow(ENV, FLOW, {})
        self.assertEqual(flow, SUMMARY_ONLY)
        self.assertEqual(len(get.calls), 2)
        self.assertIn("scopes/admin", get.calls[1])

    def test_raises_when_both_endpoints_refuse(self):
        get = fake_get({ADMIN: FakeResponse(404), SCOPED: FakeResponse(403)})
        with mock.patch.object(download_flows.requests, "get", get):
            with self.assertRaises(requests.HTTPError) as ctx:
                download_flows.get_flow(ENV, FLOW, {})
        self.assertEqual(ctx.exception.response.status_code, 404)


class DownloadEnvironmentTests(unittest.TestCase):
    def run_download(self, flow_json):
        env = {"name": ENV, "properties": {"displayName": "Default Environment"}}
        flows = [{"name": FLOW, "properties": {"displayName": "Invoice Approval"}}]
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(download_flows, "get_flows", return_value=flows), \
                mock.patch.object(download_flows, "get_flow", return_value=flow_json), \
                mock.patch.object(download_flows, "get_runs", return_value=[]), \
                mock.patch.object(download_flows.time, "sleep"):
            with self.assertLogs(level="DEBUG") as logs:
                download_flows.download_environment(env, {}, out)
            path = os.path.join(out, ENV, f"Invoice Approval_{FLOW}", "definition.json")
            saved = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
        return saved, logs.output

    def test_warns_when_only_a_summary_is_available(self):
        saved, logs = self.run_download(SUMMARY_ONLY)
        self.assertEqual(saved, SUMMARY_ONLY)
        self.assertTrue(any("WARNING" in line and "only a summary" in line for line in logs))

    def test_no_warning_when_the_full_definition_is_available(self):
        saved, logs = self.run_download(FULL)
        self.assertEqual(saved, FULL)
        self.assertFalse(any("only a summary" in line for line in logs))

    def test_environment_id_stays_out_of_info_logs(self):
        _, logs = self.run_download(FULL)
        info_lines = [line for line in logs if line.startswith("INFO")]
        self.assertTrue(info_lines)
        self.assertFalse(any(ENV in line for line in info_lines))


RUNS = [
    {"name": "r-old-ok", "properties": {"status": "Succeeded", "startTime": "2026-01-01T00:00:00Z"}},
    {"name": "r-new-ok", "properties": {"status": "Succeeded", "startTime": "2026-03-01T00:00:00Z"}},
    {"name": "r-fail", "properties": {"status": "Failed", "startTime": "2026-02-01T00:00:00Z"}},
    {"name": "r-live", "properties": {"status": "Running", "startTime": "2026-04-01T00:00:00Z"}},
]


class RunDetailsTests(unittest.TestCase):
    def test_selection_skips_running_and_puts_failed_first(self):
        names = [r["name"] for r in download_flows.select_runs_for_details(RUNS, 2)]
        self.assertEqual(names, ["r-fail", "r-new-ok"])

    def test_inline_and_linked_content_are_saved(self):
        actions = {"value": [{"name": "A", "properties": {
            "status": "Failed", "outputsLink": {"uri": "https://blob.example/out?sig=x"}}}]}
        get = fake_get({"/actions": FakeResponse(200, actions),
                        "blob.example": FakeResponse(200, {"error": "504"})})
        with tempfile.TemporaryDirectory() as flow_dir, \
                mock.patch.object(download_flows.requests, "get", get):
            saved = download_flows.download_run_details(ENV, FLOW, RUNS[2:3], {}, flow_dir, 5)
            with open(os.path.join(flow_dir, "run_details", "r-fail.json"), encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(saved, 1)
        self.assertEqual(data[0]["properties"]["outputsContent"], {"error": "504"})

    def test_existing_files_are_not_fetched_again(self):
        get = fake_get({})
        with tempfile.TemporaryDirectory() as flow_dir, \
                mock.patch.object(download_flows.requests, "get", get):
            os.makedirs(os.path.join(flow_dir, "run_details"))
            open(os.path.join(flow_dir, "run_details", "r-fail.json"), "w").write("[]")
            saved = download_flows.download_run_details(ENV, FLOW, RUNS[2:3], {}, flow_dir, 5)
        self.assertEqual(saved, 0)
        self.assertEqual(get.calls, [])

    def test_refusal_stops_the_flow_without_raising(self):
        get = fake_get({"/actions": FakeResponse(403)})
        with tempfile.TemporaryDirectory() as flow_dir, \
                mock.patch.object(download_flows.requests, "get", get):
            saved = download_flows.download_run_details(ENV, FLOW, RUNS[:3], {}, flow_dir, 5)
            self.assertFalse(os.path.exists(os.path.join(flow_dir, "run_details")))
        self.assertEqual(saved, 0)
        self.assertEqual(len(get.calls), 1)

    def test_limit_zero_turns_it_off(self):
        get = fake_get({})
        with tempfile.TemporaryDirectory() as flow_dir, \
                mock.patch.object(download_flows.requests, "get", get):
            self.assertEqual(download_flows.download_run_details(ENV, FLOW, RUNS, {}, flow_dir, 0), 0)
        self.assertEqual(get.calls, [])


if __name__ == "__main__":
    unittest.main()
