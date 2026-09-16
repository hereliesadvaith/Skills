---
name: directus-api
description: Use this skill whenever the user wants to talk to their Directus instance over its REST API — read/create/update/delete items in a collection, inspect collections and fields, create collections/fields/relations, upload or download files, or manage users. It reads DIRECTUS_URL and the API token from the skill's .env and runs requests through directus.py.
---

# Directus REST API

Connect to the user's Directus instance and call its REST API. All calls go
through `directus.py` next to this file, which reads `.env`, adds the
`Authorization: Bearer <token>` header, encodes query params the way Directus
expects, and prints the JSON response.

Paths below are relative to this skill directory:
`.claude/skills/directus-api/` (in the project). Run scripts as
`python .claude/skills/directus-api/directus.py ...` from the project root.

## Setup (once)

Credentials live in `.env` next to this file:

```
DIRECTUS_URL=https://cms.your-domain.com   # no trailing slash
DIRECTUS_TOKEN=                            # preferred: a user's static token
DIRECTUS_EMAIL=                            # fallback: email + password login
DIRECTUS_PASSWORD=
```

- If `DIRECTUS_URL` is still the placeholder or no credential is set, ask the
  user to fill `.env` in before doing anything else.
- Prefer `DIRECTUS_TOKEN`. A static token is created in Data Studio
  (User Directory → user → *Token* → Generate) or with
  `PATCH /users/<id> {"token": "..."}`. It never expires and is shown once.
- With email/password the script logs in via `POST /auth/login` (`mode: json`)
  and logs out at the end of each run.
- Environment variables with the same names override the `.env` values.
- Never print the token or password in output.

## Test the connection

```bash
python .claude/skills/directus-api/directus.py
```

Pings the server, shows who you are authenticated as, the project name and
version, and lists the non-system collections.

## Explore the data model first

```bash
python .claude/skills/directus-api/directus.py collections          # every user collection + field:type
python .claude/skills/directus-api/directus.py fields <collection>  # type, interface, required, default, pk
```

Do this before querying or writing to a collection you have not seen yet so
field names and types are right. System collections start with `directus_`
(`directus_users`, `directus_files`, ...) and are reached through their own
endpoints (`/users`, `/files`, `/roles`, ...), not `/items/...`.

## Generic requests

```
directus.py <METHOD> <path> [params-json] [body-json]
```

- `params-json` is a JSON **object** of query parameters. Nested objects such as
  `filter`, `deep`, `aggregate`, `alias` are sent as JSON strings; lists for
  `fields`, `sort`, `groupBy` are joined with commas. Pass `''` when you need a
  body but no params.
- `body-json` is the JSON request body.
- Responses are `{"data": ...}`; errors print `HTTP <status>: [CODE] message`
  and exit 1. `429` is retried with backoff (the instance rate-limits per IP).

### Items — `/items/<collection>`

```bash
# List (default limit is 100; -1 = all)
directus.py GET /items/articles '{"fields":["id","title","author.name"],"limit":20,"sort":"-date_created"}'

# Filter + total count in meta
directus.py GET /items/articles '{"filter":{"status":{"_eq":"published"}},"meta":"filter_count"}'

# Full-text search across string fields
directus.py GET /items/articles '{"search":"directus","limit":10}'

# Aggregate / group
directus.py GET /items/orders '{"aggregate":{"sum":"amount","count":"*"},"groupBy":["status"]}'

# One item
directus.py GET /items/articles/42 '{"fields":"*.*"}'

# Create one / create many
directus.py POST /items/articles '' '{"title":"Hello","status":"draft"}'
directus.py POST /items/articles '' '[{"title":"A"},{"title":"B"}]'

# Update one / update many by key / update by query
directus.py PATCH /items/articles/42 '' '{"status":"published"}'
directus.py PATCH /items/articles '' '{"keys":[1,2,3],"data":{"status":"archived"}}'
directus.py PATCH /items/articles '' '{"query":{"filter":{"status":{"_eq":"draft"}}},"data":{"status":"archived"}}'

# Delete one / many by key / by query   (204 → prints "data": null)
directus.py DELETE /items/articles/42
directus.py DELETE /items/articles '' '[1,2,3]'
directus.py DELETE /items/articles '' '{"query":{"filter":{"status":{"_eq":"trash"}}}}'

# Singleton collections: GET/PATCH /items/<collection> return/accept a single object
directus.py GET /items/settings
```

