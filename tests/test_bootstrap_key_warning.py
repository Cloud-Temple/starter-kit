# -*- coding: utf-8 -*-
"""Non-regression tests for the default ADMIN_BOOTSTRAP_KEY warning value."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "boilerplate" / "src" / "mon_service" / "config.py"
SERVER = ROOT / "boilerplate" / "src" / "mon_service" / "server.py"
ENV_EXAMPLE = ROOT / "boilerplate" / ".env.example"


def test_bootstrap_key_default_is_consistent_across_config_env_and_warning():
    """The warning must trigger for the documented default bootstrap key."""
    config = CONFIG.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    expected = "change_me_in_production"

    assert f'admin_bootstrap_key: str = "{expected}"' in config
    assert f"ADMIN_BOOTSTRAP_KEY={expected}" in env_example
    assert f'settings.admin_bootstrap_key == "{expected}"' in server


def test_obsolete_bootstrap_key_default_is_not_used_for_warning():
    """Avoid reintroducing the old dashed default that does not match .env.example."""
    server = SERVER.read_text(encoding="utf-8")
    assert 'settings.admin_bootstrap_key == "changeme-in-production"' not in server
