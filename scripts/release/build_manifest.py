#!/usr/bin/env python3
"""Build the signed release manifest after every public artifact exists."""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
        "workflow_schema_version": str(release["workflow_schema_version"]),
        "compiler_version": str(release["workflow_compiler_version"]),
        "projector_version": str(release["workflow_projector_version"]),
        "minimum_compatible_versions": {"platform": "0.3.0", "sdk": "0.2.0"},
        "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release_notes_url": f"https://github.com/{repository}/releases/tag/analytics-v{platform}",
        "artifacts": {
            "wheel": f"https://pypi.org/project/witdem-analytics/{platform}/",
            "npm": f"https://www.npmjs.com/package/witdem/v/{platform}",
            "sdk": f"https://pypi.org/project/witdem-sdk/{sdk}/",
            "image": f"ghcr.io/{repository.split('/')[0]}/witdem-analytics:{platform}",
        },
    }
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(signing_key, validate=True))
    except ValueError as exc:
        raise ValueError("WITDEM_RELEASE_SIGNING_KEY must be a base64-encoded raw Ed25519 key") from exc
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