### Query parameters (all endpoints that return lists)

| Param | Syntax | Notes |
| --- | --- | --- |
| `fields` | `["id","title","author.name","*.*"]` | dot notation walks relations; `*.*` one level deep |
| `filter` | `{"field":{"_op":value}}` | see operators below; nest for relations: `{"author":{"name":{"_eq":"x"}}}` |
| `search` | `"text"` | matches all string/text fields |
| `sort` | `["-date_created","title"]` | `-` prefix = descending |
| `limit` / `offset` / `page` | ints | `limit=-1` for everything; `page` starts at 1 |
| `meta` | `"total_count"`, `"filter_count"`, `"*"` | counts land in the `meta` key of the response |
| `aggregate` | `{"count":"*","sum":"amount","avg":"price"}` | count, countDistinct, sum, sumDistinct, avg, avgDistinct, min, max |
| `groupBy` | `["status","year(date_created)"]` | used with `aggregate` |
| `deep` | `{"comments":{"_filter":{"approved":{"_eq":true}},"_limit":5,"_sort":"-id"}}` | params for nested relational data |
| `alias` | `{"nl":"translations"}` | fetch the same relation twice with different `deep` |
| `export` | `"csv"`, `"json"`, `"xml"`, `"yaml"` | raw export, the script prints the bytes |
| `backlink` | `false` | stops `*.*` from following reverse relations |
| `version` | `"draft"` / version key | content versioning |

Functions work in any param: `year()`, `month()`, `week()`, `day()`,
`weekday()`, `hour()`, `minute()`, `second()`, `count()`, e.g.
`{"filter":{"year(date_created)":{"_eq":2026}}}`.

**Filter operators:** `_eq _neq _lt _lte _gt _gte _in _nin _null _nnull
_contains _ncontains _icontains _nicontains _starts_with _nstarts_with
_istarts_with _nistarts_with _ends_with _nends_with _iends_with _niends_with
_between _nbetween _empty _nempty _intersects _nintersects _intersects_bbox
_nintersects_bbox _regex`. Combine with `{"_and":[...]}` / `{"_or":[...]}`. For
O2M/M2M use `{"tags":{"_some":{"name":{"_eq":"x"}}}}` or `_none`. Dynamic
values: `$CURRENT_USER`, `$CURRENT_ROLE`, `$NOW`, `$NOW(-7 days)`.

### Schema — collections, fields, relations

```bash
# Create a collection (schema:{} = real table; schema:null = folder). Include an id field.
directus.py POST /collections '' '{
  "collection":"expenses",
  "meta":{"icon":"payments","note":"Team expenses","singleton":false,"sort_field":null},
  "schema":{},
  "fields":[
    {"field":"id","type":"integer","meta":{"hidden":true,"interface":"input","readonly":true},
     "schema":{"is_primary_key":true,"has_auto_increment":true}},
    {"field":"status","type":"string","meta":{"interface":"select-dropdown","width":"full",
     "options":{"choices":[{"text":"Draft","value":"draft"},{"text":"Approved","value":"approved"}]}},
     "schema":{"default_value":"draft","is_nullable":false}}
  ]}'

# Add / change / drop a field
directus.py POST  /fields/expenses '' '{"field":"amount","type":"decimal","schema":{"numeric_precision":10,"numeric_scale":2,"is_nullable":false},"meta":{"interface":"input","required":true}}'
directus.py PATCH /fields/expenses/amount '' '{"meta":{"note":"In INR"}}'
directus.py DELETE /fields/expenses/amount

# Many-to-one: create the FK field first, then the relation
directus.py POST /fields/expenses '' '{"field":"category","type":"integer","meta":{"interface":"select-dropdown-m2o","display":"related-values","display_options":{"template":"{{name}}"}},"schema":{}}'
directus.py POST /relations '' '{"collection":"expenses","field":"category","related_collection":"categories","meta":{"one_field":null},"schema":{"on_delete":"SET NULL"}}'
# One-to-many is the same relation seen from the other side: set meta.one_field to the alias
# field on the "one" collection (create that alias field with type "alias" and special ["o2m"]).

# Inspect
directus.py GET /collections/expenses
directus.py GET /relations/expenses
directus.py DELETE /collections/expenses          # drops the table AND all items — confirm first
```

