"""Deterministic, scripted fake chat/tool-call generator.

No paid provider credentials are ever required to run this demo or its tests:
every scenario's sequence of model turns is a fixed, hand-written script (see
``scenarios/*.py``), keyed by scenario name and indexed by turn number. This
is the ONLY source of "model behavior" the workflow depends on, so a full
run is bit-for-bit reproducible.

A real-provider path (calling an actual chat/tool-calling model) is an
explicitly optional stretch goal per the task brief and is not implemented
here -- see the final report for this documented gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

TurnKind = Literal["tool_call", "final_answer"]


@dataclass(frozen=True)
class ScriptedTurn:
    """One deterministic step of a scripted conversation.

    ``kind="tool_call"`` asks the workflow to invoke ``tool_name`` with
    ``tool_args``; ``force_tool_error`` simulates that tool/provider failing
    regardless of arguments, so failure/recovery scenarios never depend on
    real flaky behavior. ``kind="final_answer"`` supplies the model's final
    text plus a pre-baked ``quality_score`` that ``EvaluateAnswer`` will
    score against its acceptance threshold -- this is what drives the
    correction-loop scenario deterministically.
    """

    kind: TurnKind
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    answer_text: str | None = None
    quality_score: float | None = None
    force_tool_error: bool = False


@dataclass(frozen=True)
class ScriptedScenario:
    """A named, fixed script plus the human-readable question that motivates it."""

    name: str
    description: str
    question: str
    turns: tuple[ScriptedTurn, ...]


class UnknownScenarioError(KeyError):
    """Raised when a scenario name isn't in the provider's registry."""


class FakeChatProvider:
    """Deterministic stand-in for a real chat/tool-calling model API.

    Takes a ``{scenario_name: ScriptedScenario}`` registry (see
    ``scenarios/__init__.py``) and deterministically replays each scenario's
    turns in order, one per call to :meth:`next_turn`.
    """

    def __init__(self, scenarios: dict[str, ScriptedScenario]) -> None:
        self._scenarios = dict(scenarios)

    def scenario(self, name: str) -> ScriptedScenario:
        try:
            return self._scenarios[name]
        except KeyError as exc:
            raise UnknownScenarioError(name) from exc

    def available_scenarios(self) -> list[str]:
        return sorted(self._scenarios)

    def next_turn(self, scenario_name: str, turn_index: int) -> ScriptedTurn:
        """Return the ``turn_index``-th scripted turn (0-based) for ``scenario_name``.

        A script that is exhausted without ever producing a ``final_answer``
        indicates a scenario-authoring bug; rather than raising an
        ``IndexError`` deep inside a pipeline run, this fails in the same
        deterministic "tool error" shape the terminal-failure scenario
        already exercises, so the workflow's bounded retry logic still
        terminates cleanly.
        """

        scenario = self.scenario(scenario_name)
        if turn_index < len(scenario.turns):
            return scenario.turns[turn_index]
        return ScriptedTurn(kind="tool_call", tool_name="lookup", force_tool_error=True)
