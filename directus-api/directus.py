#!/usr/bin/env python3
"""Directus REST API client driven by the .env file next to this script.

Usage:
    python directus.py                                   # test connection (ping, version, current user)
    python directus.py <METHOD> <path> [params-json] [body-json]
    python directus.py collections                       # list user collections + their fields
    python directus.py fields <collection>               # list fields of one collection
    python directus.py upload <file-path> [meta-json]    # multipart upload to /files
    python directus.py download <file-id> <out-path>     # save /assets/<id> to disk

Examples:
    python directus.py GET /items/articles '{"limit":5,"fields":["id","title"],"sort":"-date_created"}'
    python directus.py GET /items/articles '{"filter":{"status":{"_eq":"published"}},"meta":"filter_count"}'
    python directus.py POST /items/articles '' '{"title":"Hello"}'
    python directus.py PATCH /items/articles/1 '' '{"title":"Renamed"}'
    python directus.py DELETE /items/articles/1

params-json is a JSON object of query parameters. Nested objects/arrays (filter,
deep, aggregate, ...) are sent as JSON strings; lists for fields/sort are joined
with commas. body-json is the JSON request body.

Importable helper:
    from directus import Directus
    dx = Directus()                       # reads .env
    rows = dx.get("/items/articles", params={"limit": 5})["data"]
    dx.post("/items/articles", body={"title": "Hi"})
"""
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SYSTEM_PREFIX = "directus_"


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
        "url": get("DIRECTUS_URL").rstrip("/"),
        "token": get("DIRECTUS_TOKEN"),
        "email": get("DIRECTUS_EMAIL"),
        "password": get("DIRECTUS_PASSWORD"),
        "timeout": int(get("DIRECTUS_TIMEOUT", "30") or 30),
    }


def _is_placeholder(value):
    return not value or "your-" in value or value.startswith("<") or "xxx" in value.lower()


# ------------------------------------------------------------------------ client
class DirectusError(Exception):
    def __init__(self, status, errors, raw=None):
        self.status = status
        self.errors = errors
        self.raw = raw
        msgs = []
        for e in errors or []:
            code = (e.get("extensions") or {}).get("code", "")
            msgs.append(f"[{code}] {e.get('message', '')}".strip())
        super().__init__(f"HTTP {status}: " + ("; ".join(msgs) if msgs else (raw or "")))


