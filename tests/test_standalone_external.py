"""#49: profile/composition boundaries, HBA policy, real protocol parsing without I/O."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get("STARTER49_SOURCE", Path(__file__).resolve().parents[1]))
OPS = ROOT / "boilerplate/ops/postgres"


def load(name):
    sys.path.insert(0, str(OPS))
    spec = importlib.util.spec_from_file_location("starter49_" + name, OPS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_host_exposure_is_explicit_loopback_only():
    import yaml
    value = yaml.safe_load((ROOT / "boilerplate/docker-compose.postgres-host.yml").read_text())
    assert value["services"]["postgres"]["ports"] == ["127.0.0.1:${PG_HOST_PORT:?validated loopback port}:5432"]
    local = yaml.safe_load((ROOT / "boilerplate/docker-compose.postgres.yml").read_text())
    assert "ports" not in local["services"]["postgres"]


def test_external_jobs_and_app_have_route_without_local_postgres():
    import yaml
    value = yaml.safe_load((ROOT / "boilerplate/docker-compose.pg-external.yml").read_text())
    assert "postgres" not in value["services"]
    assert value["services"]["mon-mcp"]["networks"] == ["pg-external"]
    assert value["services"]["pg-external-operator"]["networks"] == ["pg-external"]
    assert "pg-net" not in value["networks"]


def test_python_profile_uses_host_paths_and_port_without_pg_environment_leak(tmp_path, monkeypatch):
    profiles = load("profiles")
    directory = tmp_path / "host-app"
    profiles.connection(directory, "CA", "tools_app", "s3cret", "tools", "postgres", 15432, hostaddr="127.0.0.1")
    assert "postgres:15432:tools:tools_app:s3cret" in (directory / "pgpass").read_text()
    assert "hostaddr=127.0.0.1" in (directory / "pg_service.conf").read_text()
    assert f"sslrootcert={directory}/ca.crt" in (directory / "pg_service.conf").read_text()
    monkeypatch.setenv("PGSERVICE", "forbidden")
    monkeypatch.setenv("PGOPTIONS", "forbidden")
    monkeypatch.setenv("PG_HOST_PORT", "15432")
    env = profiles.clean_python_env({"private": str(tmp_path)})
    assert {name for name in env if name.startswith("PG")} == {"PGSERVICEFILE", "PGPASSFILE"}
    assert env["FILES_PG_CA_FILE"] == str(directory / "ca.crt")
    for path in directory.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("port", [0, 1023, 65536, True, "15432"])
def test_bad_host_port_refuses_before_any_docker(port, monkeypatch):
    op = load("prepare")
    monkeypatch.setattr(op, "run", lambda *a, **k: pytest.fail("No Docker"))
    with pytest.raises(ValueError, match="Host port"):
        op.initialize(SimpleNamespace(ca_days=3650, server_days=365, host_port=port))


def test_external_composition_never_includes_local_pg_files():
    op = load("prepare")
    args, _ = op.compose(dict(root="/copy", project="starter49-test-src", env={}, mode="external"))
    assert "/copy/docker-compose.pg-external.yml" in args
    assert not any(name in " ".join(args) for name in ("postgres.yml", "pg-client.yml", "pg-operator.yml"))


ROLES = dict(owner="domain_owner", app="domain_app", install="domain_install", backup="domain_backup", restore="domain_restore")


def snapshot():
    lines = ["local all all reject"]
    for family in ("0.0.0.0/0", "::/0"):
        lines += ["hostssl domain all " + family + " scram-sha-256", "host all all " + family + " reject"]
    rows = []
    for number, line in enumerate(lines, 1):
        fields = line.split()
        rows.append(dict(line_number=number, file_name="/hba", error=None,
                         type=fields[0], database=fields[1].split(","), user_name=fields[2].split(","),
                         auth_method=fields[-1], address=None if fields[0] == "local" else fields[3].split("/")[0],
                         netmask=None if fields[0] == "local" else ("::" if ":" in fields[3] else "0.0.0.0")))
    return dict(roles=["postgres", "neighbor_app"], databases=["postgres", "template0", "template1", "neighbor"],
                hba=rows, hba_text="\n".join(lines), hba_file="/hba", acl=[], superuser=True, server_version_num=170011)


def test_closed_hba_all_four_future_login_roles():
    load("hba").validate(snapshot(), "domain", ROLES, "neighbor")


@pytest.mark.parametrize("line", [
    'host all all 0.0.0.0/0 scram-sha-256',
    'host all +domain_owner 0.0.0.0/0 reject',
    'host all /domain_.* 0.0.0.0/0 reject',
    'host all @users 0.0.0.0/0 reject',
    'host samegroup all 0.0.0.0/0 reject',
    'host "all" all 0.0.0.0/0 reject',
    'host all all localhost reject',
    'host all all 127.0.0.1/32 reject',
    'host all all 0.0.0.0/0 trust',
])
def test_hba_ambiguous_or_prior_authorization_is_refused(line):
    data = snapshot()
    data["hba_text"] = line + "\n" + data["hba_text"]
    fields = line.split()
    for row in data["hba"]:
        row["line_number"] += 1
    data["hba"].insert(0, dict(line_number=1, file_name="/hba", error=None, type=fields[0],
                             database=[fields[1]], user_name=[fields[2]], auth_method=fields[-1],
                             address="0.0.0.0", netmask="0.0.0.0"))
    with pytest.raises(ValueError):
        load("hba").validate(data, "domain", ROLES, "neighbor")


@pytest.mark.parametrize("change", ["role_collision", "database_collision", "hba_error", "include", "missing_ipv6", "not_super"])
def test_hba_missing_precondition_never_allows_ddl(change):
    data = snapshot()
    if change == "role_collision":
        data["roles"].append(ROLES["app"])
    elif change == "database_collision":
        data["databases"].append("domain")
    elif change == "hba_error":
        data["hba"][0]["error"] = "invalid"
    elif change == "include":
        data["hba"][0]["file_name"] = "/included"
    elif change == "missing_ipv6":
        data["hba"] = data["hba"][:-2]
    else:
        data["superuser"] = False
    with pytest.raises(ValueError):
        load("hba").validate(data, "domain", ROLES, "neighbor")


class Stream:
    def __init__(self, data, fragment=1):
        self.data = data
        self.fragment = fragment
        self.sent = []

    def recv(self, size):
        size = min(size, self.fragment)
        value, self.data = self.data[:size], self.data[size:]
        return value

    def sendall(self, value):
        self.sent.append(value)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def error(payload=b"SFATAL\0C28000\0Mnot-published\0\0", kind=b"E"):
    return kind + struct.pack("!I", len(payload) + 4) + payload


@pytest.mark.parametrize("fragment", [1, 2, 7, 64])
def test_real_error_frame_fragmented_is_28000(fragment):
    assert load("connection_probe").error_code(Stream(error(), fragment)) == "28000"


@pytest.mark.parametrize("frame", [
    error(b"C28P01\0\0"), error(b"C42501\0\0"), error(b"C28000\0C28000\0\0"),
    error(b"C28000\0\0garbage"), error(b"M28000\0\0"), error(b"C28000"),
    b"R" + struct.pack("!II", 8, 0), b"E" + struct.pack("!I", 1000000),
    error()[:-1], b"", error(b"Z28000\0\0"),
])
def test_hostile_auth_or_code_never_proves_rejection(frame):
    with pytest.raises(ValueError):
        load("connection_probe").error_code(Stream(frame))


def test_probe_uses_same_ca_hostname_hostaddr_and_never_sends_password(monkeypatch):
    probe = load("connection_probe")
    raw, verified = Stream(b"S"), Stream(error())
    events = []
    def create(address, timeout):
        events.append(("tcp", address, timeout))
        return raw
    class Context:
        def wrap_socket(self, stream, server_hostname):
            events.append(("tls", server_hostname, self.check_hostname, self.verify_mode))
            assert stream is raw
            return verified
    def context(cafile):
        events.append(("ca", cafile))
        return Context()
    monkeypatch.setattr(probe.socket, "create_connection", create)
    monkeypatch.setattr(probe.ssl, "create_default_context", context)
    values = dict(host="postgres", hostaddr="127.0.0.1", port="15432", user="domain_app",
                  sslrootcert="/private/ca.crt", connect_timeout="3")
    assert probe.rejected(values, "neighbor") == "28000"
    assert events[0] == ("ca", "/private/ca.crt")
    assert events[1] == ("tcp", ("127.0.0.1", 15432), 3)
    assert events[2][1:3] == ("postgres", True)
    assert raw.sent == [struct.pack("!II", 8, 80877103)]
    assert verified.sent == [probe.startup("domain_app", "neighbor")]


@pytest.mark.parametrize("bad", [b"N", b"E", b"", socket.timeout(), OSError()])
def test_no_tls_timeout_or_io_is_not_a_denial(monkeypatch, bad):
    probe = load("connection_probe")
    def create(*a, **k):
        if isinstance(bad, Exception):
            raise bad
        return Stream(bad)
    monkeypatch.setattr(probe.socket, "create_connection", create)
    monkeypatch.setattr(probe.ssl, "create_default_context", lambda **k: SimpleNamespace())
    with pytest.raises((ValueError, OSError)):
        probe.rejected(dict(host="postgres", port="5432", user="app", sslrootcert="/ca", connect_timeout="3"), "neighbor")


@pytest.mark.parametrize("value", ["a\0b", "bad-name", "x" * 64, "", "App"])
def test_startup_identity_cannot_inject_parameters(value):
    with pytest.raises(ValueError):
        load("connection_probe").startup(value, "neighbor")


def test_external_preflight_rejects_before_any_mutation(tmp_path, monkeypatch):
    external = load("external")
    calls = []
    monkeypatch.setattr(external, "validate_network", lambda _: None)
    def operator(state, argv, **kwargs):
        calls.append(argv)
        return json.dumps({**snapshot(), "roles": [ROLES["app"]]})
    monkeypatch.setattr(external.prepare, "operator", operator)
    with pytest.raises(ValueError):
        external.provision(dict(private=str(tmp_path), roles=ROLES, database="domain", target={"neighbor": "neighbor"}))
    assert calls == [["external-preflight"]]
    assert not (tmp_path / "provision.intent").exists()


def test_external_role_dispatch_uncertain_never_replayed(tmp_path, monkeypatch):
    external = load("external")
    calls = []
    monkeypatch.setattr(external, "validate_network", lambda _: None)
    def operator(state, argv, **kwargs):
        calls.append(argv)
        if argv == ["external-preflight"]:
            return json.dumps(snapshot())
        raise RuntimeError("ACK lost")
    monkeypatch.setattr(external.prepare, "operator", operator)
    state = dict(private=str(tmp_path), roles=ROLES, database="domain", target={"neighbor": "neighbor"})
    with pytest.raises(RuntimeError):
        external.provision(state)
    first = copy.deepcopy(calls)
    with pytest.raises(ValueError, match="never replay"):
        external.provision(state)
    assert calls == first
    assert (tmp_path / "provision.intent").exists()
    assert not (tmp_path / "provision.confirmed").exists()


def test_failed_real_contexts_close_only_confirmed_case_roles(tmp_path, monkeypatch):
    external = load("external")
    calls = []
    monkeypatch.setattr(external, "validate_network", lambda _: None)
    monkeypatch.setattr(external, "check_contexts", lambda _: (_ for _ in ()).throw(ValueError("not28000")))
    def operator(state, argv, **kwargs):
        calls.append(argv)
        return json.dumps(snapshot()) if argv == ["external-preflight"] else ""
    monkeypatch.setattr(external.prepare, "operator", operator)
    state = dict(private=str(tmp_path), roles=ROLES, database="domain", target={"neighbor": "neighbor"})
    with pytest.raises(ValueError):
        external.provision(state)
    assert calls[-1] == ["external-retire-all"]
    assert not any(argv == ["controls"] for argv in calls)
    assert not (tmp_path / "provision.confirmed").exists()


def test_external_database_sql_has_no_system_or_neighbor_revoke():
    script = (OPS / "operator.sh").read_text()
    branch = script.split("  database-external)", 1)[1].split("    ;;", 1)[0]
    assert 'REVOKE ALL ON DATABASE :"db" FROM PUBLIC' in branch
    sql = branch.split("<<'SQL'", 1)[1]
    assert "template1" not in sql and "neighbor" not in sql and "ON DATABASE postgres" not in sql


def test_effective_external_network_is_checked_for_app_and_operator(monkeypatch):
    external = load("external")
    state = dict(root="/copy", private="/private", project="starter49-net-src", env={}, mode="external", target={"network": "case-net"})
    network = dict(Driver="bridge", Internal=False)
    config = dict(services={"mon-mcp": {"networks": {"mcp-net": {}, "pg-external": {}}},
                           "pg-external-operator": {"networks": {"pg-external": {}}}},
                  networks={"mcp-net": {}, "pg-external": {"external": True, "name": "case-net"}})
    monkeypatch.setattr(external.prepare, "run", lambda argv, **kwargs: json.dumps([network] if "inspect" in argv else config))
    external.validate_network(state)
    del config["services"]["mon-mcp"]["networks"]["pg-external"]
    with pytest.raises(ValueError):
        external.validate_network(state)


@pytest.mark.parametrize("fault", ["internal", "host", "operator_extra", "postgres", "old_pg_net"])
def test_external_composition_faults_refuse(fault, monkeypatch):
    external = load("external")
    network = dict(Driver="bridge", Internal=False)
    config = dict(services={"mon-mcp": {"networks": {"pg-external": {}}},
                           "pg-external-operator": {"networks": {"pg-external": {}}}},
                  networks={"pg-external": {"external": True, "name": "case-net"}})
    if fault == "internal":
        network["Internal"] = True
    elif fault == "host":
        network["Driver"] = "host"
    elif fault == "operator_extra":
        config["services"]["pg-external-operator"]["networks"]["pg-net"] = {}
    elif fault == "postgres":
        config["services"]["postgres"] = {}
    else:
        config["networks"]["pg-net"] = {}
    monkeypatch.setattr(external.prepare, "run", lambda argv, **kwargs: json.dumps([network] if "inspect" in argv else config))
    with pytest.raises(ValueError):
        external.validate_network(dict(root="/copy", project="p", env={}, mode="external", target={"network": "case-net"}))


def test_external_files_are_new_scoped_profiles_not_a_server_or_ca(tmp_path, monkeypatch):
    external = load("external")
    copy = tmp_path / "copy"
    import shutil
    shutil.copytree(ROOT / "boilerplate", copy)
    monkeypatch.chdir(copy)
    transport = tmp_path / "authorized-provisioner"
    transport.mkdir(mode=0o700)
    for name, value in {
        "ca.crt": "EXISTING PUBLIC CA",
        "pgpass": "fixture:5432:*:postgres:authorized-private-password\n",
        "pg_service.conf": "[operator]\nhost=fixture\nport=5432\ndbname=postgres\nuser=postgres\nsslmode=verify-full\nsslrootcert=/run/pg-client/ca.crt\npassfile=/run/pg-client/pgpass\nconnect_timeout=3\n",
    }.items():
        external.prepare.write_private(transport / name, value)
    config = tmp_path / "target.json"
    external.prepare.write_private(config, json.dumps(dict(host="fixture", port=5432, host_hostaddr="127.0.0.1",
        host_port=15432, network="case-net", neighbor="neighbor", provision_private=str(transport))))
    staged = []
    monkeypatch.setattr(external.prepare, "inspect_image", lambda image: "sha256:" + {"app": "a", "job": "b", "op": "c"}[image] * 64)
    monkeypatch.setattr(external.prepare, "measured_ids", lambda image, account: (1001, 1002) if image.endswith("a" * 64) else (2001, 2002))
    monkeypatch.setattr(external.prepare, "stage", lambda state, directory, uid, gid: staged.append((directory.name, uid, gid)))
    monkeypatch.setattr(external, "validate_network", lambda _: None)
    monkeypatch.setattr(external.prepare, "run", lambda *a, **k: pytest.fail("No server command during file preparation"))
    private = tmp_path / "new-private"
    args = SimpleNamespace(target=str(config), private=str(private), project="starter49-files-src", database="domain",
                           owner="domain_owner", app="domain_app", installer="domain_install", backup="domain_backup",
                           restore="domain_restore", app_image="app", job_image="job", operator_image="op", app_service="mon-mcp")
    external.initialize(args)
    state = json.loads((private / "state.json").read_text())
    assert state["mode"] == "external" and "PG_PRIVATE_DIR" not in state["env"]
    assert not (private / "server").exists() and not (private / "ca").exists()
    assert (private / "host-app/ca.crt").read_text() == "EXISTING PUBLIC CA"
    assert "fixture:15432:domain:domain_app:" in (private / "host-app/pgpass").read_text()
    assert ("app", 1001, 1002) in staged and ("app-probe-job", 2001, 2002) in staged
    for name in ("app", "install", "backup", "restore"):
        assert "postgres" not in (private / name / "pgpass").read_text()
    with pytest.raises(ValueError, match="New private"):
        external.initialize(args)


def test_three_contexts_and_each_auxiliary_use_only_their_profile(tmp_path, monkeypatch):
    external = load("external")
    calls = []
    monkeypatch.setattr(external.profiles, "clean_python_env", lambda *a: {})
    def command(argv, **kwargs):
        calls.append(argv)
        return json.dumps(dict(status="connection_context_checked", tls=True, denials=["28000"] * 3))
    monkeypatch.setattr(external.prepare, "run", command)
    state = dict(private=str(tmp_path), root="/copy", database="domain", roles=ROLES,
                 target={"network": "case-net", "neighbor": "neighbor"},
                 app_ids=(111, 112), job_ids=(211, 212), app_image="appimage", job_image="jobimage")
    external.check_contexts(state)
    assert len(calls) == 9
    app, = [argv for argv in calls if "appimage" in argv]
    assert app[app.index("--user") + 1] == "111:112"
    assert any("src=" + str(tmp_path / "app") + "," in word for word in app)
    jobapp, = [argv for argv in calls if "jobimage" in argv and ROLES["app"] in argv]
    assert any("app-probe-job" in word for word in jobapp)
    assert jobapp[jobapp.index("--user") + 1] == "211:212"
    assert not any("provision" in word or "server.key" in word for argv in calls for word in argv)


def test_complete_provision_checks_contexts_before_privileged_product_probes(tmp_path, monkeypatch):
    external = load("external")
    events = []
    monkeypatch.setattr(external, "validate_network", lambda _: events.append("network"))
    monkeypatch.setattr(external, "check_contexts", lambda _: events.append("contexts"))
    def operator(state, argv, **kwargs):
        events.append(argv[0])
        if argv[0] == "external-preflight":
            return json.dumps(snapshot())
        if argv[0] == "identity":
            return json.dumps(dict(system_identifier="123", timeline=1, tls=True))
        return ""
    monkeypatch.setattr(external.prepare, "operator", operator)
    external.provision(dict(private=str(tmp_path), roles=ROLES, database="domain", target={"neighbor": "neighbor"}))
    assert events == ["network", "external-preflight", "role", "role", "role", "role", "database-external", "contexts", "controls", "identity"]
    assert (tmp_path / "contexts.confirmed").is_file() and (tmp_path / "provision.confirmed").is_file()


def test_wrong_server_major_refused_before_role_creation():
    data = snapshot()
    data["server_version_num"] = 160011
    with pytest.raises(ValueError, match="PostgreSQL17"):
        load("hba").validate(data, "domain", ROLES, "neighbor")


def test_unsupported_client_cert_never_silently_omitted(tmp_path):
    profiles = load("profiles")
    directory = tmp_path / "profile"
    profiles.connection(directory, "CA", "app", "password", "domain", "fixture", 5432)
    file = directory / "pg_service.conf"
    file.write_text(file.read_text() + "sslcert=/client.crt\n")
    with pytest.raises(ValueError, match="partial TLS projection"):
        load("connection_probe").profile(str(file), "tools-install")


def test_connection_probe_never_publishes_free_error_text(monkeypatch, capsys):
    probe = load("connection_probe")
    monkeypatch.setattr(sys, "argv", ["probe", "--service-file", "/file", "--passfile", "/pass", "--service", "operator",
                                      "--database", "domain", "--role", "app", "--neighbor", "neighbor"])
    monkeypatch.setattr(probe, "check", lambda *a: (_ for _ in ()).throw(RuntimeError("PRIVATE RESPONSE password=secret")))
    with pytest.raises(SystemExit):
        probe.main()
    output = capsys.readouterr()
    assert "PRIVATE" not in output.err and "secret" not in output.err
    assert json.loads(output.err) == {"status": "connection_context_unconfirmed"}


def test_authenticated_domain_control_precedes_raw_denials_with_exact_identity(tmp_path, monkeypatch):
    probe, profiles = load("connection_probe"), load("profiles")
    directory = tmp_path / "host-app"
    profiles.connection(directory, "CA", "domain_app", "secret", "domain", "fixture", 5432)
    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            calls.append("identity-control")
            return SimpleNamespace(fetchone=lambda: ("domain_app", "domain", True, 170011))
    def connect(**kwargs):
        calls.append(kwargs)
        assert os.environ["PGSERVICEFILE"] == str(directory / "pg_service.conf")
        assert os.environ["PGPASSFILE"] == str(directory / "pgpass")
        return Connection()
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setattr(probe.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(probe, "rejected", lambda values, database: calls.append(database) or "28000")
    monkeypatch.setattr(probe.os, "environ", dict(os.environ, PGHOST="wrong"))
    result = probe.check(str(directory / "pg_service.conf"), str(directory / "pgpass"), "tools-app", "domain", "domain_app", "neighbor")
    assert calls[0]["sslmode"] == "verify-full" and calls[0]["service"] == "tools-app"
    assert calls[1:] == ["identity-control", "neighbor", "postgres", "template1"]
    assert result["denials"] == ["28000"] * 3
    assert "PGHOST" not in probe.os.environ


def test_extra_protocol_message_after_error_is_not_accepted(monkeypatch):
    probe = load("connection_probe")
    raw, verified = Stream(b"S"), Stream(error() + b"R")
    context = SimpleNamespace(wrap_socket=lambda *a, **k: verified)
    monkeypatch.setattr(probe.socket, "create_connection", lambda *a, **k: raw)
    monkeypatch.setattr(probe.ssl, "create_default_context", lambda **k: context)
    with pytest.raises(ValueError, match="one ErrorResponse"):
        probe.rejected(dict(host="fixture", port="5432", user="app", sslrootcert="/ca", connect_timeout="3"), "neighbor")


def test_hba_source_and_view_address_disagreement_is_not_a_policy():
    data = snapshot()
    data["hba"][1]["address"] = "127.0.0.1"
    with pytest.raises(ValueError, match="mismatch"):
        load("hba").validate(data, "domain", ROLES, "neighbor")


@pytest.mark.parametrize("failure", ["certificate", "timeout", "io"])
def test_tls_failure_never_sends_startup_or_password(monkeypatch, failure):
    import ssl
    probe = load("connection_probe")
    raw = Stream(b"S")
    error_type = {"certificate": ssl.SSLCertVerificationError, "timeout": socket.timeout, "io": OSError}[failure]
    def wrap(*args, **kwargs):
        raise error_type("not published")
    context = SimpleNamespace(wrap_socket=wrap)
    monkeypatch.setattr(probe.socket, "create_connection", lambda *a, **k: raw)
    monkeypatch.setattr(probe.ssl, "create_default_context", lambda **k: context)
    with pytest.raises(OSError):
        probe.rejected(dict(host="fixture", port="5432", user="app", sslrootcert="/ca", connect_timeout="3"), "neighbor")
    assert raw.sent == [struct.pack("!II", 8, 80877103)]


def test_distributed_copy_external_witness_has_no_local_pg_service(tmp_path):
    import yaml
    fixture = ROOT / "tests/fixtures/standalone_project"
    spec = importlib.util.spec_from_file_location("copy49", fixture / "copy_witness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = tmp_path / "synthetic-copy-input"
    for directory in ("src", "scripts", "docs/postgres"):
        (tools / directory).mkdir(parents=True, exist_ok=True)
    for name in ("Dockerfile", "VERSION", "requirements.txt", "requirements.lock", "requirements-postgres.txt",
                 "requirements-postgres.lock", ".env.example", "docs/postgres/infrastructure.sql"):
        (tools / name).write_text("copy-only synthetic input, never executed")
    destination = tmp_path / "copy"
    module.copy(ROOT / "boilerplate", tools, destination)
    extra = yaml.safe_load((destination / "compose.external-jobs.yml").read_text())
    assert "postgres" not in extra["services"]
    assert "pg-net" not in extra.get("networks", {})
    assert "pg-external" in extra["services"]["mcp-tools"]["networks"]
    assert "pg-external" in extra["services"]["tools-job"]["networks"]
    client = yaml.safe_load((destination / "docker-compose.pg-external.yml").read_text())
    assert "mcp-tools" in client["services"] and "mon-mcp" not in client["services"]
    assert (destination / "ops/postgres/external.py").is_file()
    assert (destination / "ops/postgres/connection_probe.py").is_file()


def test_hba_future_database_probe_cannot_collide_with_requested_domain():
    data = snapshot()
    # Every known database has an explicit reject, followed by an unsafe default.
    lines = ["local all all reject"]
    for network in ("0.0.0.0/0", "::/0"):
        lines += ["hostssl starter49_unrelated all " + network + " scram-sha-256"]
        lines += ["host postgres,template0,template1,neighbor all " + network + " reject"]
        lines += ["host all all " + network + " scram-sha-256"]
    data["hba_text"] = "\n".join(lines)
    data["hba"] = [data["hba"][0]]
    for number, text in enumerate(lines[1:], 2):
        kind, databases, users, network, method = text.split()
        address = network.split("/")[0]
        data["hba"].append(dict(line_number=number, file_name="/hba", error=None,
                                type=kind, database=databases.split(","), user_name=[users],
                                address=address, netmask=address, auth_method=method))
    with pytest.raises(ValueError, match="non-domain"):
        load("hba").validate(data, "starter49_unrelated", ROLES, "neighbor")


def test_config_argv_activates_operator_profile(monkeypatch):
    external = load("external")
    calls = []
    config = dict(services={"mon-mcp": {"networks": {"pg-external": {}}},
                           "pg-external-operator": {"networks": {"pg-external": {}}}},
                  networks={"pg-external": {"external": True, "name": "case-net"}})
    def command(argv, **kwargs):
        calls.append(argv)
        if "inspect" in argv:
            return json.dumps([dict(Driver="bridge", Internal=False)])
        rendered = copy.deepcopy(config)
        if "--profile" not in argv or argv[argv.index("--profile") + 1] != "operator":
            del rendered["services"]["pg-external-operator"]
        return json.dumps(rendered)
    monkeypatch.setattr(external.prepare, "run", command)
    external.validate_network(dict(root="/copy", project="p", env={}, mode="external", target={"network": "case-net"}))
    config_argv = calls[-1]
    assert config_argv[-5:] == ["--profile", "operator", "config", "--format", "json"]


def test_real_compose_config_keeps_operator_and_app_without_runtime(tmp_path, monkeypatch):
    """Actual local Compose YAML rendering only; no daemon/network/container calls."""
    import shutil
    import subprocess
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable; no runtime fallback")
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    external = load("external")
    copied = tmp_path / "standalone-copy"
    shutil.copytree(ROOT / "boilerplate", copied)
    (copied / ".env").write_text("")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    env = dict(PG_APP_IMAGE="sha256:" + "a" * 64, PG_OPERATOR_IMAGE="sha256:" + "b" * 64,
               PG_CLIENT_PRIVATE_DIR=str(private), PG_JOB_PRIVATE_DIR=str(private),
               PG_JOB_UID="1001", PG_JOB_GID="1001", PG_DATABASE_NAME="domain",
               PG_OWNER_ROLE="domain_owner", PG_APP_ROLE="domain_app", PG_INSTALL_ROLE="domain_install",
               PG_BACKUP_ROLE="domain_backup", PG_RESTORE_ROLE="domain_restore", PG_EXTERNAL_NETWORK="config-only-case")
    actual = []
    def command(argv, **kwargs):
        if "inspect" in argv:
            # Network existence/ownership is not claimed by this local rendering test.
            return json.dumps([dict(Driver="bridge", Internal=False)])
        assert "config" in argv and argv[-2:] == ["--format", "json"]
        result = subprocess.run(argv, env=kwargs["env"], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, "Compose config failed; no runtime operation was attempted"
        actual.append((argv, json.loads(result.stdout)))
        return result.stdout
    monkeypatch.setattr(external.prepare, "run", command)
    state = dict(root=str(copied), project="starter49-config-src", env=env, mode="external",
                 target={"network": "config-only-case"}, app_image=env["PG_APP_IMAGE"], operator_image=env["PG_OPERATOR_IMAGE"])
    try:
        external.validate_network(state)
    finally:
        # Optional private test evidence. Rendered data contains synthetic paths/images only.
        evidence = os.environ.get("STARTER49_CONFIG_EVIDENCE")
        if evidence:
            with Path(evidence).open("x") as stream:
                json.dump([{"argv": argv, "composition": value} for argv, value in actual], stream, indent=2)
    _, rendered = actual[0]
    assert "pg-external-operator" in rendered["services"]
    assert set(rendered["services"]["pg-external-operator"]["networks"]) == {"pg-external"}
    assert set(rendered["services"]["mon-mcp"]["networks"]) == {"mcp-net", "pg-external"}


def test_real_local_host_and_default_compose_merge_ports_and_network(tmp_path, monkeypatch):
    """Real Compose rendering: opt-in changes only PG publication/network scalar."""
    import shutil
    import subprocess
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable; no runtime fallback")
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    prepare = load("prepare")
    copied = tmp_path / "copied"
    shutil.copytree(ROOT / "boilerplate", copied)
    (copied / ".env").write_text("")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    env = dict(PG_PRIVATE_DIR=str(private), PG_CLIENT_PRIVATE_DIR=str(private),
               PG_OPERATOR_IMAGE="sha256:" + "a" * 64, PG_JOB_PRIVATE_DIR=str(private),
               PG_JOB_UID="1001", PG_JOB_GID="1001", PG_DATABASE_NAME="domain",
               PG_OWNER_ROLE="domain_owner", PG_APP_ROLE="domain_app", PG_INSTALL_ROLE="domain_install",
               PG_BACKUP_ROLE="domain_backup", PG_RESTORE_ROLE="domain_restore", WAF_PORT="18082")
    rendered = {}
    for host in (False, True):
        state = dict(root=str(copied), project="starter49-render-src", env=dict(env))
        if host:
            state.update(host_port=19432)
            state["env"]["PG_HOST_PORT"] = "19432"
        argv, configured = prepare.compose(state)
        expected_files = 5 if host else 4
        assert argv.count("-f") == expected_files
        command = argv + ["--profile", "operator", "config", "--format", "json"]
        result = subprocess.run(command, env=configured, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, "Compose config refused; no runtime effect attempted"
        rendered[host] = json.loads(result.stdout)
    default, host = rendered[False], rendered[True]
    assert default["networks"]["pg-net"]["internal"] is True
    assert not host["networks"]["pg-net"].get("internal")
    assert not default["services"]["postgres"].get("ports")
    assert set(default["services"]) == set(host["services"])
    for name in host["services"]:
        if name != "postgres":
            # The inherited WAF port is preserved, never erased to force this oracle.
            assert host["services"][name].get("ports") == default["services"][name].get("ports")
    ports = host["services"]["postgres"]["ports"]
    assert len(ports) == 1
    assert ports[0]["host_ip"] == "127.0.0.1" and str(ports[0]["published"]) == "19432"
    assert ports[0]["target"] == 5432 and ports[0]["protocol"] == "tcp"
    assert host["services"]["waf"]["ports"] == default["services"]["waf"]["ports"]
