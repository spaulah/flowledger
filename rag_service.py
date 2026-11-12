import json
import hashlib
import logging
import os
import re
import time
from collections import Counter
from typing import Dict, Any

# Chroma sends anonymous usage telemetry unless told not to. Keep it off by default.
# Set ANONYMIZED_TELEMETRY=True in the environment to opt in. This must be set before chromadb is imported.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from langchain_chroma import Chroma  # noqa: E402
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
from llm_clients import build_embeddings
from redaction import redact

load_dotenv()

DEFAULT_CHROMA_PATH = os.path.join(".flow-data", "chroma_db")


def chroma_path() -> str:
    """Where the vector index lives. Shared by the CLI, the API, and the reindex job."""
    return os.getenv("CHROMA_DB_PATH", DEFAULT_CHROMA_PATH)


def _flow_label(flow_key: str) -> str:
    """The flow's own folder name, without the environment ID in front of it.

    flow_data.load_flow_data keys flows as "<environment id>/<flow name>_<flow id>". The environment
    ID is tenant information, so it stays out of indexed text, which reaches the chat model and the API.
    """
    return re.split(r"[\\/]", flow_key)[-1]


def _environment_id(flow_key: str):
    """The environment ID in front of the flow folder name, or None if the key has none."""
    parts = re.split(r"[\\/]", flow_key)
    return parts[0] if len(parts) > 1 else None


def _without_owner(definition):
    """Drop the creator block (tenant, object, and user IDs) from a flow definition.

    It identifies the tenant and a person, and says nothing about how the flow works.
    """
    props = definition.get("properties") if isinstance(definition, dict) else None
    if not isinstance(props, dict) or "creator" not in props:
        return definition
    return {**definition, "properties": {k: v for k, v in props.items() if k != "creator"}}


# Safety cap on the text indexed for one flow's logic. Real definitions are far smaller (a few thousand characters).
MAX_DEFINITION_CHARS = 150_000


def _trigger_names(structure):
    """Trigger names from a definition (a dict of triggers) or a definition summary (a list of triggers)."""
    triggers = structure.get("triggers") if isinstance(structure, dict) else None
    if isinstance(triggers, dict):
        return list(triggers)
    if isinstance(triggers, list):
        return [t.get("swaggerOperationId") or t.get("type") or "trigger" for t in triggers if isinstance(t, dict)]
    return []


def _connector_names(properties):
    """Display names of the connectors a flow uses. The rest of connectionReferences is bulk, so it is not indexed."""
    refs = properties.get("connectionReferences") or {}
    names = []
    for ref in (refs.values() if isinstance(refs, dict) else refs):
        name = ref.get("displayName") or ref.get("apiName") if isinstance(ref, dict) else None
        if name and name not in names:
            names.append(name)
    return names


def _definition_documents(flow_name, definition, clean, splitter):
    """Documents describing one flow's structure: a short overview, then its logic in chunks.

    The logic is properties.definition (triggers and actions). It is split into chunks, not truncated.
    When the app could not read it, the definition summary is indexed instead, so the flow is still described.
    Returns [] for an empty definition, for example when only runs.json was downloaded.
    """
    props = definition.get("properties") if isinstance(definition, dict) else None
    if not isinstance(props, dict) or not props:
        return []
    logic = props.get("definition") or {}
    summary = props.get("definitionSummary") or {}

    lines = [f"Flow: {flow_name}"]
    for label, key in (("Display name", "displayName"), ("State", "state"),
                       ("Created", "createdTime"), ("Last modified", "lastModifiedTime")):
        if props.get(key):
            lines.append(f"{label}: {props[key]}")
    triggers = _trigger_names(logic or summary)
    if triggers:
        lines.append(f"Triggers: {', '.join(triggers)}")
    connectors = _connector_names(props)
    if connectors:
        lines.append(f"Connectors: {', '.join(connectors)}")
    lines.append("Full definition: available" if logic else "Full definition: not available, only a summary")
    docs = [Document(page_content=clean("\n".join(lines)),
                     metadata={"flow_name": flow_name, "type": "definition"})]

    if logic:
        doc_type, header, body = "definition", "Definition", logic
    elif summary:
        doc_type, header, body = "definition_summary", "Definition summary (the full definition could not be read)", summary
    else:
        return docs

    text = clean(json.dumps(body, indent=2))
    if len(text) > MAX_DEFINITION_CHARS:
        logging.warning(f"{flow_name}: the definition is {len(text)} characters. Only the first {MAX_DEFINITION_CHARS} are indexed.")
        text = text[:MAX_DEFINITION_CHARS] + "... (truncated)"
    chunks = splitter.split_text(text)
    for i, chunk in enumerate(chunks, 1):
        docs.append(Document(page_content=f"Flow: {flow_name}\n{header} (part {i} of {len(chunks)}):\n{chunk}",
                             metadata={"flow_name": flow_name, "type": doc_type}))
    return docs


