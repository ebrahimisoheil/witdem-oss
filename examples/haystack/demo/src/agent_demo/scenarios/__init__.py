"""Scenario registry: every sibling module defines one ``SCENARIO`` constant.

Six scenarios are registered, matching the physical shapes required of this
demo: ``simple_success``, ``tool_calling``, ``correction_loop``,
``failure_recovery``, ``terminal_failure``, ``nested``. See each module's
docstring for its exact expected span shape.
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario
from agent_demo.scenarios import (
    correction_loop,
    failure_recovery,
    nested,
    simple_success,
    terminal_failure,
    tool_calling,
)

REGISTRY: dict[str, ScriptedScenario] = {
    module.SCENARIO.name: module.SCENARIO
    for module in (
        simple_success,
        tool_calling,
        correction_loop,
        failure_recovery,
        terminal_failure,
        nested,
    )
}

__all__ = ["REGISTRY"]
