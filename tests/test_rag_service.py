"""Tests for rag_service.index_flows with a fake vector store, so no model or network is needed.
Run from the project root: python -m unittest discover -s tests"""
import os
import sys
import unittest
from collections import Counter
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rag_service  # noqa: E402
from rag_service import RAGService, _flow_label, chroma_path  # noqa: E402

ENV_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
TENANT_ID = "11111111-2222-3333-4444-555555555555"
USER_OBJECT_ID = "99999999-8888-7777-6666-555555555555"
FLOW_KEY = f"{ENV_ID}/Invoice Approval_cccccccc-4444-5555-6666-dddddddddddd"


class FakeStore:
    def __init__(self):
        self.docs = {}
        self.add_calls = 0

    def get(self, ids=None, limit=None):
        if ids is not None:
            return {"ids": [i for i in ids if i in self.docs]}
        return {"ids": list(self.docs)[:limit]}

    def add_documents(self, docs, ids):
        self.add_calls += 1
        for doc, doc_id in zip(docs, ids):
            self.docs[doc_id] = doc


def make_service():
    service = RAGService.__new__(RAGService)  # skip __init__: it builds a real embeddings client
    service.persist_directory = None
    service.vector_store = FakeStore()
    return service


def sample_flows():
    return {
        FLOW_KEY: {
            # Shaped like a real definition: the environment ID is inside "id" and "properties.environment",
            # and "properties.creator" holds tenant and user IDs.
            "definition": {
                "id": f"/providers/Microsoft.ProcessSimple/environments/{ENV_ID}/flows/cccccccc",
                "properties": {
                    "displayName": "Invoice Approval",
                    "owner": "jane.doe@example.com",
                    "environment": {"name": ENV_ID, "id": f"/providers/Microsoft.ProcessSimple/environments/{ENV_ID}"},
                    "creator": {"tenantId": TENANT_ID, "objectId": USER_OBJECT_ID, "userId": USER_OBJECT_ID},
                },
            },
            "runs": [{"properties": {"status": "Succeeded"}}, {"properties": {"status": "Failed"}}],
            "run_details": {
                "run1": [{
                    "name": "HTTP_Post_ERP",
                    "properties": {
                        "status": "Failed",
                        "inputsContent": {"password": "hunter2hunter2", "environment": ENV_ID},
                        "outputsContent": {"error": "504"},
                        "startTime": "2026-11-12T10:00:00Z",
                    },
                }]
            },
        }
    }


class FlowLabelTests(unittest.TestCase):
    def test_environment_id_is_removed(self):
        self.assertEqual(_flow_label(FLOW_KEY), "Invoice Approval_cccccccc-4444-5555-6666-dddddddddddd")

    def test_windows_separator(self):
        self.assertEqual(_flow_label(f"{ENV_ID}\\My Flow_abc"), "My Flow_abc")

    def test_key_without_environment_is_unchanged(self):
        self.assertEqual(_flow_label("My Flow_abc"), "My Flow_abc")


