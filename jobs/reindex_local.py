#!/usr/bin/env python3
"""Rebuild the local vector index from flow data already on disk.

Usage (from the project root):
    python jobs/reindex_local.py
    python jobs/reindex_local.py --rebuild   # drop the existing index first

Reads FLOW_DEFINITIONS_PATH (default: .flow-data/flow_exports) and writes the index to
CHROMA_DB_PATH (default: .flow-data/chroma_db).

Use --rebuild after an upgrade that changes how text is indexed (for example, the
redaction added in redaction.py). Entries are keyed by content, so a normal reindex
would keep the old, unredacted entries next to the new ones.
"""
import argparse
import logging
import sys
from pathlib import Path

# Make the project root importable when this file is run directly.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from flow_data import load_flow_data  # noqa: E402
from logging_config import configure_logging  # noqa: E402
from rag_service import RAGService, chroma_path  # noqa: E402


def main_reindex(rebuild=False):
    configure_logging()
    logging.info("Loading flow data from disk...")
    flows_data = load_flow_data()

    path = chroma_path()
    logging.info(f"Initializing RAG service with persistence at {path}")
    rag = RAGService(persist_directory=path)
    if rebuild:
        logging.info("Dropping the existing index (--rebuild).")
        rag.vector_store.delete_collection()
        rag.load()
    rag.index_flows(flows_data)
    logging.info("Reindex complete.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Rebuild the local vector index from flow data on disk.')
    parser.add_argument('--rebuild', action='store_true',
                        help='Drop the existing index before indexing')
    args = parser.parse_args()
    main_reindex(rebuild=args.rebuild)
