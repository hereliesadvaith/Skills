---
name: n8n-api
description: Use this skill whenever the user wants to talk to their n8n instance over its public REST API — list/inspect/create/update/activate/deactivate/delete workflows, read execution history and errors, export or import workflow JSON, manage tags, credentials, variables, projects or users, or fire a webhook trigger. It reads N8N_URL and N8N_API_KEY from the skill's .env and runs requests through n8n.py.
---

# n8n REST API

Connect to the user's n8n instance and call its public API (`/api/v1`). All
calls go through `n8n.py` next to this file, which reads `.env`, adds the
`X-N8N-API-KEY` header, encodes query params, follows cursor pagination and
prints the JSON response.

Paths below are relative to this skill directory:
`.claude/skills/n8n-api/` (in the project). Run scripts as
`python .claude/skills/n8n-api/n8n.py ...` from the project root.

## Setup (once)

Credentials live in `.env` next to this file:

```
N8N_URL=https://n8n.your-domain.com   # no trailing slash; the script appends /api/v1
N8N_API_KEY=                          # Settings → n8n API → Create an API key
N8N_TIMEOUT=30                        # optional
```

- If `N8N_URL` is still the placeholder or `N8N_API_KEY` is empty, ask the user
  to paste them into `.env` before doing anything else.
- The API key is created in the n8n UI under **Settings → n8n API**. It is shown
  once. The public API must be enabled (`N8N_PUBLIC_API_DISABLED` not set) —
  a 404 on every `/api/v1` path usually means it is disabled.
- Environment variables with the same names override the `.env` values.
- Never print the API key in output.
- Interactive API docs live at `<N8N_URL>/api/v1/docs` if you need to check an
  endpoint's exact schema.

## Test the connection

```bash
python .claude/skills/n8n-api/n8n.py
```

Hits `/healthz`, then lists workflows with their id, name and active state.

## Explore first

```bash
python .claude/skills/n8n-api/n8n.py workflows                 # all workflows: id, name, active, tags, node count, trigger types
python .claude/skills/n8n-api/n8n.py workflows '{"active":true}'
python .claude/skills/n8n-api/n8n.py nodes <workflow-id>       # every node: name, type, disabled, credentials, parameters + connections
python .claude/skills/n8n-api/n8n.py executions '{"status":"error","limit":10}'
```

Do `nodes` before editing a workflow so node names, types and connections are
right. Workflow ids are short strings (e.g. `aBcD3fGh1jK`), not integers.

## Generic requests

```
n8n.py <METHOD> <path> [params-json] [body-json]
```

- `path` is relative to `/api/v1` (`/workflows`, `/executions`, ...).
- `params-json` is a JSON **object** of query parameters; lists join with
  commas. Pass `''` when you need a body but no params.
- `body-json` is the JSON request body.
- List responses are `{"data": [...], "nextCursor": "..."}`; `limit` max is
  250. Pass `cursor` to get the next page (the `workflows` command and
  `N8n.list_all()` do this for you).
- Errors print `HTTP <status>: message` and exit 1. `401` = bad key,
  `404` on everything = public API disabled, `400 "must NOT have additional
  properties"` = read-only fields in a PUT body (use the `update` command).

### Workflows — `/workflows`

```bash
# List (filters: active, tags "a,b", name, projectId, excludePinnedData, limit, cursor)
n8n.py GET /workflows '{"active":true,"tags":"prod","limit":50}'
n8n.py GET /workflows/<id>                       # full definition incl. nodes + connections
n8n.py GET /workflows/<id> '{"excludePinnedData":true}'

# Activate / deactivate (workflow needs a trigger node to activate)
n8n.py POST /workflows/<id>/activate
n8n.py POST /workflows/<id>/deactivate

# Create — body needs name, nodes, connections, settings
n8n.py POST /workflows '' '{
  "name":"Hello",
  "nodes":[
    {"id":"1","name":"Schedule","type":"n8n-nodes-base.scheduleTrigger","typeVersion":1.2,"position":[0,0],
     "parameters":{"rule":{"interval":[{"field":"hours","hoursInterval":1}]}}},
    {"id":"2","name":"Set","type":"n8n-nodes-base.set","typeVersion":3.4,"position":[220,0],
     "parameters":{"assignments":{"assignments":[{"id":"a","name":"msg","value":"hi","type":"string"}]}}}
  ],
  "connections":{"Schedule":{"main":[[{"node":"Set","type":"main","index":0}]]}},
  "settings":{"executionOrder":"v1"}}'

# Update — PUT replaces the whole workflow and rejects read-only fields, so use the
# helper: it GETs, merges your patch (name/nodes/connections/settings/staticData) and PUTs.
n8n.py update <id> '{"name":"Renamed"}'
n8n.py update <id> '{"settings":{"executionOrder":"v1","timezone":"Asia/Kolkata"}}'
# For node edits: `nodes <id>` → change the JSON → `update <id> '{"nodes":[...],"connections":{...}}'`
# or export → edit file → import as a new workflow.

# Export / import (import strips ids & read-only fields; creates an inactive copy)
n8n.py export <id> ./my-workflow.json
n8n.py import ./my-workflow.json "Optional new name"

# Tags on a workflow
n8n.py GET /workflows/<id>/tags
n8n.py PUT /workflows/<id>/tags '' '[{"id":"<tag-id>"}]'

# Move to another project (enterprise)   /   delete (irreversible — confirm first)
n8n.py PUT /workflows/<id>/transfer '' '{"destinationProjectId":"<project-id>"}'
n8n.py DELETE /workflows/<id>
```

