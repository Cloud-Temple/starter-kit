#!/usr/bin/env python3
"""Private host connection files. No credential provider or application backend."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare


def endpoint(host, port, hostaddr=None):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}", host):
        raise ValueError("Literal hostname or address required")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid target port")
    if hostaddr is not None:
        import ipaddress
        ipaddress.ip_address(hostaddr)
    return host, port


def connection(directory, ca, role, password, database, host, port, *, hostaddr=None,
               container=False):
    endpoint(host, port, hostaddr)
    directory.mkdir(mode=0o700)
    prepare.write_private(directory / "ca.crt", ca)
    base = Path("/run/pg-client") if container else directory
    section = (f"host={host}\nport={port}\ndbname={database}\nuser={role}\n"
               f"sslmode=verify-full\nsslrootcert={base}/ca.crt\n"
               f"passfile={base}/pgpass\nconnect_timeout=3\n")
    if hostaddr is not None:
        section += f"hostaddr={hostaddr}\n"
    prepare.write_private(directory / "pg_service.conf", "".join(
        f"[{name}]\n{section}" for name in ("operator", "tools-app", "tools-install")))
    escaped_host = host.replace(":", "\\:")
    prepare.write_private(directory / "pgpass", f"{escaped_host}:{port}:{database}:{role}:{password}\n")


def python_env(state, role="app"):
    directory = Path(state["private"]) / ("host-" + role)
    prepare.private_path(str(directory))
    for name in ("pg_service.conf", "pgpass", "ca.crt"):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError("Private host files required")
    return {"PGSERVICEFILE": str(directory / "pg_service.conf"),
            "PGPASSFILE": str(directory / "pgpass"),
            "FILES_PG_CA_FILE": str(directory / "ca.crt")}


def clean_python_env(state, role="app"):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(python_env(state, role))
    return env


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--role", choices=("app", "install", "backup", "restore"), default="app")
    parser.add_argument("--run-module", help="launch the unchanged public Python module instead of printing paths")
    args = parser.parse_args()
    state = prepare.load(args.state)
    if args.run_module:
        if args.role != "app" or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]{0,120}", args.run_module):
            parser.error("Application module and app role required")
        env = clean_python_env(state)
        env["MCP_SERVER_HOST"] = "127.0.0.1"
        os.execve(sys.executable, [sys.executable, "-m", args.run_module], env)
    print(json.dumps(python_env(state, args.role)))
