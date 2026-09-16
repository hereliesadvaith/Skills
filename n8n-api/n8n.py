#!/usr/bin/env python3
"""n8n public REST API client driven by the .env file next to this script.

Usage:
    python n8n.py                                        # test connection (health, workflow count)
    python n8n.py <METHOD> <path> [params-json] [body-json]
    python n8n.py workflows [params-json]                # every workflow: id, name, active, tags, node count
    python n8n.py nodes <workflow-id>                    # nodes of one workflow: name, type, disabled
    python n8n.py update <workflow-id> <patch-json>      # GET + merge + PUT (PUT needs the full body)
    python n8n.py export <workflow-id> <out.json>        # save a workflow as importable JSON
    python n8n.py import <file.json> [name]              # create a workflow from exported JSON
    python n8n.py executions [params-json]               # recent executions: id, workflow, status, times
    python n8n.py webhook <path> [body-json] [test]      # POST to /webhook/<path> (or /webhook-test/<path>)

Examples:
    python n8n.py GET /workflows '{"active":true,"limit":50}'
    python n8n.py GET /workflows/abc123
    python n8n.py POST /workflows/abc123/activate
    python n8n.py GET /executions '{"status":"error","limit":20}'
    python n8n.py update abc123 '{"name":"Renamed"}'

Paths are relative to <N8N_URL>/api/v1. params-json is a JSON object of query
parameters (lists are joined with commas). body-json is the JSON request body.

Importable helper:
    from n8n import N8n
    nx = N8n()                                   # reads .env
    wfs = nx.list_all("/workflows", active=True)  # follows nextCursor
    nx.post("/workflows/abc123/activate")
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_PREFIX = "/api/v1"
# Fields n8n returns on GET /workflows/<id> but rejects on PUT ("must NOT have additional properties").
WORKFLOW_READONLY = {
    "id", "active", "createdAt", "updatedAt", "tags", "versionId", "meta", "pinData",
    "triggerCount", "shared", "homeProject", "sharedWithProjects", "isArchived", "activeVersionId",
    "versionCounter", "activeVersion", "description", "parentFolder",
}
WORKFLOW_WRITABLE = ("name", "nodes", "connections", "settings", "staticData")


# --------------------------------------------------------------------------- env
def load_env():
    """Load KEY=VALUE pairs from the .env next to this script (env vars override)."""
    env_path = Path(__file__).with_name(".env")
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            cfg[key.strip()] = val

    def get(key, default=""):
        return os.environ.get(key, cfg.get(key, default))

    return {
        "url": get("N8N_URL").rstrip("/"),
        "api_key": get("N8N_API_KEY") or get("N8N_TOKEN"),
        "timeout": int(get("N8N_TIMEOUT", "30") or 30),
    }


def _is_placeholder(value):
    return not value or "your-" in value or value.startswith("<") or "xxx" in value.lower()


# ------------------------------------------------------------------------ client
class N8nError(Exception):
    def __init__(self, status, message, raw=None):
        self.status = status
        self.message = message
        self.raw = raw
        super().__init__(f"HTTP {status}: {message or raw or ''}")


class N8n:
    """Thin REST wrapper: adds X-N8N-API-KEY, encodes params, parses JSON, retries on 429."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_env()
        if _is_placeholder(self.cfg["url"]):
            raise SystemExit("N8N_URL is not set in .env — ask the user to fill it in.")
        if _is_placeholder(self.cfg["api_key"]):
            raise SystemExit("N8N_API_KEY is not set in .env — ask the user to paste an API key "
                             "(n8n > Settings > n8n API > Create an API key).")
        self.base = self.cfg["url"]
        self.api = self.base + API_PREFIX
        self.timeout = self.cfg["timeout"]

    # ---- params
    @staticmethod
    def encode_params(params):
        if not params:
            return ""
        pairs = []
        for key, val in params.items():
            if val is None:
                continue
            if isinstance(val, bool):
                val = "true" if val else "false"
            elif isinstance(val, dict):
                val = json.dumps(val, separators=(",", ":"))
            elif isinstance(val, (list, tuple)):
                val = ",".join(str(v) for v in val)
            pairs.append((key, str(val)))
        return "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)

    # ---- http
    def _raw(self, url, method, params=None, body=None, auth=True, headers=None, retries=3):
        url = url + self.encode_params(params)
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        if auth:
            hdrs["X-N8N-API-KEY"] = self.cfg["api_key"]
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=payload, method=method.upper(), headers=hdrs)

        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.status == 204 or not raw:
                        return {"data": None, "status": resp.status}
                    ctype = resp.headers.get("Content-Type", "")
                    if "json" in ctype:
                        return json.loads(raw)
                    return {"data": raw.decode(errors="replace"), "status": resp.status, "content_type": ctype}
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code == 429 and attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                text = raw.decode(errors="replace")
                try:
                    parsed = json.loads(raw)
                    msg = parsed.get("message") if isinstance(parsed, dict) else None
                    if isinstance(parsed, dict) and parsed.get("hint"):
                        msg = f"{msg} ({parsed['hint']})"
                    raise N8nError(e.code, msg, text) from None
                except json.JSONDecodeError:
                    raise N8nError(e.code, None, text) from None
            except urllib.error.URLError as e:
                raise SystemExit(f"Could not reach {self.base}: {e.reason}") from None

    def request(self, method, path, params=None, body=None, **kw):
        if not path.startswith("/"):
            path = "/" + path
        return self._raw(self.api + path, method, params=params, body=body, **kw)

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, body=None, params=None):
        return self.request("POST", path, params=params, body=body)

    def put(self, path, body=None, params=None):
        return self.request("PUT", path, params=params, body=body)

    def patch(self, path, body=None, params=None):
        return self.request("PATCH", path, params=params, body=body)

    def delete(self, path, params=None):
        return self.request("DELETE", path, params=params)

    # ---- convenience
    def list_all(self, path, **params):
        """Follow nextCursor until exhausted. Returns the concatenated `data` list."""
        params.setdefault("limit", 250)
        out, cursor = [], None
        while True:
            if cursor:
                params["cursor"] = cursor
            res = self.get(path, params=params)
            out.extend(res.get("data") or [])
            cursor = res.get("nextCursor")
            if not cursor:
                return out

    def health(self):
        return self._raw(self.base + "/healthz", "GET", auth=False)

    @staticmethod
    def writable_workflow(wf):
        """Strip read-only fields so the object is accepted by PUT /workflows/<id> or POST /workflows."""
        body = {k: wf[k] for k in WORKFLOW_WRITABLE if k in wf}
        body.setdefault("settings", {})
        return body

    def update_workflow(self, wf_id, patch):
        """GET the workflow, merge `patch` on top, PUT the writable subset back."""
        current = self.get(f"/workflows/{wf_id}")
        merged = {**current, **patch}
        return self.put(f"/workflows/{wf_id}", body=self.writable_workflow(merged))

    def webhook(self, path, body=None, test=False, method="POST"):
        """Call a Webhook-trigger node. Production: /webhook/<path>; editor 'Listen for test event': /webhook-test/<path>."""
        prefix = "/webhook-test/" if test else "/webhook/"
        return self._raw(self.base + prefix + path.lstrip("/"), method, body=body, auth=False)


