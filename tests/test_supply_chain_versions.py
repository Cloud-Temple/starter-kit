"""Regression tests for security-sensitive version pins."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOILERPLATE = ROOT / "boilerplate"


def test_python_runtime_and_packaging_tools_are_fixed():
    dockerfile = (BOILERPLATE / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "FROM python:3.11.16-slim-trixie@sha256:"
        "9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
    ) in dockerfile
    assert "pip==26.2.1" in dockerfile
    assert "setuptools==84.0.0" in dockerfile
    assert "wheel==0.48.0" in dockerfile
    assert "jaraco.context==6.1.2" in dockerfile
    assert "packaging==26.3" in dockerfile
    assert "backports.tarfile==1.2.0" in dockerfile
    assert "--require-hashes -r requirements.lock" in dockerfile


def test_waf_build_uses_reviewed_security_versions_and_non_root_runtime():
    dockerfile = (BOILERPLATE / "waf" / "Dockerfile").read_text(encoding="utf-8")

    expected_pins = (
        "caddy:2.11.4-builder-alpine@sha256:1a1689db91cfb390b2d856a1b3774e796852822cd723fa54c475b272f82bb4b7",
        "xcaddy build v2.11.4",
        "coraza-caddy/v2@v2.5.0",
        "caddy-ratelimit@5625512f24f6f59d6f64fb3aafe5eecff0b286db",
        "golang.org/x/crypto@v0.56.0",
        "golang.org/x/net@v0.58.0",
        "google.golang.org/grpc@v1.83.2",
        "FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40",
        "setcap cap_net_bind_service=+ep /usr/bin/caddy",
        "USER caddy",
    )
    for pin in expected_pins:
        assert pin in dockerfile


def test_compose_images_do_not_use_floating_latest_tags():
    compose_files = list(BOILERPLATE.glob("docker-compose*.yml"))

    assert len(compose_files) == 3

    for compose_file in compose_files:
        content = compose_file.read_text(encoding="utf-8")
        assert ":latest" not in content, f"floating tag in {compose_file.name}"

    vault_compose = (BOILERPLATE / "docker-compose.vault-ci.yml").read_text(encoding="utf-8")
    assert (
        "python:3.11.16-slim-trixie@sha256:"
        "9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
    ) in vault_compose


def test_s3_fixture_replaces_unmaintained_minio_with_audited_moto_lock():
    compose = (BOILERPLATE / "docker-compose.ci.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "tests" / "fixtures" / "s3" / "Dockerfile").read_text(encoding="utf-8")
    lock = (ROOT / "tests" / "fixtures" / "s3" / "requirements.lock").read_text(encoding="utf-8")

    assert "minio/" not in compose
    assert "starter-kit-moto-s3:5.2.3" in compose
    assert (
        "FROM python:3.11.16-slim-trixie@sha256:"
        "9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
    ) in dockerfile
    assert "USER moto" in dockerfile
    assert "moto==5.2.3" in lock


def test_github_actions_are_pinned_to_full_commits():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow
    assert workflow.count("python-version: '3.11.16'") == 3
    assert "python -m pip_audit -r boilerplate/requirements.lock" in workflow
    assert "python -m pip_audit -r tests/fixtures/s3/requirements.lock" in workflow
    assert "Verify WAF runtime and modules" in workflow
    assert "coraza-caddy/v2[[:space:]]+v2\\.5\\.0" in workflow
    assert "coraza/v3[[:space:]]+v3\\.7\\.0" in workflow
    assert "coraza-coreruleset/v4[[:space:]]+v4\\.25\\.0" in workflow
    assert "caddy-ratelimit[[:space:]]+v0\\.1\\.1-0\\.20260612195517-5625512f24f6" in workflow
    assert "exec -T waf id -u | grep -vx '0'" in workflow


def test_coraza_is_enabled_and_bypass_is_limited_to_mcp_route():
    caddyfile = (BOILERPLATE / "waf" / "Caddyfile").read_text(encoding="utf-8")

    assert "SecRuleEngine On" in caddyfile
    assert "@mcp path /mcp /mcp/*" in caddyfile
    assert "handle @mcp {" in caddyfile
    assert "path /mcp /mcp/*" in caddyfile
    assert "handle /mcp*" not in caddyfile


def test_default_compose_persists_caddy_acme_state():
    compose = (BOILERPLATE / "docker-compose.yml").read_text(encoding="utf-8")

    assert "- caddy-data:/data" in compose
    assert "caddy-data:" in compose
