#!/usr/bin/env python3
"""Witness-only adapter to PUBLIC Tools procedures. No application SQL implementation."""
import argparse
import json
import os
from pathlib import Path
import sys

# This file is manually copied next to the DISTRIBUTED prepare.py in the witness.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare


def product_env(state, phase):
    proof = json.loads((Path(state["private"]) / "identity.json").read_text())
    role = "install" if phase == "install" else phase
    required = ("TEST_S3_NETWORK", "TOOLS_S3_APP_ENV", "TOOLS_S3_JOB_ENV")
    for key in required:
        if not os.environ.get(key):
            raise ValueError("Witness S3 configuration missing")
    # Distinct files per app/backup/restore; operator must provision their scopes.
    for key in ("TOOLS_S3_APP_ENV", "TOOLS_S3_JOB_ENV"):
        path = Path(os.environ[key])
        if not path.is_absolute() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("Private scoped S3 env file required")
    return dict(TOOLS_PG_SYSTEM_IDENTIFIER=proof["system_identifier"],
                TOOLS_PG_TIMELINE=str(proof["timeline"]), TOOLS_APP_IMAGE=state["app_image"],
                TOOLS_JOB_IMAGE=state["job_image"], TOOLS_JOB_ROLE=state["roles"][role],
                PG_JOB_PRIVATE_DIR=str(Path(state["private"]) / role),
                PG_JOB_UID=str(state["job_ids"][0]), PG_JOB_GID=str(state["job_ids"][1]),
                TOOLS_ARCHIVE_PARENT=str(Path(state["private"]) / "archives"))


def sql_product(state, filename=None, text=None):
    # Only a phase using the provisioner can execute the existing public product recipe.
    args, env = prepare.compose(state, env=dict(
        PG_JOB_PRIVATE_DIR=str(Path(state["private"]) / "provision-db"),
        PG_JOB_UID=str(state["operator_ids"][0]), PG_JOB_GID=str(state["operator_ids"][1])))
    if filename:
        source = Path(filename).resolve()
        args += ["run", "--rm", "--no-deps", "--volume", f"{source}:/product.sql:ro",
                 "--entrypoint", "psql", "pg-operator", "--no-psqlrc", "--set=ON_ERROR_STOP=1",
                 "--quiet", "--file", "/product.sql"]
        prepare.run(args, env=env)
    else:
        # Fixed witness grants only, not a generic SQL service.
        import subprocess
        args += ["run", "--rm", "--no-deps", "--entrypoint", "psql", "pg-operator",
                 "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--quiet"]
        result = subprocess.run(args, env=env, input=text, text=True, timeout=120,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError("Product operator phase uncertain; no replay")


def job(state, phase, arguments):
    env = product_env(state, phase)
    env["TOOLS_ARCHIVE_READ_ONLY"] = "false" if phase == "backup" else "true"
    args, merged = prepare.compose(state, extra=("compose.jobs.yml",), env=env)
    result = prepare.run(args + ["run", "--rm", "--no-deps", "tools-job", *arguments],
                         env=merged, capture=True, timeout=180)
    payload = json.loads(result)
    if payload.get("status") != "ok":
        raise RuntimeError("Product operator did not confirm the phase")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("install", "backup", "restore", "start", "resume"))
    parser.add_argument("state")
    args = parser.parse_args()
    state = prepare.load(args.state)
    if state["database"] != "tools" or state["roles"] != dict(owner="tools_files_owner",
            app="tools_files_app", install="tools_files_install", backup="tools_files_backup", restore="tools_files_restore"):
        raise ValueError("This witness adapter only handles the public Tools example names")
    bootstrap = "src.mcp_tools.storage.files_bootstrap"
    if args.phase == "install":
        job(state, "install", [bootstrap, "--apply"])
        sql_product(state, "tools-source/docs/postgres/infrastructure.sql")
        sql_product(state, text="GRANT EXECUTE ON FUNCTION tools_files.files_infrastructure() TO tools_files_install, tools_files_backup, tools_files_restore;\nGRANT USAGE ON SCHEMA tools_files TO tools_files_backup;\nGRANT SELECT ON ALL TABLES IN SCHEMA tools_files TO tools_files_backup;\n")
        job(state, "install", [bootstrap, "--check"])
        prepare.operator(state, ["retire-install"], database=True)
    elif args.phase in ("backup", "restore"):
        prepare.save(state, args.phase)
        if args.phase == "restore":
            sql_product(state, text="GRANT EXECUTE ON FUNCTION pg_control_system(), pg_control_checkpoint() TO tools_files_owner;\nGRANT pg_read_all_stats TO tools_files_owner;\n")
        # Before import, prepare provision has already granted pg_control_* and recorded
        # THIS target's system_identifier. No schema apply on an import target.
        job(state, args.phase, ["src.mcp_tools.storage.files_backup",
                              "export" if args.phase == "backup" else "import",
                              "--quiesced", "--archive", "/archives/export"])
        if args.phase == "restore":
            # Dump includes the infrastructure function. Validate its existing definition
            # through PUBLIC product checks; do not replay CREATE FUNCTION/TABLE.
            # Native pg_control grants precede import; imported probe is checked after it.
            sql_product(state, text="GRANT EXECUTE ON FUNCTION tools_files.files_infrastructure() TO tools_files_restore;\n")
            # read-only check uses the temporary restore identity, not a reinstallation.
            job(state, "restore", [bootstrap, "--operator-service", "operator", "--check"])
        prepare.operator(state, ["retire-" + args.phase], database=True)
    else:
        env = product_env(state, "install")
        arguments, merged = prepare.compose(state, extra=("compose.jobs.yml",), env=env)
        if args.phase == "start":
            prepare.run(arguments + ["up", "-d", "--no-build", "--no-deps", "mcp-tools", "waf"], env=merged)
        else:
            prepare.run(arguments + ["start", "postgres", "mcp-tools", "waf"], env=merged)
    print(json.dumps({"status": "operator_phase_confirmed", "phase": args.phase,
                      "public_qualification": False}))


if __name__ == "__main__":
    main()