class Directus:
    """Thin REST wrapper: handles auth, query-param encoding, JSON, retries on 429."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_env()
        if _is_placeholder(self.cfg["url"]):
            raise SystemExit("DIRECTUS_URL is not set in .env — ask the user to fill it in.")
        self.base = self.cfg["url"]
        self.timeout = self.cfg["timeout"]
        self._token = None
        self._refresh = None

    # ---- auth
    def token(self):
        if self._token:
            return self._token
        if not _is_placeholder(self.cfg["token"]):
            self._token = self.cfg["token"]
            return self._token
        if not _is_placeholder(self.cfg["email"]) and self.cfg["password"]:
            data = self._raw("POST", "/auth/login", body={
                "email": self.cfg["email"], "password": self.cfg["password"], "mode": "json",
            }, auth=False)["data"]
            self._token = data["access_token"]
            self._refresh = data.get("refresh_token")
            return self._token
        raise SystemExit(
            "No credentials in .env — set DIRECTUS_TOKEN (static token) or DIRECTUS_EMAIL + DIRECTUS_PASSWORD."
        )

    def logout(self):
        """Invalidate a login session (no-op for static tokens)."""
        if self._refresh:
            try:
                self._raw("POST", "/auth/logout", body={"refresh_token": self._refresh, "mode": "json"}, auth=False)
            except Exception:
                pass
            self._refresh = None

    # ---- params
    @staticmethod
    def encode_params(params):
        """Turn a dict into a Directus-friendly query string."""
        if not params:
            return ""
        pairs = []
        for key, val in params.items():
            if val is None:
                continue
            if isinstance(val, bool):
                val = "true" if val else "false"
            elif isinstance(val, (dict,)):
                val = json.dumps(val, separators=(",", ":"))
            elif isinstance(val, (list, tuple)):
                if key in ("fields", "sort", "groupBy") or all(isinstance(v, (str, int, float)) for v in val):
                    val = ",".join(str(v) for v in val)
                else:
                    val = json.dumps(val, separators=(",", ":"))
            pairs.append((key, str(val)))
        return "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)

    # ---- http
    def _raw(self, method, path, params=None, body=None, auth=True, data=None, headers=None, retries=3):
        if not path.startswith("/"):
            path = "/" + path
        url = self.base + path + self.encode_params(params)
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        if auth:
            hdrs["Authorization"] = f"Bearer {self.token()}"
        payload = data
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
                    return {"data": raw, "status": resp.status, "content_type": ctype}
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code == 429 and attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                try:
                    parsed = json.loads(raw)
                    raise DirectusError(e.code, parsed.get("errors"), raw.decode(errors="replace")) from None
                except json.JSONDecodeError:
                    raise DirectusError(e.code, None, raw.decode(errors="replace")) from None
            except urllib.error.URLError as e:
                raise SystemExit(f"Could not reach {self.base}: {e.reason}") from None

    def request(self, method, path, params=None, body=None, **kw):
        return self._raw(method, path, params=params, body=body, **kw)

    def get(self, path, params=None):
        return self._raw("GET", path, params=params)

    def post(self, path, body=None, params=None):
        return self._raw("POST", path, params=params, body=body)

    def patch(self, path, body=None, params=None):
        return self._raw("PATCH", path, params=params, body=body)

    def delete(self, path, body=None, params=None):
        return self._raw("DELETE", path, params=params, body=body)

    # ---- convenience
    def items(self, collection, **params):
        """All items of a collection (handles pagination when limit=-1 isn't allowed)."""
        params.setdefault("limit", -1)
        return self.get(f"/items/{collection}", params=params)["data"]

    def upload(self, file_path, meta=None):
        """Multipart upload to /files. `meta` keys (title, folder, ...) go BEFORE the file part."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise SystemExit(f"File not found: {file_path}")
        boundary = "----directus" + uuid.uuid4().hex
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        parts = []
        for k, v in (meta or {}).items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v if isinstance(v, str) else json.dumps(v)}\r\n".encode()
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_path.name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n".encode() + file_path.read_bytes() + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        return self._raw("POST", "/files", data=b"".join(parts),
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def download(self, file_id, out_path, **transforms):
        """Save /assets/<id> to disk. transforms: width, height, fit, format, quality, key ..."""
        res = self._raw("GET", f"/assets/{file_id}", params=transforms or None)
        data = res["data"]
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        Path(out_path).write_bytes(data)
        return out_path


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


def cmd_test(dx):
    ping = dx._raw("GET", "/server/ping", auth=False)
    print(f"Ping {dx.base}: {ping.get('data', b'').decode() if isinstance(ping.get('data'), bytes) else ping}")
    me = dx.get("/users/me", params={"fields": ["id", "email", "first_name", "last_name", "role.name", "status"]})["data"]
    print(f"Authenticated as: {me.get('email') or me.get('id')}  role={((me.get('role') or {}).get('name'))}  status={me.get('status')}")
    try:
        info = dx.get("/server/info")["data"]
        print(f"Project: {info.get('project', {}).get('project_name', '?')}  version={info.get('version', '?')}")
    except DirectusError as e:
        print(f"(server/info not readable: {e})")
    cols = [c["collection"] for c in dx.get("/collections")["data"] if not c["collection"].startswith(SYSTEM_PREFIX)]
    print(f"User collections ({len(cols)}): {', '.join(sorted(cols)) or '(none)'}")


def cmd_collections(dx):
    cols = [c for c in dx.get("/collections")["data"] if not c["collection"].startswith(SYSTEM_PREFIX)]
    fields = dx.get("/fields")["data"]
    by_col = {}
    for f in fields:
        by_col.setdefault(f["collection"], []).append(f)
    out = []
    for c in sorted(cols, key=lambda c: c["collection"]):
        meta = c.get("meta") or {}
        out.append({
            "collection": c["collection"],
            "singleton": bool(meta.get("singleton")),
            "hidden": bool(meta.get("hidden")),
            "note": meta.get("note"),
            "fields": {
                f["field"]: f["type"] + (" (pk)" if (f.get("schema") or {}).get("is_primary_key") else "")
                for f in sorted(by_col.get(c["collection"], []), key=lambda f: (f.get("meta") or {}).get("sort") or 0)
            },
        })
    _print(out)


def cmd_fields(dx, collection):
    fields = dx.get(f"/fields/{collection}")["data"]
    out = []
    for f in sorted(fields, key=lambda f: (f.get("meta") or {}).get("sort") or 0):
        meta = f.get("meta") or {}
        schema = f.get("schema") or {}
        out.append({
            "field": f["field"],
            "type": f["type"],
            "interface": meta.get("interface"),
            "required": bool(meta.get("required")),
            "nullable": schema.get("is_nullable"),
            "default": schema.get("default_value"),
            "primary_key": bool(schema.get("is_primary_key")),
            "special": meta.get("special"),
            "note": meta.get("note"),
        })
    _print(out)


def main(argv):
    dx = Directus()
    try:
        if not argv:
            return cmd_test(dx)
        cmd = argv[0]
        if cmd == "collections":
            return cmd_collections(dx)
        if cmd == "fields":
            if len(argv) < 2:
                raise SystemExit("usage: directus.py fields <collection>")
            return cmd_fields(dx, argv[1])
        if cmd == "upload":
            if len(argv) < 2:
                raise SystemExit("usage: directus.py upload <file-path> [meta-json]")
            return _print(dx.upload(argv[1], _json_arg(argv[2] if len(argv) > 2 else None, {})))
        if cmd == "download":
            if len(argv) < 3:
                raise SystemExit("usage: directus.py download <file-id> <out-path> [transforms-json]")
            transforms = _json_arg(argv[3] if len(argv) > 3 else None, {})
            print(dx.download(argv[1], argv[2], **transforms))
            return
        method = cmd.upper()
        if method not in ("GET", "POST", "PATCH", "PUT", "DELETE", "SEARCH"):
            raise SystemExit(f"Unknown command/method: {cmd}\n\n{__doc__}")
        if len(argv) < 2:
            raise SystemExit(f"usage: directus.py {method} <path> [params-json] [body-json]")
        path = argv[1]
        params = _json_arg(argv[2] if len(argv) > 2 else None, None)
        body = _json_arg(argv[3] if len(argv) > 3 else None, None)
        _print(dx.request(method, path, params=params, body=body))
    except DirectusError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        dx.logout()


if __name__ == "__main__":
    main(sys.argv[1:])
