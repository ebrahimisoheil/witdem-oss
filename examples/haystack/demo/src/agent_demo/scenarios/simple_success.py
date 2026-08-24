"""simple_success: the model answers directly, no tool call needed.

Physical shape: interpret_query -> generate_turn(final_answer) -> evaluate_answer.
Exactly one ``haystack.component.run`` span named "generate_turn" and one
named "evaluate_answer"; zero "execute_tool" spans. This is the minimal-depth
scenario -- every other scenario is deliberately larger or shaped differently.
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario, ScriptedTurn

SCENARIO = ScriptedScenario(
    name="simple_success",
    description="Model answers directly; no tool call needed.",
    question="What is the capital of France?",
    turns=(
        ScriptedTurn(
            kind="final_answer",
            answer_text="The capital of France is Paris.",
            quality_score=0.95,
        ),
    ),
)
