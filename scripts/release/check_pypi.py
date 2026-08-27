#!/usr/bin/env python3
"""Refuse to publish a Python distribution version that already exists."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def version_exists(project: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            json.load(response)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("version")
    args = parser.parse_args()
    if version_exists(args.project, args.version):
        print(
            f"Refusing version reuse: {args.project} {args.version} already exists on PyPI.",
            file=sys.stderr,
        )
        return 1
    print(f"PyPI version is available: {args.project} {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
