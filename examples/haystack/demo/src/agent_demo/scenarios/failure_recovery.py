"""failure_recovery: the primary tool fails; a fallback tool recovers and succeeds.

Physical shape: interpret_query -> generate_turn(tool_call:lookup) ->
execute_tool(ERROR) -> generate_turn(tool_call:calculator, fallback) ->
execute_tool(ok) -> generate_turn(final_answer) -> evaluate_answer. Exactly
one ERROR-status "execute_tool" span followed by an OK one -- distinct from
terminal_failure, where no recovery ever succeeds and the run never reaches
a final answer.
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario, ScriptedTurn

SCENARIO = ScriptedScenario(
    name="failure_recovery",
    description="Primary lookup tool fails once; a fallback calculator call recovers and succeeds.",
    question=(
        "Look up this quarter's fraud rate; if that source is down, estimate it "
        "from the raw counts instead."
    ),
    turns=(
        ScriptedTurn(
            kind="tool_call",
            tool_name="lookup",
            tool_args={"topic": "fraud_rate"},
            force_tool_error=True,
        ),
        ScriptedTurn(kind="tool_call", tool_name="calculator", tool_args={"expression": "42/2100*100"}),
        ScriptedTurn(
            kind="final_answer",
            answer_text="The lookup source was unavailable; estimated fraud rate from raw counts: 2%.",
            quality_score=0.8,
        ),
    ),
)
