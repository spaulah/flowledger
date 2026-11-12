# Security Policy

FlowLedger reads Power Automate flow definitions and run history from your tenant, stores them on disk, and sends parts of them to a chat and embeddings endpoint you choose. Treat it like any tool with read access to business automation. This page explains how to report a problem and what to know before you run it.

## Reporting a vulnerability

Please report security problems privately. Do not open a public issue.

Use GitHub's private vulnerability reporting: open the **Security** tab of this repository and choose **Report a vulnerability**.

Include:

- What the problem is and what an attacker could do with it.
- Steps to reproduce, and the commit or version you tested.
- Your Python version and operating system, if relevant.

Do not include real flow definitions, run history, tenant or environment IDs, tokens, client secrets, or API keys. Redact them, or describe the shape of the data instead.

This is a single-maintainer project. I aim to acknowledge a report within 7 days and to share a plan or a fix within 30 days. These are goals, not guarantees. I will credit you in the fix unless you prefer not to be named.

## Supported versions

FlowLedger has no stable release yet. Only the latest commit on `main` receives security fixes.

## In scope

- Flaws in this repository's code: the HTTP API (`api.py`), the redaction rules (`redaction.py`), the download and indexing jobs, and the setup scripts in `scripts/`.
- Ways to read data the tool should not expose, for example bypassing the `X-API-Key` check, or secrets that survive redaction in common formats.
- Unsafe handling of credentials or temporary files.
- Known vulnerabilities in a dependency that this project's use of it makes exploitable.

## Out of scope

- Problems in Microsoft services, in your chat or embeddings provider, or in a dependency itself. Report those upstream.
- Running the API on a network without an API key, or behind no authentication, and then being surprised that others can read it. The API has no user accounts. See below.
- Findings that need an attacker who already controls your machine or your `.env` file.

## How FlowLedger handles your data

**On disk.** The download job writes flow definitions and run history as plain JSON to `.flow-data/flow_exports/`. The vector index is in `.flow-data/chroma_db/`. Neither is encrypted. Both are git-ignored. Keep them out of backups and shares you do not control.

**Sent to your model endpoint.** When you index, the text of flow overviews, flow logic, and run summaries is sent to the embeddings endpoint in `LLM_BASE_URL`. When you ask a question, the question and the retrieved passages are sent to the chat model. If that endpoint is a cloud service, that data leaves your machine. Use a local endpoint such as Ollama or LM Studio to keep everything local.

**Redaction is a safety net, not a guarantee.** Before text is embedded and before it is sent to the chat model, `redaction.py` replaces common secret patterns (bearer tokens, JWTs, API keys, SAS signatures, connection-string secrets, password-style fields, private keys) and email addresses. It cannot recognize business data such as customer names, amounts, or SharePoint site and list URLs, and it does not catch a secret stored inside a nested object (for example `"secret": {"value": "..."}`). Flow logic is sent as written.

**Identifiers.** The environment ID is removed from indexed text, and so is each flow's `creator` block (tenant, object, and user IDs). Flow IDs and connector client IDs are kept. INFO-level logs do not print the environment ID or endpoint URLs.

**Credentials.** The download job uses a service principal with read access to every flow in the tenant. Its client secret lives in `.env`. Treat that secret as high privilege: give the app only the access it needs, use a short expiry, and rotate it if it may have leaked. Re-running `scripts/setup_dev_app_registration.sh` issues a new secret and invalidates the old ones.

**The HTTP API.** It serves everything in the index to anyone who can reach it. It has no user accounts, no TLS, no rate limiting, and no CORS configuration. `GET /health` needs no key. Setting `FLOWLEDGER_API_KEY` makes `POST /ask` require a matching `X-API-Key` header.

**Telemetry.** Chroma's anonymous usage telemetry is off by default. Set `ANONYMIZED_TELEMETRY=True` to opt in. FlowLedger sends no other telemetry.

## Hardening checklist

- Bind the API to `127.0.0.1` (`uvicorn api:app --host 127.0.0.1`). If you must expose it, put it behind a reverse proxy that adds TLS and authentication, and set `FLOWLEDGER_API_KEY`.
- Use a local model endpoint if flow data must not leave your machine.
- Do not share output from `LOG_LEVEL=DEBUG`. It includes endpoint URLs and environment IDs.
- Never commit `.env` or anything under `.flow-data/`. Do not attach them to issues.
- Review what you index. After changing how text is indexed, rebuild with `python jobs/reindex_local.py --rebuild`.
- Keep dependencies current. Dependabot is configured for this repository.
