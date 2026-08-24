"""nested: a compound tool whose own execution nests child spans.

Physical shape: interpret_query -> generate_turn(tool_call:multi_step_lookup)
-> execute_tool(ok) -- which itself opens two nested child spans
("tool.multi_step_lookup.lookup_step" and
"tool.multi_step_lookup.calculate_step") as it performs a lookup and a
calculation as one compound tool -- -> generate_turn(final_answer) ->
evaluate_answer. This is the only scenario whose span tree goes deeper than
two levels below the execution root, which is the point of it: it proves a
tool implementation is free to represent internal sub-steps as real nested
spans, and that this nesting still correlates to the same execution_id via
baggage propagation, not just top-level component spans.
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario, ScriptedTurn

SCENARIO = ScriptedScenario(
    name="nested",
    description="A single compound tool call that internally nests a lookup and a calculation.",
    question="Look up the capital of France and attach 40+2 as a reference number.",
    turns=(
        ScriptedTurn(
            kind="tool_call",
            tool_name="multi_step_lookup",
            tool_args={"topic": "capital_of_france", "expression": "40+2"},
        ),
        ScriptedTurn(kind="final_answer", answer_text="Paris (ref #42).", quality_score=0.9),
    ),
)
