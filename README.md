# FlowLedger

An AI assistant for Power Automate flow history. It downloads flow definitions and run history from your Power Platform environments, indexes them into a local vector store, and answers questions about them: failure patterns, cross-flow dependencies, loops, and how a flow is built.

It is useful when flows interact in ways that are hard to see from the designer, for example when one flow's output triggers another, or when several flows write to the same list.

This tool runs on your own machine. It reads your Power Platform environments, keeps the downloaded data and the index in `.flow-data/`, and exposes a small HTTP API on localhost.

![FlowLedger answering "Suggest improvements to the ERT flows" in the terminal](docs/flowledger-demo.gif)

## Components

| File                     | Role                                                                                                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jobs/download_flows.py` | Downloads flow definitions and run history from the Power Automate admin API, using a service principal. Also downloads action-level inputs and outputs for recent runs. |
| `flow_data.py`           | Loads the downloaded folders into memory.                                                                                                                        |
| `llm_clients.py`         | Builds the chat and embedding clients for Azure or any OpenAI-compatible API, from the `LLM_*` settings.                                                         |
| `rag_service.py`         | Chunks the data and stores embeddings in ChromaDB.                                                                                                               |
| `ai_analysis_service.py` | Retrieves relevant chunks and asks the chat model to answer.                                                                                                     |
| `api.py`                 | HTTP API built with FastAPI: `POST /ask` and `GET /health`.                                                                                                      |
| `main.py`                | Command-line interface: download, index, then ask questions in a terminal. It uses the same index as the API.                                                    |
| `jobs/reindex_local.py`  | Rebuilds the vector index from data already on disk.                                                                                                             |
| `redaction.py`           | Replaces secret-like values (tokens, keys, SAS signatures, passwords) and email addresses with placeholders before indexing and before the chat model sees them. |

## Prerequisites

1. **Service principal (app registration)** in Microsoft Entra ID with a client secret. The scripts use the client-credentials flow, so no user signs in.
   - The easiest way is `scripts/setup_dev_app_registration.sh`. It creates the app, the secret, and the `.env` values, and registers the app as a Power Platform management app. The signed-in account must be a Power Platform administrator.
   - Two steps stay manual in the Power Platform admin center, once per environment:
     1. Make the admin account a **System Administrator** in that environment (Settings > Users + permissions > Users).
     2. Add the app as an **application user** with System Administrator (Settings > Users + permissions > Application users > + New app user).
   - Your tenant admin may need to grant consent for the API permissions.
   - To re-run only the Power Platform registration: `scripts/register_power_platform_app.sh`.
2. **A chat and embeddings endpoint.** Any OpenAI-compatible API works (OpenAI, Ollama, LM Studio, vLLM, and similar), as do Azure OpenAI and Azure AI Foundry deployments. Set `LLM_PROVIDER` to `openai` or `azure`.
3. **Python 3.11+**.

## Setup

All downloaded data and the vector index go in a hidden `.flow-data/` folder at the project root. It is git-ignored, so it won't be committed. Your editor and `ls -a` show it at the top.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your values
```

Never commit `.env`. The `.gitignore` already excludes it, along with the downloaded flow data and the vector index.

`requirements.txt` gives each direct dependency a bounded range: the version this project was tested with as the lower bound, and the next major version as the upper bound. CI installs it on Python 3.11, 3.12, and 3.13 and runs the tests.

## Usage

### 1. Download flow data

```bash
python jobs/download_flows.py
```

This writes one folder per flow to `FLOW_DEFINITIONS_PATH` (default `.flow-data/flow_exports/`):

```
.flow-data/flow_exports/<environment>/<flow name>_<flow id>/
    definition.json
    runs.json
    run_details/<run id>.json
```

`run_details/` holds the status, inputs and outputs of each action in a run. It is fetched for the most recent `FLOW_RUN_DETAILS_LIMIT` runs of each flow (default 25, failed runs first; `0` turns it off), and runs already on disk are not fetched again. This uses the environment-scoped Flow API. If the app is refused (HTTP 401/403) for a flow, the log says so and that flow keeps only `definition.json` and `runs.json`. Run details are redacted like everything else before indexing.

Full flow definitions (the triggers and actions) come from the environment-scoped Flow API, with the admin API as a fallback. For some flows the app is refused (HTTP 403) and only a summary can be saved. The download log names those flows with `only a summary is available`. They are still indexed, but answers about how they work are limited to what the summary shows.

### 2. Build the index

```bash
python jobs/reindex_local.py
```

Re-running this is safe. Entries are keyed by content, so unchanged data is neither duplicated nor embedded again.

After upgrading to a version that changes how text is indexed, rebuild the index once (this release added redaction and removed environment IDs from flow names):

```bash
python jobs/reindex_local.py --rebuild
```

Without `--rebuild`, the old entries stay in the index next to the new ones. Flows you deleted from the tenant also stay until you rebuild.

### 3a. Use the HTTP API

```bash
uvicorn api:app --host 127.0.0.1 --port 8000
```

The API answers from the index built in step 2. Interactive docs are at http://127.0.0.1:8000/docs.

