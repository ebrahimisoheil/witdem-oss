#!/usr/bin/env python3
"""Build the signed release manifest after every public artifact exists."""

from __future__ import annotations

import argparse
import base64
import json
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_signing_key(value: str) -> Ed25519PrivateKey:
    """Load the production Ed25519 key from common secret-store encodings."""

    text = value.strip()
    if text.startswith("WITDEM_RELEASE_SIGNING_KEY="):
        text = text.split("=", 1)[1].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    if "\\n" in text:
        text = text.replace("\\n", "\n")

    encoded = text.encode()
    candidates: list[bytes] = []
    if text.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
        try:
            key = serialization.load_ssh_private_key(encoded, password=None)
            if isinstance(key, Ed25519PrivateKey):
                return key
        except (TypeError, ValueError):
            pass
    elif text.startswith("-----BEGIN"):
        try:
            key = serialization.load_pem_private_key(encoded, password=None)
            if isinstance(key, Ed25519PrivateKey):
                return key
        except (TypeError, ValueError):
            pass

    compact = "".join(text.split())
    if compact.startswith("base64:"):
        compact = compact.removeprefix("base64:")
    if len(compact) in {64, 128}:
        with suppress(ValueError):
            candidates.append(bytes.fromhex(compact))
    with suppress(ValueError):
        candidates.append(base64.b64decode(compact, validate=True))
    padding = "=" * (-len(compact) % 4)
    with suppress(ValueError):
        candidates.append(base64.urlsafe_b64decode(compact + padding))

    for raw in candidates:
        if raw.startswith(b"-----BEGIN"):
            try:
                key = serialization.load_pem_private_key(raw, password=None)
                if isinstance(key, Ed25519PrivateKey):
                    return key
            except (TypeError, ValueError):
                continue
        if len(raw) == 64:
            raw = raw[:32]
        if len(raw) == 32:
            try:
                return Ed25519PrivateKey.from_private_bytes(raw)
            except ValueError:
                continue
        try:
            key = serialization.load_der_private_key(raw, password=None)
            if isinstance(key, Ed25519PrivateKey):
                return key
        except (TypeError, ValueError):
            continue
    raise ValueError(
        "WITDEM_RELEASE_SIGNING_KEY must contain an Ed25519 private key "
        "encoded as raw base64, raw hex, PKCS8 PEM/DER, or OpenSSH"
    )


def build_manifest(*, repository: str, signing_key: str) -> dict[str, Any]:
    release = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
    platform = str(release["platform_version"])
    sdk = str(release["sdk_version"])
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "signature_version": 1,
        "channel": "stable",
        "platform_version": platform,
        "npm_version": platform,
        "sdk_version": sdk,
        "image_version": platform,
        "protocol_version": str(release["semantic_record_protocol"]),
        "evidence_bundle_schema_version": str(release["evidence_bundle_schema_version"]),
        "workflow_schema_version": str(release["workflow_schema_version"]),
        "compiler_version": str(release["workflow_compiler_version"]),
        "projector_version": str(release["workflow_projector_version"]),
        "minimum_compatible_versions": {"platform": "0.1.0", "sdk": "0.1.0"},
        "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release_notes_url": f"https://github.com/{repository}/releases/tag/analytics-v{platform}",
        "artifacts": {
            "wheel": f"https://pypi.org/project/witdem-analytics/{platform}/",
            "npm": f"https://www.npmjs.com/package/witdem/v/{platform}",
            "sdk": f"https://pypi.org/project/witdem-sdk/{sdk}/",
            "image": f"ghcr.io/{repository.split('/')[0]}/witdem-analytics:{platform}",
        },
    }
    private_key = _load_signing_key(signing_key)
    manifest["signature"] = base64.b64encode(private_key.sign(_canonical(manifest))).decode()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    signing_key = os.getenv("WITDEM_RELEASE_SIGNING_KEY")
    if not signing_key:
        raise SystemExit("WITDEM_RELEASE_SIGNING_KEY is required")
    manifest = build_manifest(repository=args.repository, signing_key=signing_key)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
