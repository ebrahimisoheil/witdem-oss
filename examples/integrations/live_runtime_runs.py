"""Runtime launcher for the product example catalog.

Every run is sent over the public ingestion service, keeping the collector's
mounted database as the single source of truth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLES_ROOT))

from run_live import run  # noqa: E402

SELECTIONS = {
    "openai-agents": {"openai/basic_agent"},
    "langchain": {"langchain/runnable_pipeline"},
    "langgraph": {"langgraph/state_graph"},
    "all": {
        "openai/basic_agent",
        "langchain/runnable_pipeline",
        "langgraph/state_graph",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", choices=tuple(SELECTIONS))
    args = parser.parse_args()
    raise SystemExit(1 if run(SELECTIONS[args.runtime]) else 0)


if __name__ == "__main__":
    main()
