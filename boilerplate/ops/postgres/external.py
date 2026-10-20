#!/usr/bin/env python3
"""Opt-in domain preparation on an explicitly supplied external fixture.

No server installation/start/reload, no system/neighbor ACL changes, no replay.
The strict HBA subset intentionally refuses unsupported hosting policies.
"""
import argparse
import configparser
import json
from pathlib import Path
import re
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hba
import prepare
import profiles


def private_file(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Private regular input file required")
    return path


def configuration(filename):
    data = json.loads(private_file(filename).read_text())
    required = {"host", "port", "host_hostaddr", "host_port", "network", "neighbor", "provision_private"}
    if set(data) != required:
        raise ValueError("Exact external target fields required")
    profiles.endpoint(data["host"], data["port"])
    profiles.endpoint(data["host"], data["host_port"], data["host_hostaddr"])
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", data["network"]):
        raise ValueError("Explicit ordinary bridge required")
    prepare.identifier(data["neighbor"])
    directory = prepare.private_path(data["provision_private"])
    files = {name: private_file(directory / name) for name in ("pg_service.conf", "pgpass", "ca.crt")}
    service = configparser.ConfigParser(interpolation=None, strict=True)
    service.read(files["pg_service.conf"])
    expected = {"host": data["host"], "port": str(data["port"]), "dbname": "postgres",
                "sslmode": "verify-full", "sslrootcert": "/run/pg-client/ca.crt",
                "passfile": "/run/pg-client/pgpass", "connect_timeout": "3"}
    if service.defaults() or service.sections() != ["operator"]:
        raise ValueError("One provisioner service required")
    values = dict(service["operator"])
    if set(values) != set(expected) | {"user"} or any(values.get(k) != v for k, v in expected.items()):
        raise ValueError("Provisioner profile/target mismatch")
    prepare.identifier(values["user"])
    data["provision_role"] = values["user"]
    return data, {name: path.read_text() for name, path in files.items()}


def validate_network(state):
    rows = json.loads(prepare.run(["docker", "network", "inspect", state["target"]["network"]], capture=True))
    if len(rows) != 1 or rows[0]["Driver"] != "bridge" or rows[0]["Internal"] is not False:
        raise ValueError("External jobs/app require an ordinary dedicated bridge")
    args, env = prepare.compose(state)
    effective = json.loads(prepare.run(args + ["--profile", "operator", "config", "--format", "json"], env=env, capture=True))
    services = effective["services"]
    operator = services["pg-external-operator"]
    app = services[state.get("app_service", "mon-mcp")]
    network = effective["networks"]["pg-external"]
    if ("postgres" in services or "pg-net" in effective["networks"]
            or set(operator["networks"]) != {"pg-external"}
            or "pg-external" not in app["networks"]
            or network.get("name") != state["target"]["network"]
            or network.get("external") is not True):
        raise ValueError("Wrong effective external composition")
    if state.get("app_image") and app.get("image") != state["app_image"]:
        raise ValueError("External app must use the verified unchanged image")
    if state.get("operator_image") and operator.get("image") != state["operator_image"]:
        raise ValueError("External operator image mismatch")


def initialize(args):
    target, transport = configuration(args.target)
    root = Path.cwd().resolve()
    for name in ("docker-compose.yml", "docker-compose.pg-external.yml", "ops/postgres/operator.sh"):
        if not (root / name).is_file():
            raise ValueError("Autonomous copied project required")
    p = prepare.private_path(args.private, exists=False)
    if p.exists() or not p.parent.is_dir():
        raise ValueError("New private destination required")
    if not re.fullmatch(r"starter49-[a-z0-9-]{1,40}-(src|restore)", args.project):
        raise ValueError("New starter49 project required")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", args.app_service):
        raise ValueError("Literal customized app service required")
    database = prepare.identifier(args.database)
    roles = {key: prepare.identifier(value) for key, value in dict(owner=args.owner,
             app=args.app, install=args.installer, backup=args.backup, restore=args.restore).items()}
    if (len(set(roles.values())) != 5 or target["provision_role"] in roles.values()
            or "postgres" in roles.values() or database in (target["neighbor"], "postgres", "template0", "template1")):
        raise ValueError("Distinct new domain and roles required")
    state = dict(root=str(root), private=str(p), project=args.project, database=database,
                 roles=roles, target=target, mode="external", app_service=args.app_service)
    for label in ("app", "job", "operator"):
        image = prepare.inspect_image(getattr(args, label + "_image"))
        state[label + "_image"] = image
        state[label + "_ids"] = prepare.measured_ids(image, "postgres" if label == "operator" else "mcp")
    state["env"] = dict(PG_EXTERNAL_NETWORK=target["network"], PG_CLIENT_PRIVATE_DIR=str(p / "app"),
                        PG_APP_IMAGE=state["app_image"],
                        PG_OPERATOR_IMAGE=state["operator_image"], PG_DATABASE_NAME=database,
                        PG_JOB_PRIVATE_DIR=str(p / "provision"),
                        PG_JOB_UID=str(state["operator_ids"][0]), PG_JOB_GID=str(state["operator_ids"][1]),
                        **{"PG_" + key.upper() + "_ROLE": value for key, value in roles.items()})
    validate_network(state)  # RO validation before file creation or server mutation.
    p.mkdir(mode=0o700)
    prepare.write_private(p / "state.json", json.dumps(state, indent=2))
    prepare.save(state, "initialize")
    for name, db in (("provision", "postgres"), ("provision-db", database)):
        directory = p / name
        directory.mkdir(mode=0o700)
        for filename, content in transport.items():
            if filename == "pg_service.conf":
                service = configparser.ConfigParser(interpolation=None)
                service.read_string(content)
                service["operator"]["dbname"] = db
                import io
                output = io.StringIO()
                service.write(output, space_around_delimiters=False)
                content = output.getvalue()
            prepare.write_private(directory / filename, content)
        prepare.stage(state, directory, *state["operator_ids"])
    ca = transport["ca.crt"]
    for key in ("app", "install", "backup", "restore"):
        password = secrets.token_hex(32)
        role = roles[key]
        profiles.connection(p / key, ca, role, password, database, target["host"], target["port"], container=True)
        prepare.stage(state, p / key, *state["app_ids" if key == "app" else "job_ids"])
        if key == "app":
            profiles.connection(p / "app-probe-job", ca, role, password, database,
                                target["host"], target["port"], container=True)
            prepare.stage(state, p / "app-probe-job", *state["job_ids"])
        profiles.connection(p / ("host-" + key), ca, role, password, database,
                            target["host"], target["host_port"], hostaddr=target["host_hostaddr"])
        issue = p / ("issue-" + key)
        issue.mkdir(mode=0o700)
        for filename, content in transport.items():
            prepare.write_private(issue / filename, content)
        prepare.write_private(issue / "new-role.password", password)
        prepare.stage(state, issue, *state["operator_ids"])
    archive = p / "archives"
    archive.mkdir(mode=0o700)
    prepare.stage(state, archive, *state["job_ids"])
    prepare.write_private(p / "initialize.confirmed", "files-only-external-server-unchanged\n")


def check_contexts(state, roles=("app", "install", "backup", "restore")):
    script = Path(state["root"]) / "ops/postgres/connection_probe.py"
    # Host, product-job and app-image network paths are distinct observations.
    for role in roles:
        common = ["--database", state["database"], "--role", state["roles"][role],
                  "--neighbor", state["target"]["neighbor"], "--service", "operator"]
        directory = Path(state["private"]) / ("host-" + role)
        checked(prepare.run([sys.executable, str(script), "--service-file", str(directory / "pg_service.conf"),
                            "--passfile", str(directory / "pgpass"), *common], capture=True,
                           env=profiles.clean_python_env(state, role)))
        contexts = ["job"] + (["app"] if role == "app" else [])
        for context in contexts:
            mount_role = "app-probe-job" if role == "app" and context == "job" else role
            checked(prepare.run(["docker", "run", "--rm", "--network", state["target"]["network"],
                         "--memory", "512m", "--cpus", "1", "--cap-drop", "ALL",
                         "--user", ":".join(map(str, state[context + "_ids"])),
                         "--mount", f"type=bind,src={Path(state['private']) / mount_role},dst=/run/pg-client,readonly",
                         "--mount", f"type=bind,src={script.parent},dst=/ops,readonly",
                         "--entrypoint", "python", state[context + "_image"],
                         "/ops/connection_probe.py", "--service-file", "/run/pg-client/pg_service.conf",
                         "--passfile", "/run/pg-client/pgpass", *common], capture=True))


def checked(payload):
    if json.loads(payload) != {"status": "connection_context_checked", "tls": True, "denials": ["28000"] * 3}:
        raise ValueError("Connection context not confirmed")


def provision(state):
    p = Path(state["private"])
    if (p / "provision.intent").exists():
        raise ValueError("Previous dispatch: inspect, never replay")
    validate_network(state)
    snapshot = json.loads(prepare.operator(state, ["external-preflight"]))
    hba.validate(snapshot, state["database"], state["roles"], state["target"]["neighbor"])
    prepare.write_private(p / "external-preflight.json", json.dumps(snapshot))
    prepare.save(state, "provision")
    # From first CREATE onward every exception is unknown, never replayed.
    for key in ("app", "install", "backup", "restore"):
        prepare.operator(state, ["role", state["roles"][key]], new_password=key)
    prepare.operator(state, ["database-external"])
    try:
        check_contexts(state)
    except Exception:
        # Only roles whose creation was confirmed above, never the hosting account.
        prepare.operator(state, ["external-retire-all"])
        raise
    prepare.operator(state, ["controls"], database=True)
    identity = json.loads(prepare.operator(state, ["identity"], database=True))
    if identity.get("tls") is not True:
        raise ValueError("TLS identity unconfirmed")
    prepare.write_private(p / "identity.json", json.dumps(identity))
    prepare.write_private(p / "contexts.confirmed", "host-job-app-real-profile-denials28000\n")
    prepare.write_private(p / "provision.confirmed", "infrastructure-only\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--app-service", default="mon-mcp", help="name customized with the base Compose service")
    for name in ("target", "private", "project", "database", "owner", "app", "installer", "backup", "restore",
                 "app-image", "job-image", "operator-image"):
        init.add_argument("--" + name, required=True)
    sub.add_parser("provision").add_argument("--state", required=True)
    checks = sub.add_parser("check-contexts")
    checks.add_argument("--state", required=True)
    checks.add_argument("--role", choices=("app", "install", "backup", "restore"), default="app")
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize(args)
        else:
            state = prepare.load(args.state)
            if state.get("mode") != "external":
                raise ValueError("Explicit external state required")
            if args.action == "provision":
                provision(state)
            else:
                check_contexts(state, roles=(args.role,))
        print('{"status":"operator_phase_confirmed","public_qualification":false}')
    except Exception:
        print('{"status":"incomplete_or_unknown","next":"inspect; no automatic replay"}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
