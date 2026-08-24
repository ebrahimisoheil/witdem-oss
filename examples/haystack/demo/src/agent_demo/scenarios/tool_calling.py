"""tool_calling: model -> tool -> continuation.

Physical shape: interpret_query -> generate_turn(tool_call) ->
execute_tool(ok) -> generate_turn(final_answer) -> evaluate_answer. Exactly
one "execute_tool" span, always OK status -- distinct from failure_recovery
(two tool spans, one ERROR) and terminal_failure (only ERROR spans, no
final answer).
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario, ScriptedTurn

SCENARIO = ScriptedScenario(
    name="tool_calling",
    description="Model requests the lookup tool once, then continues to a final answer.",
    question="What is the capital of France? Please confirm with a lookup.",
    turns=(
        ScriptedTurn(kind="tool_call", tool_name="lookup", tool_args={"topic": "capital_of_france"}),
        ScriptedTurn(
            kind="final_answer",
            answer_text="Confirmed via lookup: the capital of France is Paris.",
            quality_score=0.9,
        ),
    ),
)