class ChromaPathTests(unittest.TestCase):
    def test_default_and_override(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("CHROMA_DB_PATH", None)
            self.assertEqual(chroma_path(), os.path.join(".flow-data", "chroma_db"))
            os.environ["CHROMA_DB_PATH"] = "/data/index"
            self.assertEqual(chroma_path(), "/data/index")


class IndexFlowsTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(rag_service.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_documents_by_type(self):
        service = make_service()
        service.index_flows(sample_flows())
        types = Counter(d.metadata["type"] for d in service.vector_store.docs.values())
        self.assertEqual(types, {"definition": 1, "run_summary": 1, "run_action": 1})

    def test_environment_id_never_reaches_indexed_text_or_metadata(self):
        service = make_service()
        service.index_flows(sample_flows())
        for doc in service.vector_store.docs.values():
            self.assertNotIn(ENV_ID, doc.page_content)
            self.assertNotIn(ENV_ID, str(doc.metadata))
        names = {d.metadata["flow_name"] for d in service.vector_store.docs.values()}
        self.assertEqual(names, {_flow_label(FLOW_KEY)})

    def test_definition_keeps_what_matters_and_drops_tenant_and_user_ids(self):
        service = make_service()
        service.index_flows(sample_flows())
        text = "\n".join(d.page_content for d in service.vector_store.docs.values())
        self.assertNotIn(TENANT_ID, text)
        self.assertNotIn(USER_OBJECT_ID, text)
        self.assertIn("<environment>", text)
        self.assertIn("Invoice Approval", text)
        self.assertIn("cccccccc", text)  # the flow ID stays, it is needed to tell flows apart

    def test_input_data_is_not_modified(self):
        flows = sample_flows()
        make_service().index_flows(flows)
        self.assertIn("creator", flows[FLOW_KEY]["definition"]["properties"])

    def test_secrets_and_emails_are_redacted_before_indexing(self):
        service = make_service()
        service.index_flows(sample_flows())
        text = "\n".join(d.page_content for d in service.vector_store.docs.values())
        self.assertNotIn("hunter2hunter2", text)
        self.assertNotIn("jane.doe@example.com", text)
        self.assertIn("HTTP_Post_ERP", text)  # useful content is kept

    def test_rerun_embeds_nothing_again(self):
        service = make_service()
        service.index_flows(sample_flows())
        calls_after_first = service.vector_store.add_calls
        with self.assertLogs(level="INFO") as logs:
            service.index_flows(sample_flows())
        self.assertEqual(service.vector_store.add_calls, calls_after_first)
        self.assertEqual(len(service.vector_store.docs), 3)
        self.assertTrue(any("Indexed 0 new documents" in m and "3 were already indexed" in m for m in logs.output))

    def test_only_missing_documents_are_added(self):
        service = make_service()
        service.index_flows(sample_flows())
        removed_id = next(i for i, d in service.vector_store.docs.items() if d.metadata["type"] == "run_summary")
        del service.vector_store.docs[removed_id]
        with self.assertLogs(level="INFO") as logs:
            service.index_flows(sample_flows())
        self.assertEqual(len(service.vector_store.docs), 3)
        self.assertTrue(any("Indexed 1 new documents (1 run_summary)" in m for m in logs.output))

    def test_empty_input(self):
        service = make_service()
        with self.assertLogs(level="INFO") as logs:
            service.index_flows({})
        self.assertEqual(service.vector_store.docs, {})
        self.assertTrue(any("nothing new" in m for m in logs.output))


def index_definition(definition, runs=None):
    service = make_service()
    service.index_flows({FLOW_KEY: {"definition": definition, "runs": runs or [], "run_details": {}}})
    return list(service.vector_store.docs.values())


def definition_text(docs, doc_type):
    return "\n".join(d.page_content for d in docs if d.metadata["type"] == doc_type)


class DefinitionDocumentTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(rag_service.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_large_logic_is_chunked_not_truncated(self):
        actions = {f"Action_{i}": {"type": "Http", "inputs": {"uri": "https://example.com/" + "x" * 200}}
                   for i in range(200)}
        docs = index_definition({"properties": {"definition": {"triggers": {"manual": {}}, "actions": actions}}})
        chunks = [d for d in docs if d.metadata["type"] == "definition" and "Definition (part" in d.page_content]
        self.assertGreater(len(chunks), 5)
        text = definition_text(docs, "definition")
        self.assertIn("Action_0", text)
        self.assertIn("Action_199", text)  # the last action is indexed, so nothing was cut off
        self.assertIn(f"part 1 of {len(chunks)}", text)
        self.assertNotIn("(truncated)", text)

    def test_overview_lists_triggers_and_connectors(self):
        docs = index_definition({"properties": {
            "displayName": "Invoice Approval", "state": "Started",
            "connectionReferences": {"a": {"displayName": "SharePoint"}, "b": {"displayName": "Microsoft Teams"}},
            "definition": {"triggers": {"When_item_created": {}}, "actions": {}},
        }})
        overview = next(d.page_content for d in docs if "Display name:" in d.page_content)
        self.assertIn("Display name: Invoice Approval", overview)
        self.assertIn("State: Started", overview)
        self.assertIn("Triggers: When_item_created", overview)
        self.assertIn("Connectors: SharePoint, Microsoft Teams", overview)
        self.assertIn("Full definition: available", overview)

    def test_summary_is_indexed_when_the_logic_is_missing(self):
        docs = index_definition({"properties": {"definitionSummary": {
            "triggers": [{"swaggerOperationId": "GetOnNewItems", "type": "OpenApiConnectionNotification"}],
            "actions": [{"swaggerOperationId": "PostMessageToConversation"}],
        }}})
        self.assertIn("Triggers: GetOnNewItems", definition_text(docs, "definition"))
        self.assertIn("only a summary", definition_text(docs, "definition"))
        self.assertIn("PostMessageToConversation", definition_text(docs, "definition_summary"))

    def test_summary_is_skipped_when_the_logic_is_present(self):
        docs = index_definition({"properties": {
            "definition": {"triggers": {"manual": {}}, "actions": {}},
            "definitionSummary": {"triggers": [], "actions": []},
        }})
        self.assertEqual(definition_text(docs, "definition_summary"), "")

    def test_empty_definition_adds_no_definition_documents(self):
        docs = index_definition({}, runs=[{"properties": {"status": "Succeeded"}}])
        self.assertEqual({d.metadata["type"] for d in docs}, {"run_summary"})

    def test_connection_reference_bulk_is_not_indexed(self):
        docs = index_definition({"properties": {
            "connectionReferences": {"a": {"displayName": "SharePoint", "swagger": "BULKMARKER" * 5000}},
            "definition": {"triggers": {"manual": {}}, "actions": {}},
        }})
        text = "\n".join(d.page_content for d in docs)
        self.assertNotIn("BULKMARKER", text)
        self.assertIn("SharePoint", text)

    def test_secrets_emails_and_environment_ids_in_logic_are_cleaned(self):
        docs = index_definition({"properties": {"definition": {"triggers": {}, "actions": {"Call": {"inputs": {
            "uri": f"https://x/environments/{ENV_ID}/flows", "password": "hunter2hunter2", "notify": "jane.doe@example.com",
        }}}}}})
        text = "\n".join(d.page_content for d in docs)
        self.assertNotIn("hunter2hunter2", text)
        self.assertNotIn("jane.doe@example.com", text)
        self.assertNotIn(ENV_ID, text)
        self.assertIn("<environment>", text)


if __name__ == "__main__":
    unittest.main()