Field `type` values: `string text integer bigInteger float decimal boolean
timestamp dateTime date time json csv uuid hash alias binary geometry.Point`
(and other geometry types). Common `meta.interface` values: `input`,
`input-multiline`, `input-rich-text-html`, `input-code`, `select-dropdown`,
`select-radio`, `select-multiple-checkbox`, `boolean`, `datetime`, `tags`,
`file`, `file-image`, `files`, `select-dropdown-m2o`, `list-o2m`, `list-m2m`.
`meta.special` marks behaviour: `["uuid"]`, `["date-created"]`,
`["date-updated"]`, `["user-created"]`, `["cast-boolean"]`, `["cast-json"]`,
`["file"]`, `["m2o"]`, `["o2m"]`, `["m2m"]`.

### Files — `/files` and `/assets`

```bash
directus.py GET /files '{"fields":["id","title","filename_download","type","filesize"],"limit":20}'
directus.py upload ./report.pdf '{"title":"Q3 report","folder":"<folder-uuid>"}'   # multipart; meta before file
directus.py POST /files/import '' '{"url":"https://example.com/pic.jpg","data":{"title":"Pic"}}'
directus.py PATCH /files/<id> '' '{"title":"Renamed"}'
directus.py download <id> ./out.jpg                                   # original
directus.py download <id> ./thumb.webp '{"width":300,"height":300,"fit":"cover","format":"webp","quality":80}'
directus.py DELETE /files/<id>
```

`/assets/<id>` params: `width`, `height`, `fit` (cover|contain|inside|outside),
`format` (jpg|png|webp|avif|tiff|auto), `quality`, `withoutEnlargement`, `key`
(preset from Settings), `transforms` (JSON array of sharp ops), `download`.

### Users, roles, misc

```bash
directus.py GET /users/me '{"fields":["id","email","role.name","policies.policy.name"]}'
directus.py GET /users '{"fields":["id","email","status","role.name"]}'
directus.py POST /users '' '{"email":"new@example.com","password":"...","role":"<role-uuid>"}'
directus.py POST /users/invite '' '{"email":"new@example.com","role":"<role-uuid>"}'
directus.py GET /roles ; directus.py GET /policies ; directus.py GET /permissions
directus.py GET /server/info ; directus.py GET /server/health ; directus.py GET /server/specs/oas
directus.py GET /activity '{"sort":"-timestamp","limit":20}'
directus.py GET /revisions '{"filter":{"collection":{"_eq":"articles"}},"limit":20}'
directus.py GET /flows ; directus.py POST /flows/trigger/<flow-id> '' '{"key":"value"}'
directus.py POST /utils/random/string '{"length":32}'
```

## Use it from your own Python

```python
import sys; sys.path.insert(0, ".claude/skills/directus-api")
from directus import Directus
dx = Directus()
rows = dx.get("/items/articles", params={"filter": {"status": {"_eq": "published"}}, "limit": -1})["data"]
dx.post("/items/articles", body={"title": "Hi"})
dx.patch("/items/articles/1", body={"title": "Renamed"})
dx.delete("/items/articles", body=[1, 2])
dx.upload("photo.png", {"title": "Photo"})
```

## Notes

- **Writes change live data.** Confirm with the user before `POST`/`PATCH`/`DELETE`,
  especially `DELETE /collections/...`, delete-by-query, and update-by-query.
- Permissions are those of the token's user. A `FORBIDDEN` error or an empty
  list on a collection that exists usually means the role lacks access, not
  that the data is missing.
- Default `limit` is 100 and the instance may cap `limit=-1`; page with
  `limit` + `offset` for large collections.
- `GET /items/<collection>` on a singleton returns an object, not an array.
- Responses embed relations only when asked via `fields`; otherwise M2O fields
  come back as raw keys.
- `.env` is gitignored; `.env.example` is the committable template.