```bash
curl -s http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Suggest improvements to the ERT flows"}'
```

The response has an `answer` and the `sources` it was based on (flow, chunk type, snippet). `GET /health` reports whether the index has data. `/ask` returns 503 if the index is empty.

The API has no user accounts. It serves everything in the index to anyone who can reach it. Keep it bound to `127.0.0.1`, or set `FLOWLEDGER_API_KEY` so every `/ask` call must send a matching `X-API-Key` header.

### 3b. Or use the terminal

```bash
python main.py                   # download, index (new documents only), then ask questions
python main.py --skip-download   # use data already on disk
```

### Graphical interface

FlowLedger does not include a web UI, to stay small. Put one in front of the API. [Chainlit](https://chainlit.io) and [Gradio](https://www.gradio.app) both work. For example, a Gradio chat window:

```python
import gradio as gr
import requests

def ask(message, history):
    r = requests.post("http://127.0.0.1:8000/ask", json={"question": message}, timeout=120)
    r.raise_for_status()
    return r.json()["answer"]

gr.ChatInterface(ask).launch()
```

## Configuration

See `.env.example` for the full list. The main variables:

| Variable                                  | Purpose                                                                                         | Default                   |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------- |
| `LLM_PROVIDER`                            | `openai` (any OpenAI-compatible API) or `azure`. If unset, Azure is detected from the host name | auto                      |
| `LLM_BASE_URL`                            | Endpoint for chat and embeddings                                                                | required                  |
| `LLM_API_KEY`                             | API key. Optional for local servers                                                             | required for Azure        |
| `LLM_MODEL`                               | Chat model, or deployment name on Azure                                                         | `gpt-4o`                  |
| `EMBEDDING_MODEL`                         | Embedding model, or deployment name on Azure. Changing it requires rebuilding the index         | `text-embedding-3-small`  |
| `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET` | Service principal for downloading                                                  | required for downloads    |
| `FLOWLEDGER_API_KEY`                      | Optional. If set, `POST /ask` requires this value in the `X-API-Key` header                       | unset (no auth)           |
| `LOG_LEVEL`                               | `INFO` or `DEBUG`. `DEBUG` also shows HTTP request lines and environment IDs. Don't share debug output | `INFO`            |
| `FLOW_DEFINITIONS_PATH`                   | Where downloaded flow data lives                                                                | `.flow-data/flow_exports` |
| `CHROMA_DB_PATH`                          | Where the vector index is stored                                                                | `.flow-data/chroma_db`    |

## Privacy

Flow definitions and run history can contain business data, connection names, and sometimes personal information from action inputs and outputs. Treat everything under `.flow-data/` (flow exports and the vector index) as sensitive. Do not share them, and do not check them into version control.

FlowLedger redacts obvious secrets and personal data before text is embedded and before it is sent to the chat model. [`redaction.py`](redaction.py) covers bearer tokens, JWTs, API keys, SAS signatures, connection-string secrets, password-style fields, private keys, and email addresses. Flow and run IDs are kept.

Redaction is a pattern match, not a guarantee. It cannot recognize business data, such as customer names or amounts. The chat model is the endpoint you configure with `LLM_BASE_URL`. If that endpoint is a cloud service, the redacted flow text is sent to it. Use a local endpoint (Ollama, LM Studio) to keep all data on your machine.

## Troubleshooting

- **`M365_CLIENT_ID, M365_CLIENT_SECRET, and M365_TENANT_ID must be set`**: fill in the service principal values in `.env`. Older `.env` files used `CLIENT_ID`, `CLIENT_SECRET`, and `TENANT_ID`. Rename those keys to the `M365_` names.
- **403 on `api.bap.microsoft.com/.../scopes/admin/environments`**: the app is not registered as a Power Platform management app. Run `scripts/register_power_platform_app.sh` while signed in as a Power Platform administrator.
- **`missing prvReadApplicationUser privilege` when adding the app user**: the admin account you used to add it is not a System Administrator in that environment.
- **`<flow>: only a summary is available` in the download log**: the app was refused (HTTP 403) when reading that flow's definition, so only its summary was saved. Answers about that flow's logic are limited. Which flows are refused depends on their ownership and sharing. The cause has not been fully diagnosed.
- **A flow shows `could not read runs (500)` in the download log**: the Flow API failed for that flow. It is skipped and the rest of the download continues. Run the download again later.
- **`.flow-data/flow_exports not found`**: run `python jobs/download_flows.py` first, or set `FLOW_DEFINITIONS_PATH` to your existing data.
- **`/ask` returns 503, or answers lack context**: check that `CHROMA_DB_PATH` points at a built index. Run `jobs/reindex_local.py` if it does not.
- **`/ask` returns 502**: the chat or embedding call failed. The server log has the details. Check the endpoint URL, key, and model names in `.env`.
- **Embedding or chat errors**: check the endpoint URL, key, and deployment names in `.env`.

## Development

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests
```

The tests use fakes. They need no Microsoft 365 tenant and no model endpoint.

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md): how to set up and test, and what is in scope.
- [SECURITY.md](SECURITY.md): how to report a vulnerability, and how FlowLedger handles your data.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
