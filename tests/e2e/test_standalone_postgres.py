"""Actual public FILES seam; requires an explicitly prepared, owned test scenario.

No setup/build/start occurs on collection or without RUN_STANDALONE_48=1.
The operator authorizes the window BEFORE enabling this test.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(os.environ.get("RUN_STANDALONE_48") != "1",
                                               reason="standalone infrastructure window not enabled")]


def test_public_copy_write_restart_refusals_and_preserved_volume():
    root = Path(os.environ["STANDALONE_48_COPY"]).resolve()
    state_file = Path(os.environ["STANDALONE_48_STATE"]).resolve()
    url = os.environ["STANDALONE_48_URL"]
    token = os.environ["STANDALONE_48_WRITER_TOKEN_FILE"]
    # Both files are private operator inputs. No fallback to a source checkout.
    assert root != Path(__file__).resolve().parents[2]
    state = json.loads(state_file.read_text())
    assert state["root"] == str(root)
    spec = importlib.util.spec_from_file_location("standalone_copy_operator", root / "ops/postgres/prepare.py")
    op = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(op)
    intent = state_file.parent / "public-intent.json"

    def call(*args):
        subprocess.run([os.environ.get("STANDALONE_48_PYTHON", "python"), *args], cwd=root,
                       check=True, timeout=180, stdout=subprocess.DEVNULL)

    def ready():
        for _ in range(30):
            try:
                with urllib.request.urlopen(url + "/ready", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        pytest.fail("Product readiness did not reach 200")

    def public(phase, target):
        call("ops/postgres/qualify.py", phase, "--url", url, "--token-file", token,
             "--intent", str(target))

    ready()
    public("seed", intent)
    # Capture the actual volume mounted by PG; inspect only this owned container.
    compose, env = op.compose(state)
    cid = op.run(compose + ["ps", "--quiet", "postgres"], env=env, capture=True).strip()
    before = json.loads(op.run(["docker", "inspect", "--format", "{{json .Mounts}}", cid], capture=True))
    pg_volume = [v["Name"] for v in before if v.get("Destination") == "/var/lib/postgresql/data"]
    assert len(pg_volume) == 1
    call("ops/postgres/prepare.py", "stop", "--state", str(state_file))
    call("ops/postgres/tools_operator.py", "resume", str(state_file))
    ready()
    public("read", intent)
    after = json.loads(op.run(["docker", "inspect", "--format", "{{json .Mounts}}", cid], capture=True))
    assert pg_volume == [v["Name"] for v in after if v.get("Destination") == "/var/lib/postgresql/data"]
    for kind in ("hostname", "ca", "credential", "database", "incomplete"):
        refused = state_file.parent / ("refused-" + kind + ".json")
        call("ops/postgres/fault.py", kind, str(state_file))
        public("refuse-write", refused)
        call("ops/postgres/tools_operator.py", "start", str(state_file))
        ready()
        public("read", intent)
        public("absent", refused)