class RAGService:
    def __init__(self, persist_directory: str = None):
        """Initialize RAGService.

        Args:
            persist_directory: If set, Chroma will persist the vector DB to this directory.
        """
        self.persist_directory = persist_directory
        self.embeddings = build_embeddings()

                # Initialize ChromaDB (persist to disk if `persist_directory` provided)
        chroma_kwargs = dict(
            collection_name="flow_knowledge",
            embedding_function=self.embeddings
        )
        if self.persist_directory:
            chroma_kwargs['persist_directory'] = self.persist_directory

        self.vector_store = Chroma(**chroma_kwargs)

    def persist(self):
        """Persist Chroma collection to disk (if supported)."""
        try:
            if hasattr(self.vector_store, 'persist'):
                self.vector_store.persist()
        except Exception:
            # Some Chroma wrappers persist on add_documents when persist_directory is set.
            pass

    def load(self):
        """Reload the vector store from `persist_directory` by reinitializing Chroma."""
        if not self.persist_directory:
            return
        chroma_kwargs = dict(
            collection_name="flow_knowledge",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )
        self.vector_store = Chroma(**chroma_kwargs)

    def _safe_json_dumps(self, obj, max_length=2000):
        """Safely dump JSON with truncation to avoid performance issues."""
        try:
            json_str = json.dumps(obj, indent=2)
            if len(json_str) > max_length:
                json_str = json_str[:max_length] + "... (truncated)"
            return json_str
        except Exception as e:
            return f"Error serializing JSON: {str(e)}"

    def index_flows(self, flows_data: Dict[str, Any]):
        """
        Index flow definitions, run summaries, and run details into ChromaDB via LangChain.
        Run details (action inputs and outputs) are indexed when a flow folder has them on disk.
        Documents already in the index are skipped, so re-running does not embed them again.
        """
        logging.info("Indexing flows into RAG Knowledge Base...")

        langchain_docs = []
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=3000, chunk_overlap=200)

        for flow_key, data in flows_data.items():
            flow_name = _flow_label(flow_key)
            env_id = _environment_id(flow_key)

            def clean(text, env_id=env_id):
                # The environment ID also appears inside definitions ("id", "properties.environment").
                if env_id:
                    text = text.replace(env_id, "<environment>")
                return redact(text)

            # 1. Index Flow Definition: an overview, then the logic in chunks.
            langchain_docs.extend(
                _definition_documents(flow_name, _without_owner(data.get('definition', {})), clean, text_splitter)
            )

            # 2. Index Run Summaries
            runs = data.get('runs', [])
            if runs:
                # Both the Flow API and the Dataverse downloader store status under "properties".
                statuses = [(r.get('properties') or {}).get('status', r.get('status')) for r in runs]
                succeeded = sum(1 for s in statuses if s == 'Succeeded')
                failed = sum(1 for s in statuses if s == 'Failed')
                total = len(runs)
                summary = f"Flow Name: {flow_name}\nRun Summary: {total} total runs, {succeeded} succeeded, {failed} failed."
                langchain_docs.append(Document(
                    page_content=summary,
                    metadata={"flow_name": flow_name, "type": "run_summary"}
                ))
            
            # 3. Index Run Details (Action Inputs/Outputs)
            run_details = data.get('run_details', {})
            for run_id, actions in run_details.items():
                for action in actions:
                    # Flow API actions nest fields under "properties"; Dataverse workflowlogs are flat.
                    props = action.get('properties') or {}
                    status = props.get('status', action.get('status', 'Unknown'))
                    action_name = action.get('name') or action.get('activityname') or 'Unknown'
                    inputs = props.get('inputsContent', action.get('inputs', {}))
                    outputs = props.get('outputsContent', action.get('outputs', {}))
                    start_time = props.get('startTime', action.get('starttime', ''))
                    
                    content = clean(f"Flow: {flow_name} | Run ID: {run_id}\nAction: {action_name} | Status: {status}\nTime: {start_time}\nInputs: {self._safe_json_dumps(inputs)}\nOutputs: {self._safe_json_dumps(outputs)}")
                    
                    # Prioritize failed actions for full indexing
                    if status == 'Failed':
                        chunks = text_splitter.split_text(content)
                        for chunk in chunks:
                            langchain_docs.append(Document(
                                page_content=chunk,
                                metadata={"flow_name": flow_name, "run_id": run_id, "action_name": action_name, "status": status, "type": "run_action"}
                            ))
                    elif status == 'Succeeded':
                        # For successful actions, index non-repetitive ones to manage size
                        # Skip 'Apply_to_each' loops as they can be voluminous
                        if 'Apply_to_each' not in action_name:
                            chunks = text_splitter.split_text(content)
                            for chunk in chunks:
                                langchain_docs.append(Document(
                                    page_content=chunk,
                                    metadata={"flow_name": flow_name, "run_id": run_id, "action_name": action_name, "status": status, "type": "run_action"}
                                ))
        
        new_docs = []
        if langchain_docs:
            # Deterministic IDs from content, so re-indexing never duplicates entries.
            ids = [
                hashlib.sha256(
                    doc.page_content.encode('utf-8') + json.dumps(doc.metadata, sort_keys=True).encode('utf-8')
                ).hexdigest()
                for doc in langchain_docs
            ]
            # Skip what is already stored: same content and metadata means the same embedding.
            existing = set()
            for i in range(0, len(ids), 500):
                existing.update(self.vector_store.get(ids=ids[i:i + 500]).get("ids", []))
            new_docs = [(doc, doc_id) for doc, doc_id in zip(langchain_docs, ids) if doc_id not in existing]

            # Add in small batches with a delay, to stay under embedding rate limits.
            batch_size = 10
            for i in range(0, len(new_docs), batch_size):
                batch = new_docs[i:i + batch_size]
                self.vector_store.add_documents([d for d, _ in batch], ids=[doc_id for _, doc_id in batch])
                time.sleep(1)

            # Persist to disk if requested
            self.persist()

        by_type = Counter(doc.metadata["type"] for doc, _ in new_docs)
        breakdown = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items())) or "nothing new"
        logging.info(
            f"Indexed {len(new_docs)} new documents ({breakdown}); "
            f"{len(langchain_docs) - len(new_docs)} were already indexed."
        )

    def has_documents(self) -> bool:
        """True if the index holds at least one document."""
        return bool(self.vector_store.get(limit=1).get("ids"))

    def get_retriever(self, k=15):
        return self.vector_store.as_retriever(search_kwargs={"k": k})
