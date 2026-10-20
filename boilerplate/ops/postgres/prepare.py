#!/usr/bin/env python3
"""Explicit infrastructure operator. No application driver, schema or automatic replay.

All Docker calls require an operator-authorized window. --help/validation is offline.
State is written before dispatch, so interruption always preserves an uncertain phase.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys

PG_IMAGE = "postgres:17.11-trixie@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3"
BASE_FILES = ("docker-compose.yml", "docker-compose.pg-client.yml",
              "docker-compose.postgres.yml", "docker-compose.pg-operator.yml")


def validate_ttls(ca_days, server_days):
    if not (1 <= server_days <= 825 and server_days < ca_days <= 36500):
        raise ValueError("CA lifetime must exceed server lifetime (server 1..825 days, CA <=36500)")
    return ca_days, server_days


def tls_job(state, mounts, script, *args, user=None, network="none", root=False):
    command = ["docker", "run", "--rm", "--network", network, "--memory", "512m", "--cpus", "1",
               "--user", user or f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL"]
    if root:
        for cap in ("CHOWN", "FOWNER", "DAC_OVERRIDE"):
            command += ["--cap-add", cap]
    for source, target, writable in mounts:
        command += ["--mount", f"type=bind,src={source},dst={target}" + ("" if writable else ",readonly")]
    return run(command + ["--entrypoint", "bash", state["operator_image"], "-euo", "pipefail", "-c", script,
                          "tls-operation", *map(str, args)])


def wait_postgres(state, seconds):
    # Observation only: at most six PG17 readiness probes within one hard deadline.
    # No retry of signing, staging, restart, or TLS leaf comparison.
    tls_job(state, [],
            "timeout \"$1\" bash -euc 'for attempt in 1 2 3 4 5 6; do "
            'if pg_isready -h postgres -p 5432 -t 2; then exit 0; fi; '
            'if [ "$attempt" -lt 6 ]; then sleep 2; fi; '
            "done; exit 1'", seconds, network=state["project"] + "_pg-net")


def renew_server_cert(state, generation, days, readiness_seconds=30):
    validate_ttls(36500, days)
    if generation == "initial" or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", generation):
        raise ValueError("Unique bounded certificate generation required")
    if not 5 <= readiness_seconds <= 90:
        raise ValueError("Readiness deadline must be 5..90 seconds")
    p = Path(state["private"])
    for intent in p.glob("cert-renew-*.intent"):
        if not intent.with_suffix(".confirmed").is_file():
            raise ValueError("Previous renewal uncertain; reconcile before another generation")
    args, env = compose(state)
    cid = run(args + ["ps", "--quiet", "postgres"], capture=True, env=env).strip()
    if not re.fullmatch(r"[0-9a-f]{12,64}", cid):
        raise ValueError("One running owned PostgreSQL container required")
    image = run(["docker", "inspect", "--format", "{{.Image}}", cid], capture=True).strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("PostgreSQL ImageID required")
    uid, gid = measured_ids(image, "postgres")
    # Refuse an expired CA, or a requested leaf outliving it. Never rotate the CA here.
    ca_mount = [(p / "ca/ca.crt", "/ca/ca.crt", False)]
    tls_job(state, ca_mount,
            'openssl x509 -in /ca/ca.crt -checkend "$1" -noout; '
            'openssl verify -CAfile /ca/ca.crt /ca/ca.crt', days * 86400 + 86400)
    phase = "cert-renew-" + generation
    save(state, phase)
    candidate = p / phase
    candidate.mkdir(mode=0o700)
    # Readonly CA, explicit random serial, new leaf key, no CA serial-file mutation.
    tls_job(state, [(p / "ca", "/ca", False), (candidate, "/new", True)],
            'umask 077; cd /new; openssl req -newkey rsa:3072 -nodes -subj /CN=postgres '
            '-keyout server.key -out server.csr; '
            'printf "subjectAltName=DNS:postgres\\nextendedKeyUsage=serverAuth\\n" > extensions; '
            'openssl x509 -req -in server.csr -CA /ca/ca.crt -CAkey /ca/ca.key '
            '-set_serial "$2" -days "$1" -extfile extensions -out server.crt; '
            'openssl verify -verify_hostname postgres -purpose sslserver -CAfile /ca/ca.crt server.crt; '
            'cmp <(openssl x509 -in server.crt -pubkey -noout) <(openssl pkey -in server.key -pubout)',
            days, "0x" + secrets.token_hex(16))
    # The directory bind remains unchanged. Switch ONE relative symlink atomically,
    # so cert and key always select the same retained generation. Old generation stays.
    tls_job(state, [(candidate, "/new", False), (p / "server", "/server", True)],
            'umask 077; cd /server; test -L current; old=$(readlink current); '
            '[[ "$old" =~ ^generation-[a-z0-9-]+$ ]]; test -d "$old"; '
            'test ! -e "$1"; mkdir -m 700 "$1"; '
            'cp /new/server.crt /new/server.key "$1/"; '
            'chown -R "$2:$3" "$1"; chmod 600 "$1/"*; '
            'openssl verify -verify_hostname postgres -purpose sslserver -CAfile ca.crt "$1/server.crt"; '
            'printf "%s\\n" "$old" > "previous-$1"; chmod 600 "previous-$1"; '
            'ln -s "$1" ".next-$1"; mv -Tf ".next-$1" current',
            "generation-" + generation, uid, gid, user="0:0", root=True)
    run(args + ["restart", "--timeout", "15", "postgres"], env=env)
    wait_postgres(state, readiness_seconds)
    # Real fresh TLS connection is part of the future native action; no SQL/auth bypass.
    # Compare the actually served leaf to the verified candidate, not only exit status.
    tls_job(state, ca_mount + [(candidate / "server.crt", "/new/server.crt", False)],
            'timeout 20 openssl s_client -connect postgres:5432 -starttls postgres '
            '-CAfile /ca/ca.crt -verify_hostname postgres -verify_return_error </dev/null '
            '2>/dev/null | openssl x509 -outform DER > /tmp/served.der; '
            'openssl x509 -in /new/server.crt -outform DER > /tmp/expected.der; '
            'cmp /tmp/served.der /tmp/expected.der', network=state["project"] + "_pg-net")
    write_private(p / (phase + ".confirmed"), "new-leaf-served-with-verified-TLS\n")
    print('{"status":"server_certificate_renewed","ca_rotated":false}')


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("Invalid SQL identifier")
    return value


def private_path(value, *, exists=True):
    p = Path(value)
    if not p.is_absolute() or p != p.resolve() or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError("Absolute path without symlink required")
    if exists:
        st = p.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077:
            raise ValueError("Private directory (0700) required")
    return p


def run(argv, *, capture=False, env=None, timeout=120):
    # Never print argv/env/output on failure: product tools can include secrets.
    result = subprocess.run(argv, check=False, text=True, env=env, timeout=timeout,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError("Operator command failed; inspect privately, do not replay")
    return result.stdout if capture else ""


def write_private(p, content):
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


def save(state, phase):
    # Append-only dispatch journal, not a claim that the operation committed.
    write_private(Path(state["private"]) / (phase + ".intent"), "dispatch-intended\n")


def inspect_image(image):
    rows = json.loads(run(["docker", "image", "inspect", image], capture=True))
    if len(rows) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", rows[0]["Id"]):
        raise ValueError("One local ImageID required; no RepoDigest assumption")
    return rows[0]["Id"]


def measured_ids(image, account):
    result = []
    for flag in ("-u", "-g"):
        value = run(["docker", "run", "--rm", "--network", "none", "--memory", "128m",
                     "--cpus", "0.5", "--entrypoint", "id", image, flag, account], capture=True).strip()
        if not value.isdecimal() or int(value) < 1:
            raise ValueError("Non-root numeric identity required")
        result.append(int(value))
    return result


def compose(state, *, extra=(), env=None):
    args = ["docker", "compose", "--project-directory", state["root"], "-p", state["project"]]
    files = (("docker-compose.yml", "docker-compose.pg-external.yml")
             if state.get("mode") == "external" else BASE_FILES)
    if state.get("host_port") is not None:
        if (state.get("mode") == "external" or type(state["host_port"]) is not int
                or not 1024 <= state["host_port"] <= 65535
                or state["env"].get("PG_HOST_PORT") != str(state["host_port"])):
            raise ValueError("Invalid local loopback port state")
        files += ("docker-compose.postgres-host.yml",)
    for file in (*files, *extra):
        args += ["-f", str(Path(state["root"]) / file)]
    merged = dict(os.environ)
    merged.update(state["env"])
    merged.update(env or {})
    return args, merged


def operator(state, action, *, database=None, new_password=None):
    # A job sees its provisioner connection plus at most one new role password.
    conn = Path(state["private"]) / ("provision-db" if database else "provision")
    if new_password:
        # New directory per role invocation; never reuse a previous job's issuance file.
        conn = Path(state["private"]) / ("issue-" + new_password)
    env = {"PG_JOB_PRIVATE_DIR": str(conn), "PG_JOB_UID": str(state["operator_ids"][0]),
           "PG_JOB_GID": str(state["operator_ids"][1])}
    args, merged = compose(state, env=env)
    service = "pg-external-operator" if state.get("mode") == "external" else "pg-operator"
    return run(args + ["run", "--rm", "--no-deps", service, *action],
               env=merged, capture=True)


def stage(state, directory, uid, gid):
    # Explicit UID0 helper, only this newly created private directory RW.
    run(["docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
         "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "CHOWN",
         "--cap-add", "FOWNER", "--cap-add", "DAC_OVERRIDE",
         "--mount", f"type=bind,src={directory},dst=/owned",
         "--entrypoint", "bash", state["operator_image"], "-euc",
         'chown -R "$1:$2" /owned; chmod 700 /owned; find /owned -type f -exec chmod 600 {} +',
         "stage", str(uid), str(gid)])


def connection(directory, ca, role, password, database, uid, gid, state, *, new_password=None):
    directory.mkdir(mode=0o700)
    write_private(directory / "ca.crt", ca)
    write_private(directory / "pg_service.conf", "[operator]\n[tools-app]\n[tools-install]\n")
    # Each name denotes exactly this job's role; cannot select a different identity.
    section = (f"host=postgres\nport=5432\ndbname={database}\nuser={role}\n"
               "sslmode=verify-full\nsslrootcert=/run/pg-client/ca.crt\n"
               "passfile=/run/pg-client/pgpass\nconnect_timeout=3\n")
    (directory / "pg_service.conf").write_text("".join(f"[{name}]\n{section}" for name in
                                                       ("operator", "tools-app", "tools-install")))
    write_private(directory / "pgpass", f"postgres:5432:{database}:{role}:{password}\n")
    if new_password:
        write_private(directory / "new-role.password", new_password)
    stage(state, directory, uid, gid)


def initialize(args):
    ca_days, server_days = validate_ttls(args.ca_days, args.server_days)
    host_port = getattr(args, "host_port", None)
    if host_port is not None and (type(host_port) is not int or not 1024 <= host_port <= 65535):
        raise ValueError("Host port must be an integer 1024..65535")
    root = Path.cwd().resolve()
    for file in BASE_FILES:
        if not (root / file).is_file():
            raise ValueError("Run from an autonomous copied project")
    p = private_path(args.private, exists=False)
    if p.exists() or not p.parent.is_dir():
        raise ValueError("New private destination with existing parent required")
    if not re.fullmatch(r"starter(?:48|49)-[a-z0-9-]{1,40}-(src|restore)", args.project):
        raise ValueError("Unique starter48|49-<run>-src|restore project required")
    database = identifier(args.database)
    roles = {k: identifier(v) for k, v in dict(owner=args.owner, app=args.app,
             install=args.installer, backup=args.backup, restore=args.restore).items()}
    if len(set(roles.values())) != 5 or "postgres" in roles.values():
        raise ValueError("Distinct non-postgres roles required")
    app_image = inspect_image(args.app_image)
    job_image = inspect_image(args.job_image)
    operator_image = inspect_image(args.operator_image)
    pg_image = inspect_image(PG_IMAGE)
    app_ids = measured_ids(app_image, "mcp")
    job_ids = measured_ids(job_image, "mcp")
    operator_ids = measured_ids(operator_image, "postgres")
    pg_ids = measured_ids(pg_image, "postgres")
    # All validation precedes creation. No existing path, volume or database is adopted.
    p.mkdir(mode=0o700)
    state = dict(root=str(root), private=str(p), project=args.project, database=database,
                 roles=roles, app_image=app_image, job_image=job_image,
                 operator_image=operator_image, app_ids=app_ids, job_ids=job_ids,
                 operator_ids=operator_ids, pg_ids=pg_ids, ca_days=ca_days, server_days=server_days)
    state["env"] = dict(PG_PRIVATE_DIR=str(p), PG_CLIENT_PRIVATE_DIR=str(p / "app"),
                        PG_OPERATOR_IMAGE=operator_image, PG_DATABASE_NAME=database,
                        PG_JOB_PRIVATE_DIR=str(p / "provision"),
                        PG_JOB_UID=str(operator_ids[0]), PG_JOB_GID=str(operator_ids[1]),
                        **{"PG_" + k.upper() + "_ROLE": v for k, v in roles.items()})
    if host_port is not None:
        state["host_port"] = host_port
        state["env"]["PG_HOST_PORT"] = str(host_port)
    write_private(p / "state.json", json.dumps(state, indent=2))
    save(state, "initialize")
    ca_dir = p / "ca"
    ca_dir.mkdir(mode=0o700)
    run(["docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
         "--user", f"{os.getuid()}:{os.getgid()}", "--mount", f"type=bind,src={ca_dir},dst=/pki",
         "--entrypoint", "bash", operator_image, "-euc",
         'umask 077; cd /pki; openssl req -x509 -newkey rsa:3072 -nodes -days "$1" '
         '-subj /CN=standalone-ca -keyout ca.key -out ca.crt; '
         'openssl req -newkey rsa:3072 -nodes -subj /CN=postgres -keyout server.key -out server.csr; '
         'printf "subjectAltName=DNS:postgres\\nextendedKeyUsage=serverAuth\\n" > extensions; '
         'openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial '
         '-days "$2" -extfile extensions -out server.crt; '
         'openssl verify -verify_hostname postgres -purpose sslserver -CAfile ca.crt server.crt',
         "initial-tls", str(ca_days), str(server_days)])
    ca = (ca_dir / "ca.crt").read_text()
    server = p / "server"
    server.mkdir(mode=0o700)
    initial = server / "generation-initial"
    initial.mkdir(mode=0o700)
    write_private(server / "ca.crt", ca)
    for name in ("server.crt", "server.key"):
        write_private(initial / name, (ca_dir / name).read_text())
    (server / "current").symlink_to("generation-initial", target_is_directory=True)
    bootstrap = secrets.token_hex(32)
    write_private(server / "bootstrap.password", bootstrap)
    stage(state, server, *pg_ids)
    connection(p / "provision", ca, "postgres", bootstrap, "postgres", *operator_ids, state)
    connection(p / "provision-db", ca, "postgres", bootstrap, database, *operator_ids, state)
    for name, role in roles.items():
        if name == "owner":
            continue
        password = secrets.token_hex(32)
        ids = app_ids if name == "app" else job_ids
        connection(p / name, ca, role, password, database, *ids, state)
        if host_port is not None:
            # Host Python has its own private files; never chown app's container mount.
            import profiles
            profiles.connection(p / ("host-" + name), ca, role, password, database,
                                "postgres", host_port, hostaddr="127.0.0.1")
        connection(p / ("issue-" + name), ca, "postgres", bootstrap, "postgres",
                   *operator_ids, state, new_password=password)
    archive = p / "archives"
    archive.mkdir(mode=0o700)
    stage(state, archive, *job_ids)
    write_private(p / "initialize.confirmed", "files-prepared-not-product-installed\n")
    print(json.dumps({"status": "files_prepared", "state": str(p / "state.json"),
                      "next": "explicit PostgreSQL start, then provision once"}))


def load(filename):
    p = Path(filename)
    private_path(str(p.parent))
    if p.is_symlink() or p.stat().st_mode & 0o077:
        raise ValueError("Private regular state file required")
    state = json.loads(p.read_text())
    if state["root"] != str(Path.cwd().resolve()):
        raise ValueError("Wrong copied project")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="prepare NEW files; does not start PostgreSQL")
    for field in ("private", "project", "database", "owner", "app", "installer", "backup", "restore",
                  "app-image", "operator-image", "job-image"):
        init.add_argument("--" + field, required=True)
    init.add_argument("--ca-days", type=int, default=3650)
    init.add_argument("--server-days", type=int, default=365)
    init.add_argument("--host-port", type=int, help="explicit loopback-only PostgreSQL port for Python")
    renew = sub.add_parser("renew-server-cert", help="explicit new server certificate under SAME CA")
    renew.add_argument("--state", required=True)
    renew.add_argument("--generation", required=True)
    renew.add_argument("--server-days", type=int, default=365)
    renew.add_argument("--readiness-seconds", type=int, default=30)
    for action in ("start-postgres", "provision", "install-product", "identity", "stop"):
        command = sub.add_parser(action)
        command.add_argument("--state", required=True)
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize(args)
            return
        state = load(args.state)
        if state.get("mode") == "external" and args.action not in ("identity", "stop", "install-product"):
            raise ValueError("External target: use external.py, never initialize or restart its server")
        if args.action == "renew-server-cert":
            renew_server_cert(state, args.generation, args.server_days, args.readiness_seconds)
        elif args.action == "start-postgres":
            # New state only. Existing volume is never adopted by a second initialization.
            save(state, "start-postgres")
            compose_args, env = compose(state)
            existing = run(compose_args + ["ps", "--all", "--quiet", "postgres"],
                           env=env, capture=True).strip()
            volumes = run(["docker", "volume", "ls", "--quiet", "--filter",
                           "label=com.docker.compose.project=" + state["project"]], capture=True).strip()
            if existing or volumes:
                raise ValueError("Existing resources: inspect instead of initializing")
            run(compose_args + ["up", "-d", "--no-deps", "postgres"], env=env)
            print('{"status":"postgres_start_dispatched","product_installed":false}')
        elif args.action == "identity":
            print(operator(state, ["identity"]))
        elif args.action == "stop":
            compose_args, env = compose(state)
            run(compose_args + ["stop", "--timeout", "15"], env=env)
            print('{"status":"stopped","volumes":"preserved"}')
        elif args.action == "provision":
            save(state, "provision")
            for role in ("app", "install", "backup", "restore"):
                operator(state, ["role", state["roles"][role]], new_password=role)
            operator(state, ["database"])
            operator(state, ["controls"], database=True)
            proof = json.loads(operator(state, ["identity"], database=True))
            if proof.get("tls") is not True:
                raise ValueError("TLS not established")
            write_private(Path(state["private"]) / "identity.json", json.dumps(proof))
            write_private(Path(state["private"]) / "provision.confirmed", "infrastructure-only\n")
            print('{"status":"infrastructure_prepared","product_installed":false}')
        elif args.action == "install-product":
            hook = Path(state["root"]) / "ops/postgres/product-install.sh"
            if not hook.is_file() or hook.is_symlink():
                raise ValueError("Product hook required; example is inactive")
            if not (Path(state["private"]) / "provision.confirmed").is_file():
                raise ValueError("Infrastructure confirmation required")
            save(state, "product-install")
            # This hook is operator-owned executable code, reviewed with the product.
            run(["bash", str(hook), str(Path(args.state).resolve())], timeout=600)
            write_private(Path(state["private"]) / "product-install.confirmed", "hook-confirmed\n")
            print('{"status":"product_hook_confirmed","public_qualification":false}')
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError):
        print('{"status":"incomplete_or_unknown","next":"inspect authorized target; no automatic replay"}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
