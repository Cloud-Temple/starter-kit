# -*- coding: utf-8 -*-
"""
Real S3 integration tests for the starter-kit S3TokenStore.

These tests are intended for nightly/manual GitHub Actions only, using the
`nightly-real-s3` environment secrets. They must not run in PR/push default CI.

They validate compatibility with the real Cloud Temple / Dell ECS S3 endpoint.
MinIO e2e remains the default CI non-regression layer.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.auth.token_store import TokenStore  # noqa: E402

pytestmark = pytest.mark.real_s3

RUN_REAL_S3 = os.environ.get("RUN_REAL_S3") == "1"
REQUIRED_ENV = [
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_BUCKET_NAME",
]


@dataclass
class S3Settings:
    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket_name: str
    s3_region_name: str = "fr1"


@pytest.fixture(scope="module")
def real_s3_settings():
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if not RUN_REAL_S3:
        pytest.skip("Set RUN_REAL_S3=1 to run real S3 tests")
    if missing:
        pytest.skip(f"Missing S3 env vars: {', '.join(missing)}")
    return S3Settings(
        s3_endpoint_url=os.environ["S3_ENDPOINT_URL"],
        s3_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        s3_bucket_name=os.environ["S3_BUCKET_NAME"],
        s3_region_name=os.environ.get("S3_REGION_NAME", "fr1"),
    )


@pytest.fixture
def token_store_with_restore(real_s3_settings):
    """Create a TokenStore and restore the original _system/tokens.json afterward."""
    store = TokenStore(real_s3_settings)
    s3 = store._get_s3()
    original = None
    original_exists = False

    try:
        resp = s3.get_object(Bucket=real_s3_settings.s3_bucket_name, Key=store.S3_KEY)
        original = resp["Body"].read()
        original_exists = True
    except Exception as exc:
        if "NoSuchKey" not in str(exc) and "404" not in str(exc):
            raise

    try:
        # Start from a known empty TokenStore for deterministic assertions.
        store._tokens = {}
        store._save()
        yield store
    finally:
        if original_exists:
            s3.put_object(
                Bucket=real_s3_settings.s3_bucket_name,
                Key=store.S3_KEY,
                Body=original,
                ContentType="application/json",
            )
        else:
            try:
                s3.delete_object(Bucket=real_s3_settings.s3_bucket_name, Key=store.S3_KEY)
            except Exception:
                pass


def read_tokens_json(store, settings):
    s3 = store._get_s3()
    resp = s3.get_object(Bucket=settings.s3_bucket_name, Key=store.S3_KEY)
    return json.loads(resp["Body"].read().decode())


def test_real_s3_tokenstore_create_list_update_revoke(token_store_with_restore, real_s3_settings):
    store = token_store_with_restore

    store.load()
    assert store.count() == 0

    created = store.create(
        client_name="ci-real-s3-agent",
        permissions=["read", "write"],
        allowed_resources=["real-s3-test-resource"],
        expires_in_days=1,
        email="ci-real-s3@example.test",
    )

    assert created["raw_token"]
    assert created["hash"]
    assert created["client_name"] == "ci-real-s3-agent"
    assert created["permissions"] == ["read", "write"]

    persisted = read_tokens_json(store, real_s3_settings)
    assert "tokens" in persisted
    assert len(persisted["tokens"]) == 1
    assert persisted["tokens"][0]["client_name"] == "ci-real-s3-agent"

    listed = store.list_all()
    assert len(listed) == 1
    hash_prefix = listed[0]["hash_prefix"]

    updated = store.update(
        hash_prefix=hash_prefix,
        permissions=["read"],
        allowed_resources=["real-s3-updated-resource"],
    )
    assert updated["status"] == "updated"
    assert set(updated["updated_fields"]) == {"permissions", "allowed_resources"}

    store.load()
    listed = store.list_all()
    assert listed[0]["permissions"] == ["read"]

    assert store.revoke(hash_prefix) is True
    store.load()
    listed = store.list_all()
    assert listed[0]["revoked"] is True
    assert store.count() == 0
