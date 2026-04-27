# -*- coding: utf-8 -*-
"""S3TokenStore policy_id metadata tests.

`policy_id` is metadata only in this ticket. No PolicyStore enforcement is
implemented here.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.auth.token_store import S3TokenStore  # noqa: E402


@dataclass
class DummySettings:
    s3_region_name: str = "fr1"
    s3_signature_version: str = "s3"
    s3_addressing_style: str = "path"
    s3_endpoint_url: str = "http://s3.example.test"
    s3_access_key_id: str = "key"
    s3_secret_access_key: str = "secret"
    s3_bucket_name: str = "bucket"


class InMemoryS3TokenStore(S3TokenStore):
    def load(self):
        self._cache_time = 9999999999

    def _save(self):
        self.saved_payload = {"tokens": list(self._tokens.values())}


def test_s3_token_store_policy_id_metadata_create_list_update():
    store = InMemoryS3TokenStore(DummySettings())

    created = store.create(
        client_name="agent",
        permissions=["read"],
        allowed_resources=["resource-a"],
        expires_in_days=1,
        email="agent@example.test",
        policy_id="policy-a",
    )

    assert created["policy_id"] == "policy-a"
    assert store.saved_payload["tokens"][0]["policy_id"] == "policy-a"
    listed = store.list_all()
    assert listed[0]["policy_id"] == "policy-a"

    result = store.update(
        created["hash"][:12],
        policy_id="policy-b",
        permissions=["read", "write"],
        allowed_resources=["resource-b"],
    )

    assert result["status"] == "updated"
    assert set(result["updated_fields"]) == {"policy_id", "permissions", "allowed_resources"}
    assert result["policy_id"] == "policy-b"
    assert store.saved_payload["tokens"][0]["policy_id"] == "policy-b"
    assert store.list_all()[0]["policy_id"] == "policy-b"