Node basics: `type` is `n8n-nodes-base.<name>` (or `@n8n/n8n-nodes-langchain.<name>`),
`position` is `[x, y]`, `connections` is keyed by **source node name** →
`{"main": [[{"node": "<target name>", "type": "main", "index": 0}]]}` (outer
list = output index, inner list = targets). Node `credentials` look like
`{"httpBasicAuth": {"id": "<cred-id>", "name": "My creds"}}`. Activating requires
at least one trigger node (`*Trigger`, `webhook`, `manualTrigger` does not count).

### Executions — `/executions`

```bash
n8n.py GET /executions '{"workflowId":"<id>","status":"error","limit":20}'   # status: success|error|waiting|canceled|crashed
n8n.py GET /executions/<id> '{"includeData":true}'   # full run data per node — large; look at data.resultData
n8n.py DELETE /executions/<id>
```

There is **no public endpoint to run a workflow on demand**. To trigger one,
call its Webhook node (below), enable a Schedule trigger, or ask the user to
run it in the editor. Retrying a failed execution is also UI-only.

Reading an error: `GET /executions/<id> {"includeData":true}` →
`data.resultData.error` (message, node) and `data.resultData.lastNodeExecuted`.

### Webhook triggers — `/webhook/<path>` (outside `/api/v1`)

```bash
n8n.py webhook my-path '{"key":"value"}'          # production URL, workflow must be active
n8n.py webhook my-path '{"key":"value"}' test     # /webhook-test/ — user must click "Listen for test event" first
```

The path is the Webhook node's `path` parameter (may be a UUID). Webhook nodes
with their own auth (header/basic) need those headers — use the Python API
below with `headers=`.

### Credentials — `/credentials`

```bash
n8n.py GET /credentials/schema/<type>              # e.g. httpBasicAuth, slackApi, postgres, googleSheetsOAuth2Api
n8n.py POST /credentials '' '{"name":"Slack bot","type":"slackApi","data":{"accessToken":"xoxb-..."}}'
n8n.py DELETE /credentials/<id>
n8n.py PUT /credentials/<id>/transfer '' '{"destinationProjectId":"<project-id>"}'
```

Credentials cannot be listed or read back through the public API — only
created, deleted and transferred. Discover which a workflow uses via `nodes <id>`.

### Tags, variables, projects, users, audit

```bash
n8n.py GET /tags ; n8n.py POST /tags '' '{"name":"prod"}' ; n8n.py PUT /tags/<id> '' '{"name":"production"}' ; n8n.py DELETE /tags/<id>
n8n.py GET /variables ; n8n.py POST /variables '' '{"key":"API_BASE","value":"https://..."}' ; n8n.py PUT /variables/<id> '' '{"key":"API_BASE","value":"..."}' ; n8n.py DELETE /variables/<id>
n8n.py GET /projects ; n8n.py POST /projects '' '{"name":"Marketing"}' ; n8n.py PUT /projects/<id> '' '{"name":"Mktg"}' ; n8n.py DELETE /projects/<id>
n8n.py GET /users '{"includeRole":true}' ; n8n.py GET /users/<id-or-email>
n8n.py POST /users '' '[{"email":"new@example.com","role":"global:member"}]'   # invites; roles: global:admin | global:member
n8n.py PATCH /users/<id>/role '' '{"newRoleName":"global:admin"}' ; n8n.py DELETE /users/<id>
n8n.py POST /audit '' '{"additionalOptions":{"daysAbandonedWorkflow":90,"categories":["credentials","nodes"]}}'
n8n.py POST /source-control/pull '' '{"force":false}'
```

Variables, projects and source control need a licensed (enterprise / pro)
instance; on community edition they return `403`/`400`. User management needs
the owner's API key.

## Use it from your own Python

```python
import sys; sys.path.insert(0, ".claude/skills/n8n-api")
from n8n import N8n
nx = N8n()
wfs = nx.list_all("/workflows", active=True)                       # follows nextCursor
wf = nx.get("/workflows/abc123")
nx.update_workflow("abc123", {"name": "Renamed"})                   # GET + merge + PUT
nx.post("/workflows/abc123/activate")
errs = nx.get("/executions", params={"status": "error", "limit": 10})["data"]
nx.webhook("my-path", body={"x": 1})                                # production webhook
nx._raw(nx.base + "/webhook/secure", "POST", body={}, auth=False, headers={"X-Key": "..."})
```

## Notes

- **Writes change live automations.** Confirm with the user before
  `POST`/`PUT`/`PATCH`/`DELETE`, and especially before `activate`/`deactivate`,
  `DELETE /workflows/...` and `DELETE /credentials/...`.
- `PUT /workflows/<id>` is a full replace. Always start from the current
  definition (the `update` command does) or nodes will be lost.
- `update` bumps the workflow version; an active workflow stays active and
  picks up the new definition immediately.
- Execution `includeData` payloads can be megabytes — filter by `workflowId`
  and keep `limit` small.
- Times are ISO 8601 in UTC.
- `.env` is gitignored; `.env.example` is the committable template.
