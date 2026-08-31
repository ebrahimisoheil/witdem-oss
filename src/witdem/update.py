"""Signed, non-mutating release discovery for Witdem installations."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from witdem import __version__
from witdem.analytics.operations import MEASUREMENT_REGISTRY_VERSION, OPERATION_TAXONOMY_VERSION
from witdem.config import storage_root
from witdem.evaluation_campaigns import EVALUATION_SCHEMA_VERSION
from witdem.protocol import CORPUS_SCHEMA_VERSION, SEMANTIC_RECORD_PROTOCOL_VERSION
from witdem.workflows import WORKFLOW_COMPILER_VERSION, WORKFLOW_PROJECTOR_VERSION

RELEASE_MANIFEST_SCHEMA_VERSION = 1
RELEASE_MANIFEST_URL = "https://github.com/ebrahimisoheil/witdem-oss/releases/latest/download/witdem-release.json"
# Release engineering replaces this key before the first signed publication.
# Tests and private channels may supply WITDEM_RELEASE_PUBLIC_KEY.
RELEASE_PUBLIC_KEY = "uhFX8JHG8gdmHzYy8oJ/zH9pX3PglkYELBCggmv89Pk="
UPDATE_CACHE_SECONDS = 24 * 60 * 60


class ManifestVerificationError(ValueError):
    """Raised when a release manifest cannot be trusted."""


def _canonical_payload(manifest: Mapping[str, Any]) -> bytes:
    value = {key: item for key, item in manifest.items() if key != "signature"}
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_manifest(
    manifest: Mapping[str, Any],
    *,
    public_key: str | None = None,
) -> dict[str, Any]:
    if int(manifest.get("schema_version") or 0) != RELEASE_MANIFEST_SCHEMA_VERSION:
        raise ManifestVerificationError("unsupported release manifest schema")
    if int(manifest.get("signature_version") or 0) != 1:
        raise ManifestVerificationError("unsupported release manifest signature version")
    signature = manifest.get("signature")
    if not isinstance(signature, str):
        raise ManifestVerificationError("release manifest signature is missing")
    encoded_key = public_key or os.getenv("WITDEM_RELEASE_PUBLIC_KEY") or RELEASE_PUBLIC_KEY
    try:
        verifier = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key, validate=True))
        verifier.verify(base64.b64decode(signature, validate=True), _canonical_payload(manifest))
    except (InvalidSignature, ValueError) as exc:
        raise ManifestVerificationError("release manifest signature is invalid") from exc
    required = {
        "channel",
        "platform_version",
        "npm_version",
        "sdk_version",
        "image_version",
        "protocol_version",
        "workflow_schema_version",
        "compiler_version",
        "projector_version",
        "minimum_compatible_versions",
        "published_at",
        "release_notes_url",
        "artifacts",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ManifestVerificationError(f"release manifest is missing: {', '.join(missing)}")
    return dict(manifest)


def _cache_path(root: Path | None = None) -> Path:
    return (root or storage_root()) / "cache" / "release-manifest.json"


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(dict(value), sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_cache(root: Path | None, *, allow_expired: bool = False) -> dict[str, Any] | None:
    path = _cache_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    checked_at = float(value.get("checked_at") or 0)
    if not allow_expired and time.time() - checked_at > UPDATE_CACHE_SECONDS:
        return None
    manifest = value.get("manifest")
    if not isinstance(manifest, Mapping):
        return None
    try:
        return verify_manifest(manifest)
    except ManifestVerificationError:
        return None


def fetch_manifest(*, timeout: float = 3.0) -> dict[str, Any]:
    url = os.getenv("WITDEM_RELEASE_MANIFEST_URL") or RELEASE_MANIFEST_URL
    request = urllib.request.Request(url, headers={"User-Agent": f"witdem-analytics/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read())
    if not isinstance(value, Mapping):
        raise ManifestVerificationError("release manifest must be a JSON object")
    return verify_manifest(value)


def _version_tuple(value: object) -> tuple[int, ...]:
    result: list[int] = []
    for part in str(value).split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        result.append(int(digits))
    return tuple(result or [0])


def installed_versions() -> dict[str, str]:
    try:
        sdk_version = version("witdem-sdk")
    except PackageNotFoundError:
        sdk_version = "not-installed"
    return {
        "platform": __version__,
        "sdk": sdk_version,
        "protocol": SEMANTIC_RECORD_PROTOCOL_VERSION,
        "corpus_schema": CORPUS_SCHEMA_VERSION,
        "workflow_schema": "1",
        "compiler": WORKFLOW_COMPILER_VERSION,
        "projector": WORKFLOW_PROJECTOR_VERSION,
        "operation_taxonomy": OPERATION_TAXONOMY_VERSION,
        "measurement_registry": MEASUREMENT_REGISTRY_VERSION,
        "evaluation_schema": EVALUATION_SCHEMA_VERSION,
    }


def _report(manifest: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    current = installed_versions()
    latest = str(manifest["platform_version"])
    minimum = dict(manifest.get("minimum_compatible_versions") or {})
    protocol_compatible = str(manifest["protocol_version"]) == current["protocol"]
    platform_compatible = _version_tuple(current["platform"]) >= _version_tuple(minimum.get("platform") or "0")
    update_available = _version_tuple(latest) > _version_tuple(current["platform"])
    target_sdk = str(manifest["sdk_version"])
    return {
        "status": "update-available" if update_available else "current",
        "source": source,
        "current": current,
        "latest": {
            "platform": latest,
            "npm": str(manifest["npm_version"]),
            "sdk": target_sdk,
            "image": str(manifest["image_version"]),
        },
        "compatibility": {
            "compatible": protocol_compatible and platform_compatible,
            "protocol": "compatible" if protocol_compatible else "incompatible",
            "platform": "compatible" if platform_compatible else "incompatible",
        },
        "guidance": {
            "npx": f"npx witdem@{manifest['npm_version']} up",
            "pipx": f'pipx install --force "witdem-analytics=={latest}"',
            "sdk": f"python -m pip install --upgrade witdem-sdk=={target_sdk}",
        },
        "release_notes_url": manifest["release_notes_url"],
        "published_at": manifest["published_at"],
    }


def check_updates(
    *,
    root: Path | None = None,
    refresh: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    """Check and guide only. Failures are returned and never block startup."""

    disabled = os.getenv("WITDEM_UPDATE_CHECK", "1").casefold() in {"0", "false", "no", "off"}
    if disabled:
        return {"status": "disabled", "current": installed_versions()}
    if not refresh:
        cached = _read_cache(root)
        if cached is not None:
            return _report(cached, source="cache")
    if offline:
        cached = _read_cache(root, allow_expired=True)
        if cached is None:
            return {"status": "unavailable", "reason": "offline and no verified cache", "current": installed_versions()}
        return _report(cached, source="offline-cache")
    try:
        manifest = fetch_manifest()
        _atomic_json(_cache_path(root), {"checked_at": time.time(), "manifest": manifest})
        return _report(manifest, source="network")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"status": "unavailable", "reason": str(exc), "current": installed_versions()}
