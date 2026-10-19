#!/usr/bin/env python3
"""Witness-only connection faults; preserve the healthy private directory and data."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare
from tools_operator import product_env


def fault(state, kind):
    target = Path(state["private"]) / ("fault-" + kind)
    if target.exists():
        raise ValueError("Fresh fault directory required; no automatic replay")
    target.mkdir(mode=0o700)
    # Root helper explicitly bounded to original RO and new fault directory RW.
    commands = {
        "hostname": "sed -i 's/host=postgres/host=postgres-wrong/g' /fault/pg_service.conf",
        "credential": "sed -i 's/:[^:]*$/:invalid-test-credential/' /fault/pgpass",
        "database": "sed -i 's/dbname=tools/dbname=wrong_domain/g' /fault/pg_service.conf",
        "incomplete": "rm /fault/ca.crt",
        "ca": "openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=wrong-ca -keyout /fault/temp.key -out /fault/ca.crt; rm /fault/temp.key",
    }
    script = ('umask 077; cp /healthy/* /fault/; ' + commands[kind] +
              '; chown -R "$1:$2" /fault; chmod 700 /fault; find /fault -type f -exec chmod 600 {} +')
    prepare.run(["docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
                 "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                 "--cap-add", "FOWNER", "--cap-add", "DAC_OVERRIDE",
                 "--mount", f"type=bind,src={state['private']}/app,dst=/healthy,readonly",
                 "--mount", f"type=bind,src={target},dst=/fault", "--entrypoint", "bash",
                 state["operator_image"], "-euc", script, "fault", *map(str, state["app_ids"])])
    env = product_env(state, "install")
    env["PG_CLIENT_PRIVATE_DIR"] = str(target)
    args, merged = prepare.compose(state, extra=("compose.jobs.yml",), env=env)
    prepare.run(args + ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "mcp-tools"], env=merged)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("hostname", "credential", "database", "incomplete", "ca"))
    parser.add_argument("state")
    args = parser.parse_args()
    fault(prepare.load(args.state), args.kind)
