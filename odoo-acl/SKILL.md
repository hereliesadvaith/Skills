---
name: odoo-acl
description: Use this skill whenever the user wants to connect to their Odoo instance, query or modify Odoo data, or run any Odoo XML-RPC / execute_kw call. It reads credentials from the skill's .env file, authenticates over XML-RPC, and runs model methods (search, search_read, create, write, unlink, etc.).
---

# Odoo XML-RPC

Connect to the user's Odoo instance over XML-RPC and run model methods.

## Setup (once)

Credentials live in `.env` next to this file. Make sure it is filled in:

```
ODOO_URL=https://your-instance.odoo.com
ODOO_DB=your_database_name
ODOO_USERNAME=you@example.com
ODOO_PASSWORD=your_password_or_api_key
```

If any value is still a placeholder, ask the user to fill it in before continuing.
Prefer an Odoo **API key** (Settings > Account Security > New API Key) over a password.

## Test the connection

```bash
python ~/.claude/skills/odoo-acl/connect.py
```

Prints the server version and authenticated `uid`, or an error if credentials are wrong.

## Run a query

`connect.py <model> <method> <args-json> <kwargs-json>` calls `execute_kw` and prints JSON:

```bash
# Count all partners — note the domain is wrapped: [[]] means one positional arg (an empty domain)
python ~/.claude/skills/odoo-acl/connect.py res.partner search_count '[[]]'

# Read 5 companies
python ~/.claude/skills/odoo-acl/connect.py res.partner search_read \
  '[[["is_company","=",true]]]' '{"fields":["name","email"],"limit":5}'

# Create a partner
python ~/.claude/skills/odoo-acl/connect.py res.partner create '[{"name":"New Co"}]'
```

- `args-json` is the positional-args list for `execute_kw`. The domain is the **first positional arg**, so pass it wrapped: `search`/`search_read`/`search_count` need `'[[...domain...]]'` (and `'[[]]'` for "everything"), not a bare `'[]'`.
- `kwargs-json` holds `fields`, `limit`, `offset`, `order`, etc.

## Use it from your own Python

```python
import sys; sys.path.insert(0, "~/.claude/skills/odoo-acl")
from connect import get_connection
uid, models, cfg = get_connection()
partners = models.execute_kw(cfg["db"], uid, cfg["password"],
                             "res.partner", "search_read", [[]], {"fields": ["name"], "limit": 10})
```

## Fields XML-RPC can't return → direct DB (db.py)

Prefer XML-RPC (`connect.py`) for everything. Only fall back to direct SQL when a
`read`/`search_read` can't return a field — e.g. a stored column that is access-
restricted or excluded from `read()`. **Non-stored computed fields are not in the
database**, so SQL can't fetch those either; there is no way to get them but to
compute them (via XML-RPC on a record that triggers the compute).

Requires the `ODOO_DB_*` values in `.env` (host/user/password for the Postgres
instance). If they're blank, tell the user direct DB access isn't configured.

```bash
# Test the DB connection
python ~/.claude/skills/odoo-acl/db.py

# Read a restricted/stored column directly
python ~/.claude/skills/odoo-acl/db.py "SELECT id, name FROM res_partner LIMIT 5"
```

- Odoo model names map to tables by replacing `.` with `_` (`res.partner` → `res_partner`).
- `db.py` returns rows as JSON. Keep queries **read-only** (SELECT); never write via SQL —
  it bypasses Odoo's ORM, constraints, and computed-field recomputation.

## Access-control checks (Odoo 19)

For auditing `ir.model.access` / `ir.rule` on a live instance, see the **`odoo-review`** skill. The
calls it relies on all work through `connect.py`, for example:

```bash
# Model-level check for the connected user (empty id list = check the model, not records)
python ~/.claude/skills/odoo-acl/connect.py hospital.patient has_access '[[], "write"]'

# Record-level check on specific ids (raises AccessError on failure)
python ~/.claude/skills/odoo-acl/connect.py hospital.patient check_access '[[5, 6], "unlink"]'
```

`check_access_rights` / `check_access_rule` still exist but are deprecated since Odoo 18; prefer
`check_access` / `has_access`.

## Notes
- Common methods: `search`, `search_read`, `search_count`, `read`, `fields_get`, `create`, `write`, `unlink`.
- Write operations (`create`, `write`, `unlink`) change live data — confirm with the user first.
- The `.env` is gitignored territory; never print the password/API key in output.
