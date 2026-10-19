#!/usr/bin/env python3
"""Prepare ONLY the qualification derivative. Not a generator distributed with the kit."""
import argparse
from pathlib import Path
import shutil


def copy(kit, tools, destination):
    if destination.exists():
        raise ValueError("Fresh witness destination required")
    shutil.copytree(kit, destination)
    # Normal manual customization of a derivative: product source stays public Tools.
    shutil.rmtree(destination / "src")
    for name in ("src", "scripts"):
        if (destination / name).exists():
            shutil.rmtree(destination / name)
        shutil.copytree(tools / name, destination / name)
    for name in ("Dockerfile", "VERSION", "requirements.txt", "requirements.lock",
                 "requirements-postgres.txt", "requirements-postgres.lock", ".env.example"):
        shutil.copyfile(tools / name, destination / name)
    for file in ("docker-compose.yml", "docker-compose.pg-client.yml"):
        p = destination / file
        p.write_text(p.read_text().replace("mon-mcp", "mcp-tools").replace("8002", "8050"))
    caddy = destination / "waf/Caddyfile"
    caddy.write_text(caddy.read_text().replace("mon-mcp:8002", "mcp-tools:8050"))
    fixture = Path(__file__).resolve().parent
    shutil.copyfile(fixture / "compose.jobs.yml", destination / "compose.jobs.yml")
    for name in ("tools_operator.py", "product-install.sh", "qualify.py", "fault.py"):
        shutil.copyfile(fixture / name, destination / "ops/postgres" / name)
    # Public operator SQL is readonly material in the witness, not the distributed kit.
    (destination / "tools-source/docs/postgres").mkdir(parents=True)
    shutil.copyfile(tools / "docs/postgres/infrastructure.sql",
                    destination / "tools-source/docs/postgres/infrastructure.sql")
    shutil.copyfile(tools / "Dockerfile", destination / "Dockerfile.app")
    shutil.copyfile(fixture / "Dockerfile.operator", destination / "Dockerfile.operator")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("kit", "tools", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    copy(args.kit, args.tools, args.destination)
