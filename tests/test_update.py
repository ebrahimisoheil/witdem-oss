from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from witdem import update


def _signed_manifest(private_key: Ed25519PrivateKey, *, platform_version: str = "9.0.0") -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "signature_version": 1,
        "channel": "stable",
        "platform_version": platform_version,
        "npm_version": platform_version,
        "sdk_version": platform_version,
        "image_version": platform_version,
        "protocol_version": update.SEMANTIC_RECORD_PROTOCOL_VERSION,
        "workflow_schema_version": "1",
        "compiler_version": update.WORKFLOW_COMPILER_VERSION,
        "projector_version": update.WORKFLOW_PROJECTOR_VERSION,
        "minimum_compatible_versions": {"platform": "0.1.0", "sdk": "0.1.0"},
        "published_at": "2026-08-30T00:00:00Z",
        "release_notes_url": "https://example.test/releases/9.0.0",
        "artifacts": {"wheel": "witdem-analytics", "npm": "witdem", "image": "witdem:9.0.0"},
    }
    signature = private_key.sign(update._canonical_payload(manifest))
    manifest["signature"] = base64.b64encode(signature).decode()
    return manifest


def test_signed_release_manifest_is_verified_and_cached(tmp_path: Path, monkeypatch) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    manifest = _signed_manifest(private_key)
    monkeypatch.setenv("WITDEM_RELEASE_PUBLIC_KEY", public_key)
    monkeypatch.setattr(update, "fetch_manifest", lambda: update.verify_manifest(manifest))

    result = update.check_updates(root=tmp_path, refresh=True)
    cached = update.check_updates(root=tmp_path, offline=True)

    assert result["status"] == "update-available"
    assert result["compatibility"]["compatible"] is True
    assert cached["source"] == "cache"
    assert "pipx install --force" in result["guidance"]["pipx"]


def test_invalid_signature_never_replaces_verified_cache(tmp_path: Path, monkeypatch) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    manifest = _signed_manifest(private_key)
    monkeypatch.setenv("WITDEM_RELEASE_PUBLIC_KEY", public_key)
    cache = tmp_path / "cache" / "release-manifest.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"checked_at": time.time(), "manifest": manifest}), encoding="utf-8")
    broken = {**manifest, "platform_version": "10.0.0"}
    monkeypatch.setattr(update, "fetch_manifest", lambda: update.verify_manifest(broken))

    result = update.check_updates(root=tmp_path, refresh=True)
    cached = update.check_updates(root=tmp_path, offline=True)

    assert result["status"] == "unavailable"
    assert cached["latest"]["platform"] == "9.0.0"


def test_update_check_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WITDEM_UPDATE_CHECK", "0")
    assert update.check_updates(root=tmp_path)["status"] == "disabled"
