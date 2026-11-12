"""Load downloaded flow data from disk.

Shared by the CLI (main.py), the reindex job, and anything else that needs the
flow data as Python objects.
"""
import json
import logging
import os

# Local data lives in one hidden folder (git-ignored): .flow-data/flow_exports, .flow-data/chroma_db
DEFAULT_FLOW_DATA_DIR = os.path.join(".flow-data", "flow_exports")


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_flow_data(root=None):
    """Load every flow folder under `root`.

    jobs/download_flows.py writes one folder per flow, nested under an
    environment folder:

        <root>/<environment>/<flow name>_<flow id>/definition.json
        <root>/<environment>/<flow name>_<flow id>/runs.json
        <root>/<environment>/<flow name>_<flow id>/run_details/<session id>.json

    Returns:
        dict: {flow_key: {'definition': dict, 'runs': list, 'run_details': dict}}
        where flow_key is the folder path relative to `root`.
    """
    root = root or os.getenv("FLOW_DEFINITIONS_PATH", DEFAULT_FLOW_DATA_DIR)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"{root} not found. Run jobs/download_flows.py first.")

    flows = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        if "definition.json" not in filenames and "runs.json" not in filenames:
            continue

        key = os.path.relpath(dirpath, root)
        definition = {}
        runs = []
        run_details = {}

        if "definition.json" in filenames:
            definition = _read_json(os.path.join(dirpath, "definition.json"))
        if "runs.json" in filenames:
            runs = _read_json(os.path.join(dirpath, "runs.json"))

        details_dir = os.path.join(dirpath, "run_details")
        if os.path.isdir(details_dir):
            for run_file in os.listdir(details_dir):
                if not run_file.endswith(".json"):
                    continue
                run_id = run_file[:-len(".json")]
                try:
                    run_details[run_id] = _read_json(os.path.join(details_dir, run_file))
                except Exception as e:
                    logging.warning(f"Failed to load run details for {run_id}: {e}")

        flows[key] = {"definition": definition, "runs": runs, "run_details": run_details}

    return flows