# ---------------------------------------------------------------------------- cli
def _json_arg(s, default):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON argument: {s!r} ({e})")


def _print(obj):
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def cmd_test(nx):
    try:
        h = nx.health()
        print(f"Health {nx.base}/healthz: {h.get('status', h)}")
    except (N8nError, SystemExit) as e:
        print(f"(healthz not reachable: {e})")
    wfs = nx.list_all("/workflows", excludePinnedData=True)
    active = [w for w in wfs if w.get("active")]
    print(f"API key OK. Workflows: {len(wfs)} total, {len(active)} active")
    for w in sorted(wfs, key=lambda w: (not w.get("active"), w.get("name", "")))[:25]:
        print(f"  {'●' if w.get('active') else '○'} {w['id']}  {w.get('name')}")
    if len(wfs) > 25:
        print(f"  ... {len(wfs) - 25} more (run: n8n.py workflows)")


def cmd_workflows(nx, params):
    params = dict(params or {})
    params.setdefault("excludePinnedData", True)
    wfs = nx.list_all("/workflows", **params)
    _print([{
        "id": w["id"],
        "name": w.get("name"),
        "active": w.get("active"),
        "tags": [t.get("name") for t in (w.get("tags") or [])],
        "nodes": len(w.get("nodes") or []),
        "triggers": [n["type"].split(".")[-1] for n in (w.get("nodes") or [])
                     if "trigger" in n.get("type", "").lower() or "webhook" in n.get("type", "").lower()],
        "updatedAt": w.get("updatedAt"),
    } for w in sorted(wfs, key=lambda w: w.get("name") or "")])


