#!/usr/bin/env python3
"""Direct PostgreSQL access to the Odoo database via the `psql` CLI.

Use this ONLY when XML-RPC (connect.py) can't return what you need, e.g. stored
columns that are access-restricted or excluded from read(). Non-stored computed
fields are NOT in the database, so SQL can't fetch those either.

Usage:
    python db.py                         # test connection, print server version
    python db.py "SELECT id, name FROM res_partner LIMIT 5"

Results print as JSON. Reads ODOO_DB_* settings from the .env next to this file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def load_db_env():
    env_path = Path(__file__).with_name(".env")
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            cfg[key.strip()] = val.strip()

    def get(key, default=""):
        return os.environ.get(key, cfg.get(key, default))

    return {
        "host": get("ODOO_DB_HOST"),
        "port": get("ODOO_DB_PORT", "5432"),
        "dbname": get("ODOO_DB_NAME") or get("ODOO_DB"),
        "user": get("ODOO_DB_USER"),
        "password": get("ODOO_DB_PASSWORD"),
    }


def run_sql(sql):
    """Run a read-only SQL query and return parsed rows (list of dicts)."""
    cfg = load_db_env()
    missing = [k for k in ("host", "dbname", "user") if not cfg[k]]
    if missing:
        raise SystemExit(
            "Missing DB settings in .env: "
            + ", ".join("ODOO_DB_" + k.upper().replace("DBNAME", "NAME") for k in missing)
        )

    # Wrap so psql emits a single JSON array regardless of the query shape.
    wrapped = f"SELECT COALESCE(json_agg(t), '[]'::json) FROM ({sql.rstrip(';')}) t;"

    env = dict(os.environ)
    if cfg["password"]:
        env["PGPASSWORD"] = cfg["password"]

    cmd = [
        "psql",
        "-h", cfg["host"],
        "-p", cfg["port"],
        "-d", cfg["dbname"],
        "-U", cfg["user"],
        "-t", "-A",           # tuples-only, unaligned
        "-v", "ON_ERROR_STOP=1",
        "-c", wrapped,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"psql error:\n{proc.stderr.strip()}")
    out = proc.stdout.strip()
    return json.loads(out) if out else []


def main(argv):
    if not argv:
        rows = run_sql("SELECT version() AS version")
        print(json.dumps(rows, indent=2, default=str))
        return
    rows = run_sql(argv[0])
    print(json.dumps(rows, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
