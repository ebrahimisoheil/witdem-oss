from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "release" / "verify.py"
SPEC = importlib.util.spec_from_file_location("release_verify", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MANIFEST_SCRIPT = Path(__file__).parents[1] / "scripts" / "release" / "build_manifest.py"
MANIFEST_SPEC = importlib.util.spec_from_file_location("release_manifest", MANIFEST_SCRIPT)
assert MANIFEST_SPEC is not None and MANIFEST_SPEC.loader is not None
MANIFEST_MODULE = importlib.util.module_from_spec(MANIFEST_SPEC)
MANIFEST_SPEC.loader.exec_module(MANIFEST_MODULE)


def test_platform_release_sources_agree() -> None:
    assert MODULE.validate("platform", None, require_clean=False) == []


def test_sdk_release_sources_agree() -> None:
    assert MODULE.validate("sdk", None, require_clean=False) == []


def test_existing_version_tag_cannot_point_to_another_commit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    real_git = MODULE._git

    def fake_git(*arguments: str, check: bool = True) -> str:
        if arguments[:2] == ("rev-parse", "refs/tags/analytics-v0.1.0^{commit}"):
            return "old-commit"
        if arguments == ("rev-parse", "HEAD"):
            return "new-commit"
        return real_git(*arguments, check=check)

    monkeypatch.setattr(MODULE, "_git", fake_git)
    errors = MODULE.validate("platform", "analytics-v0.1.0", require_clean=False)
    assert any("version reuse refused" in error for error in errors)


def test_tag_suffix_must_match_manifest() -> None:
    errors = MODULE.validate("platform", "analytics-v9.9.9", require_clean=False)
    assert any("release tag" in error for error in errors)


def test_ambient_platform_tag_does_not_contaminate_sdk_validation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "analytics-v0.1.0")

    assert MODULE.validate("sdk", None, require_clean=False) == []


def test_explicit_tag_is_validated_even_when_ambient_tag_differs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "sdk-v0.1.0")

    errors = MODULE.validate("platform", "analytics-v9.9.9", require_clean=False)
    assert any("release tag" in error for error in errors)


def test_release_commit_whitespace_errors_are_rejected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    real_git = MODULE._git

    def fake_git(*arguments: str, check: bool = True) -> str:
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("show", "--check", "--oneline", "--no-renames", "HEAD"):
            raise RuntimeError("example.yml:4: new blank line at EOF")
        return real_git(*arguments, check=check)

    monkeypatch.setattr(MODULE, "_git", fake_git)
    errors = MODULE.validate("platform", None, require_clean=True)
    assert any("release commit contains whitespace errors" in error for error in errors)


def test_platform_recovery_preserves_immutable_tag_provenance() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "release-analytics.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    release_tag_expression = (
        "RELEASE_TAG: ${{ github.event_name == 'workflow_dispatch' "
        "&& inputs.release_tag || github.ref_name }}"
    )
    assert release_tag_expression in workflow
    assert workflow.count("ref: ${{ needs.preflight.outputs.release_tag }}") == 3
    assert "sha-${{ needs.preflight.outputs.release_commit }}" in workflow


def test_platform_npm_release_uses_explicit_non_latest_tag() -> None:
    root = Path(__file__).parents[1]
    release_workflow = (root / ".github" / "workflows" / "release-analytics.yml").read_text(
        encoding="utf-8"
    )

    assert 'NPM_DIST_TAG: "stable-0-1"' in release_workflow
    assert 'npm publish --access public --tag "$NPM_DIST_TAG"' in release_workflow
    assert "resume_publication:" in release_workflow
    assert 'test "$latest_after" = "$latest_before"' in release_workflow


def test_release_manifest_links_to_analytics_tag() -> None:
    signing_key = base64.b64encode(bytes(range(32))).decode()
    manifest = MANIFEST_MODULE.build_manifest(
        repository="ebrahimisoheil/witdem-oss",
        signing_key=signing_key,
    )

    assert manifest["release_notes_url"].endswith("/releases/tag/analytics-v0.1.0")
