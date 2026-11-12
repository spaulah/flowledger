# Contributing to FlowLedger

Thanks for taking an interest. FlowLedger is a small, local tool: it downloads Power Automate flow definitions and run history, indexes them, and answers questions about them from a terminal or a local HTTP API. Contributions that keep it small and dependable are very welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). To report a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## What fits in this repository

In scope:

- Bug fixes, and clearer errors and logs.
- Better indexing and retrieval: what gets indexed, chunking, prompts, redaction rules.
- Support for more OpenAI-compatible endpoints and local models.
- Documentation, tests, and tooling.

Out of scope, because the project is meant to stay a lean, single-user, local tool:

- A bundled web UI. Put Chainlit, Gradio, or your own front end in front of the HTTP API instead. The README has an example.
- Multi-user accounts, per-user permissions, or anything that needs a hosted service.
- Scheduled or event-driven ingestion, and admin or management endpoints.

If you are unsure, open a feature request and ask before writing code.

## Set up

You need Python 3.11 or newer. CI tests 3.11, 3.12, and 3.13.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

You do not need a Microsoft 365 tenant or a model endpoint to run the tests. To run the tool itself, copy `.env.example` to `.env` and follow the [README](README.md).

## Run the tests

```bash
python -m unittest discover -s tests
```

The tests use fakes and mocks. They do not call Microsoft, a model, or the network.

## Project layout

The project is a flat set of modules:

| Path | Role |
| --- | --- |
| `jobs/download_flows.py` | Downloads definitions and run history |
| `jobs/reindex_local.py` | Builds the vector index from data on disk |
| `flow_data.py` | Loads downloaded flows |
| `rag_service.py` | Chunks and indexes flows into Chroma |
| `ai_analysis_service.py` | Retrieval and answering |
| `llm_clients.py` | Chat and embedding clients |
| `redaction.py` | Secret and email redaction |
| `api.py` | FastAPI app |
| `main.py` | Terminal interface |
| `tests/` | Unit tests |

## Guidelines

**Never commit real tenant data.** That means flow exports, run history, environment or tenant IDs, tokens, client secrets, SharePoint URLs, and real email addresses. Test data must be synthetic. If a test needs a flow definition, write a small one by hand. CI scans the history with gitleaks, but check your diff yourself.

**Test what you change.** Add or update tests for new behavior and for bug fixes. A bug fix should come with a test that fails without it. Keep tests fast and offline.

**Keep dependencies few.** Add a dependency only when it earns its place. Give it a bounded range in `requirements.txt`: the version you tested as the lower bound and the next major version as the upper bound.

**Update the docs.** If you add a setting, put it in `.env.example` and the README configuration table. If you change what is indexed, say so in the README, because users then need to rebuild their index.

**Handle data carefully.** Anything that reaches a log, an API response, or the model should go through the redaction rules where it could contain secrets. INFO-level logs must not include environment IDs, tenant IDs, or endpoint URLs.

**Style.** Follow the style of the surrounding code. Write comments that explain why, not what. No linter is enforced yet.

**Commits.** The history uses short Conventional Commit style messages such as `feat: ...` and `fix: ...`.

## Open a pull request

1. Fork the repository and create a branch.
2. Make a focused change. Smaller pull requests get reviewed faster.
3. Run the tests.
4. Open a pull request and fill in the template.

## License

FlowLedger is licensed under the [Apache License 2.0](LICENSE). By submitting a contribution you agree that it is licensed under the same terms, as described in section 5 of the license.
