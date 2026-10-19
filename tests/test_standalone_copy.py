"""Operator failures must preserve state and refuse before Docker/SQL dispatch."""
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def op():
    return module(ROOT / "boilerplate/ops/postgres/prepare.py", "operator48")


def test_fresh_copy_runs_without_factory_imports(tmp_path):
    copied = tmp_path / "copy"
    shutil.copytree(ROOT / "boilerplate", copied)
    op = module(copied / "ops/postgres/prepare.py", "copied_operator48")
    assert op.identifier("my_domain") == "my_domain"
    assert (copied / "docker-compose.pg-client.yml").is_file()
    assert (copied / "ops/postgres/product-install.example.sh").is_file()
    assert not (copied / "ops/postgres/product-install.sh").exists()


@pytest.mark.parametrize("name", ["postgres;DROP DATABASE tools", "Tools", "../tools", "a-b", "", "x" * 64])
def test_identifier_refuses_unsafe_sql(op, name):
    with pytest.raises(ValueError):
        op.identifier(name)


def test_private_path_refuses_symlink_and_public_directory(op, tmp_path):
    target = tmp_path / "public"
    target.mkdir(mode=0o755)
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    for path in (link, target):
        with pytest.raises(ValueError):
            op.private_path(str(path))


def test_uncertain_dispatch_cannot_be_replayed_or_overwritten(op, tmp_path):
    tmp_path.chmod(0o700)
    state = {"private": str(tmp_path)}
    op.save(state, "provision")
    with pytest.raises(FileExistsError):
        op.save(state, "provision")
    assert (tmp_path / "provision.intent").read_text() == "dispatch-intended\n"


def test_failure_does_not_return_secret_output(op, monkeypatch):
    monkeypatch.setattr(op.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout="secret-credential", stderr="secret"))
    with pytest.raises(RuntimeError) as error:
        op.run(["not-executed"], capture=True)
    assert "secret" not in str(error.value)


def test_wrong_project_state_refused(op, tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "state.json"
    op.write_private(path, json.dumps({"root": "/not-this-project"}))
    with pytest.raises(ValueError, match="Wrong copied project"):
        op.load(str(path))


def test_compose_keeps_private_state_out_of_argv(op):
    state = dict(root="/copy", project="starter48-test-src", env={"PG_PRIVATE_DIR": "/private"})
    args, env = op.compose(state)
    assert args[:3] == ["docker", "compose", "--project-directory"]
    assert args[-7::2] == ["/copy/docker-compose.yml", "/copy/docker-compose.pg-client.yml",
                             "/copy/docker-compose.postgres.yml", "/copy/docker-compose.pg-operator.yml"]
    assert "/private" not in args
    assert env["PG_PRIVATE_DIR"] == "/private"


def test_archive_mismatch_refused_before_destination_created(tmp_path):
    acquire = module(ROOT / "tests/fixtures/standalone_project/acquire.py", "acquire48")
    destination = tmp_path / "tools"
    with pytest.raises(ValueError, match="mismatch"):
        acquire.extract(b"not-the-public-archive", destination)
    assert not destination.exists()


def test_local_image_id_is_not_a_registry_digest(op, monkeypatch):
    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(op, "run", lambda *a, **k: json.dumps([{"Id": image_id, "RepoDigests": []}]))
    assert op.inspect_image("local-witness:run") == image_id


def test_operator_contract_has_no_host_pg_exposure_or_external_volume():
    import yaml
    root = ROOT / "boilerplate"
    local = yaml.safe_load((root / "docker-compose.postgres.yml").read_text())
    assert "ports" not in local["services"]["postgres"]
    assert local["volumes"] == {"pg-data": {}}
    client = yaml.safe_load((root / "docker-compose.pg-client.yml").read_text())
    bind, = client["services"]["mon-mcp"]["volumes"]
    assert bind["target"] == "/run/pg-client" and bind["read_only"] is True
    assert bind["bind"]["create_host_path"] is False
    assert client["networks"]["pg-net"] == {"internal": True}
    witness = yaml.safe_load((ROOT / "tests/fixtures/standalone_project/compose.jobs.yml").read_text())
    assert witness["services"]["tools-job"]["networks"] == ["pg-net", "test-s3"]
    assert witness["networks"]["test-s3"]["external"] is True
    assert "volumes" not in witness


def test_existing_private_destination_refused_before_docker(op, tmp_path, monkeypatch):
    copy = tmp_path / "copy"
    shutil.copytree(ROOT / "boilerplate", copy)
    monkeypatch.chdir(copy)
    existing = tmp_path / "existing-private"
    existing.mkdir(mode=0o700)
    (existing / "preserve").write_text("third-party-state")
    monkeypatch.setattr(op, "run", lambda *a, **k: pytest.fail("Docker must not be reached"))
    with pytest.raises(ValueError, match="New private destination"):
        op.initialize(SimpleNamespace(private=str(existing)))
    assert (existing / "preserve").read_text() == "third-party-state"


def test_example_product_hook_fails_closed_without_docker():
    import subprocess
    result = subprocess.run(["sh", str(ROOT / "boilerplate/ops/postgres/product-install.example.sh")],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert "hook missing" in result.stderr
