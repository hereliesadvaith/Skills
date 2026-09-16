#!/usr/bin/env python3
"""Establish an Odoo XML-RPC connection from the skill's .env file.

Usage:
    python connect.py                      # test connection, print server version + uid
    python connect.py <model> <method> ... # run execute_kw and print the JSON result

Examples:
    python connect.py res.partner search_count '[[]]'
    python connect.py res.partner search_read '[[["is_company","=",true]]]' '{"fields":["name"],"limit":5}'

Importable helper:
    from connect import get_connection
    uid, models, cfg = get_connection()
    models.execute_kw(cfg["db"], uid, cfg["password"], "res.partner", "search_count", [[]])
"""
import json
import os
import sys
import xmlrpc.client
from pathlib import Path


def load_env():
    """Load KEY=VALUE pairs from the .env sitting next to this script."""
    env_path = Path(__file__).with_name(".env")
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            cfg[key.strip()] = val.strip()
    # Environment variables override the .env file.
    return {
        "url": os.environ.get("ODOO_URL", cfg.get("ODOO_URL", "")).rstrip("/"),
        "db": os.environ.get("ODOO_DB", cfg.get("ODOO_DB", "")),
        "username": os.environ.get("ODOO_USERNAME", cfg.get("ODOO_USERNAME", "")),
        "password": os.environ.get("ODOO_PASSWORD", cfg.get("ODOO_PASSWORD", "")),
    }


def get_connection():
    """Authenticate and return (uid, object_proxy, cfg). Raises on failure."""
    cfg = load_env()
    missing = [k for k in ("url", "db", "username", "password") if not cfg[k]]
    if missing:
        raise SystemExit(f"Missing settings in .env: {', '.join(missing)}")

    common = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/common")
    uid = common.authenticate(cfg["db"], cfg["username"], cfg["password"], {})
    if not uid:
        raise SystemExit("Authentication failed — check ODOO_DB / ODOO_USERNAME / ODOO_PASSWORD.")

    models = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/object")
    return uid, models, cfg


def main(argv):
    uid, models, cfg = get_connection()

    if len(argv) < 2:
        common = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/common")
        version = common.version()
        print(f"Connected to {cfg['url']} (db: {cfg['db']})")
        print(f"Server version: {version.get('server_version', '?')}")
        print(f"Authenticated uid: {uid}")
        return

    model, method = argv[0], argv[1]
    args = json.loads(argv[2]) if len(argv) > 2 else []
    kwargs = json.loads(argv[3]) if len(argv) > 3 else {}
    result = models.execute_kw(cfg["db"], uid, cfg["password"], model, method, args, kwargs)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
