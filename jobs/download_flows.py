"""Download flow definitions and run history for every flow in every environment.

Uses:
  - Power Platform admin API (BAP) to list environments.
  - Power Automate admin Flow API to list flows and get runs.
  - Power Automate Flow API, environment-scoped, to get definitions (the triggers and actions).
    The admin endpoint returns only a summary for standalone (non-solution) flows, so it is the fallback.
    Flows the app is not allowed to read at environment scope (HTTP 403) keep the summary only.

Layout written (read by flow_data.py):

    <FLOW_DEFINITIONS_PATH>/<environment id>/<flow name>_<flow id>/definition.json
    <FLOW_DEFINITIONS_PATH>/<environment id>/<flow name>_<flow id>/runs.json
    <FLOW_DEFINITIONS_PATH>/<environment id>/<flow name>_<flow id>/run_details/<run id>.json

Run details are the per-action status, inputs and outputs of a run, from the environment-scoped
Flow API. They are fetched for the most recent FLOW_RUN_DETAILS_LIMIT runs of each flow (default 25,
0 turns it off), failed runs first. Finished runs are not fetched again. If the API refuses the app
(HTTP 401/403) for a flow, its run details are skipped and the rest of the download carries on.

Requires M365_CLIENT_ID, M365_CLIENT_SECRET and M365_TENANT_ID in the environment (or .env).
"""
import json
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BAP_URL = 'https://api.bap.microsoft.com'
BAP_API_VERSION = '2023-06-01'
FLOW_URL = 'https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple'
FLOW_API_VERSION = '2016-11-01'

BAP_SCOPE = 'https://api.bap.microsoft.com/.default'
FLOW_SCOPE = 'https://service.flow.microsoft.com/.default'

DEFAULT_OUTPUT_DIR = os.path.join('.flow-data', 'flow_exports')