def cmd_nodes(nx, wf_id):
    wf = nx.get(f"/workflows/{wf_id}", params={"excludePinnedData": True})
    print(f"{wf['id']}  {wf.get('name')}  active={wf.get('active')}")
    _print([{
        "name": n.get("name"),
        "type": n.get("type"),
        "typeVersion": n.get("typeVersion"),
        "disabled": bool(n.get("disabled")),
        "credentials": {k: v.get("name") for k, v in (n.get("credentials") or {}).items()},
        "parameters": n.get("parameters"),
    } for n in wf.get("nodes") or []])
    print("connections:")
    _print(wf.get("connections"))


def cmd_executions(nx, params):
    params = dict(params or {})
    params.setdefault("limit", 20)
    res = nx.get("/executions", params=params)
    _print([{
        "id": e["id"],
        "workflowId": e.get("workflowId"),
        "status": e.get("status"),
        "mode": e.get("mode"),
        "startedAt": e.get("startedAt"),
        "stoppedAt": e.get("stoppedAt"),
        "retryOf": e.get("retryOf"),
    } for e in res.get("data") or []])
    if res.get("nextCursor"):
        print(f'nextCursor: {res["nextCursor"]}')


def main(argv):
    nx = N8n()
    try:
        if not argv:
            return cmd_test(nx)
        cmd = argv[0]
        if cmd == "workflows":
            return cmd_workflows(nx, _json_arg(argv[1] if len(argv) > 1 else None, {}))
        if cmd == "nodes":
            if len(argv) < 2:
                raise SystemExit("usage: n8n.py nodes <workflow-id>")
            return cmd_nodes(nx, argv[1])
        if cmd == "executions":
            return cmd_executions(nx, _json_arg(argv[1] if len(argv) > 1 else None, {}))
        if cmd == "update":
            if len(argv) < 3:
                raise SystemExit("usage: n8n.py update <workflow-id> <patch-json>")
            return _print(nx.update_workflow(argv[1], _json_arg(argv[2], {})))
        if cmd == "export":
            if len(argv) < 3:
                raise SystemExit("usage: n8n.py export <workflow-id> <out.json>")
            wf = nx.get(f"/workflows/{argv[1]}")
            Path(argv[2]).write_text(json.dumps(wf, indent=2, ensure_ascii=False))
            print(f"Saved {wf.get('name')} ({wf['id']}) -> {argv[2]}")
            return
        if cmd == "import":
            if len(argv) < 2:
                raise SystemExit("usage: n8n.py import <file.json> [name]")
            wf = json.loads(Path(argv[1]).read_text())
            body = nx.writable_workflow(wf)
            if len(argv) > 2:
                body["name"] = argv[2]
            body.setdefault("name", Path(argv[1]).stem)
            body.setdefault("nodes", [])
            body.setdefault("connections", {})
            return _print(nx.post("/workflows", body=body))
        if cmd == "webhook":
            if len(argv) < 2:
                raise SystemExit("usage: n8n.py webhook <path> [body-json] [test]")
            body = _json_arg(argv[2] if len(argv) > 2 else None, None)
            test = len(argv) > 3 and argv[3].lower() == "test"
            return _print(nx.webhook(argv[1], body=body, test=test))
        method = cmd.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise SystemExit(f"Unknown command/method: {cmd}\n\n{__doc__}")
        if len(argv) < 2:
            raise SystemExit(f"usage: n8n.py {method} <path> [params-json] [body-json]")
        path = argv[1]
        params = _json_arg(argv[2] if len(argv) > 2 else None, None)
        body = _json_arg(argv[3] if len(argv) > 3 else None, None)
        _print(nx.request(method, path, params=params, body=body))
    except N8nError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