def get_token(scope):
    """Client-credentials token for the given scope."""
    tenant_id = os.getenv('M365_TENANT_ID')
    client_id = os.getenv('M365_CLIENT_ID')
    client_secret = os.getenv('M365_CLIENT_SECRET')
    if not all([tenant_id, client_id, client_secret]):
        raise ValueError("M365_CLIENT_ID, M365_CLIENT_SECRET and M365_TENANT_ID must be set in the environment (or .env).")

    r = requests.post(
        f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
        data={
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
            'scope': scope,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()['access_token']


def _get_all(url, headers, params=None):
    """GET a list, following @odata.nextLink or nextLink until exhausted."""
    items = []
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        items.extend(body.get('value', []))
        url = body.get('@odata.nextLink') or body.get('nextLink')
        params = None
    return items


def get_environments(bap_headers):
    url = f'{BAP_URL}/providers/Microsoft.BusinessAppPlatform/scopes/admin/environments'
    return _get_all(url, bap_headers, params={'api-version': BAP_API_VERSION})


def get_flows(env_id, flow_headers):
    url = f'{FLOW_URL}/scopes/admin/environments/{env_id}/v2/flows'
    return _get_all(url, flow_headers, params={'api-version': FLOW_API_VERSION})


def get_flow(env_id, flow_id, flow_headers):
    """Flow detail, including properties.definition (the triggers and actions) when it can be read.

    The environment-scoped endpoint is tried first: for standalone flows it returns the full definition,
    and for solution flows a much smaller payload than the admin endpoint. If it refuses the call, the
    admin endpoint is used, which returns a summary only for standalone flows.
    """
    params = {'api-version': FLOW_API_VERSION}
    try:
        r = requests.get(f'{FLOW_URL}/environments/{env_id}/flows/{flow_id}',
                         headers=flow_headers, params=params, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        logging.debug(f'  Environment-scoped read refused (HTTP {e.response.status_code}); trying the admin endpoint.')

    r = requests.get(f'{FLOW_URL}/scopes/admin/environments/{env_id}/flows/{flow_id}',
                     headers=flow_headers, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def get_runs(env_id, flow_id, flow_headers):
    url = f'{FLOW_URL}/scopes/admin/environments/{env_id}/flows/{flow_id}/runs'
    return _get_all(url, flow_headers, params={'api-version': FLOW_API_VERSION})


def get_run_actions(env_id, flow_id, run_id, flow_headers):
    """Every action of one run, with inputs and outputs filled in where the API links to them.

    Small payloads come inline as properties.inputsContent / outputsContent. Larger ones come as a
    pre-signed link (inputsLink / outputsLink), which is fetched here without the bearer token.
    """
    url = f'{FLOW_URL}/environments/{env_id}/flows/{flow_id}/runs/{run_id}/actions'
    actions = _get_all(url, flow_headers, params={'api-version': FLOW_API_VERSION})
    for action in actions:
        props = action.get('properties') or {}
        for link_key, content_key in (('inputsLink', 'inputsContent'), ('outputsLink', 'outputsContent')):
            uri = (props.get(link_key) or {}).get('uri')
            if uri and content_key not in props:
                try:
                    r = requests.get(uri, timeout=60)  # pre-signed: must not carry the bearer token
                    r.raise_for_status()
                    try:
                        props[content_key] = r.json()
                    except ValueError:
                        props[content_key] = r.text
                except requests.RequestException as e:
                    logging.debug(f'    Could not fetch {link_key} for {action.get("name")}: {e}')
    return actions


def select_runs_for_details(runs, limit):
    """The runs worth fetching details for: the `limit` most recent, failed ones first. Skips runs still going."""
    def props(r):
        return r.get('properties') or {}

    finished = [r for r in runs if props(r).get('status') not in ('Running', 'Waiting')]
    finished.sort(key=lambda r: props(r).get('startTime') or '', reverse=True)
    recent = finished[:limit]
    return sorted(recent, key=lambda r: props(r).get('status') != 'Failed')


def download_run_details(env_id, flow_id, runs, flow_headers, flow_dir, limit):
    """Write run_details/<run id>.json for selected runs. Returns the number written."""
    if limit <= 0:
        return 0
    details_dir = os.path.join(flow_dir, 'run_details')
    saved = 0
    for run in select_runs_for_details(runs, limit):
        run_id = run.get('name')
        if not run_id:
            continue
        path = os.path.join(details_dir, f'{_safe(run_id)}.json')
        if os.path.exists(path):
            continue  # a finished run does not change
        try:
            actions = get_run_actions(env_id, flow_id, run_id, flow_headers)
        except requests.HTTPError as e:
            status = e.response.status_code
            if status in (401, 403):
                logging.warning(f'    Run details refused ({status}); skipping run details for this flow.')
                break
            logging.debug(f'    Run {run_id}: could not read actions ({status}).')
            continue
        os.makedirs(details_dir, exist_ok=True)
        _write_json(path, actions)
        saved += 1
    return saved


def _safe(name):
    return re.sub(r'[^\w\-_\. ]', '_', name or 'unnamed')


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _env_label(env):
    """Environment display name for logs. The environment ID stays out of INFO output."""
    return env.get('properties', {}).get('displayName') or env.get('name', 'unknown')


def run_details_limit():
    """FLOW_RUN_DETAILS_LIMIT: runs per flow to fetch details for. Default 25, 0 turns it off."""
    raw = os.getenv('FLOW_RUN_DETAILS_LIMIT', '25')
    try:
        return max(int(raw), 0)
    except ValueError:
        logging.warning(f'FLOW_RUN_DETAILS_LIMIT={raw!r} is not a number; using 25.')
        return 25


def download_environment(env, flow_headers, out_dir):
    env_id = env['name']
    logging.info(f'--- Environment: {_env_label(env)}')
    logging.debug(f'  Environment ID: {env_id}')

    flows = get_flows(env_id, flow_headers)
    logging.info(f'  Found {len(flows)} flows.')

    for flow in flows:
        flow_id = flow['name']
        display = flow.get('properties', {}).get('displayName', flow_id)
        flow_dir = os.path.join(out_dir, _safe(env_id), f'{_safe(display)}_{flow_id}')
        os.makedirs(flow_dir, exist_ok=True)

        try:
            definition = get_flow(env_id, flow_id, flow_headers)
            _write_json(os.path.join(flow_dir, 'definition.json'), definition)
            if not (definition.get('properties') or {}).get('definition'):
                logging.warning(f'  {display}: only a summary is available. '
                                f'The app cannot read this flow\'s full definition.')
        except requests.HTTPError as e:
            logging.warning(f'  {display}: could not read definition ({e.response.status_code}).')

        try:
            runs = get_runs(env_id, flow_id, flow_headers)
            _write_json(os.path.join(flow_dir, 'runs.json'), runs)
            logging.info(f'  {display}: saved {len(runs)} runs.')
        except requests.HTTPError as e:
            logging.warning(f'  {display}: could not read runs ({e.response.status_code}).')
            runs = []

        if runs:
            saved = download_run_details(env_id, flow_id, runs, flow_headers, flow_dir, run_details_limit())
            if saved:
                logging.info(f'  {display}: saved details for {saved} runs.')

        time.sleep(0.2)  # be gentle with the API


def main():
    out_dir = os.getenv('FLOW_DEFINITIONS_PATH', DEFAULT_OUTPUT_DIR)
    os.makedirs(out_dir, exist_ok=True)

    logging.info('Acquiring tokens...')
    bap_headers = {'Authorization': f'Bearer {get_token(BAP_SCOPE)}'}
    flow_headers = {'Authorization': f'Bearer {get_token(FLOW_SCOPE)}'}

    environments = get_environments(bap_headers)
    logging.info(f'Discovered {len(environments)} environments.')

    for env in environments:
        try:
            download_environment(env, flow_headers, out_dir)
        except requests.HTTPError as e:
            logging.error(f'  Environment {_env_label(env)}: HTTP {e.response.status_code}')
            logging.debug(f'  Response body: {e.response.text[:200]}')
        except Exception as e:
            logging.error(f'  Environment {_env_label(env)}: {e}')

    logging.info(f'Download complete. Output: {out_dir}')


if __name__ == '__main__':
    import sys
    # Make the project root importable when this file is run directly.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from logging_config import configure_logging
    configure_logging()
    main()
