"""terminal_failure: a genuine terminal failure that stops the execution.

Physical shape: interpret_query -> generate_turn(tool_call) ->
execute_tool(ERROR) -> generate_turn(tool_call) -> execute_tool(ERROR). Two
consecutive tool errors trip the workflow's failure-streak bound
(``workflow.MAX_CONSECUTIVE_TOOL_FAILURES``) and the run ends with
``status="failed"`` and no final answer at all -- no "evaluate_answer" span
is ever produced. This is the one scenario that never reaches an accepted
answer, distinct from failure_recovery where a fallback always succeeds.
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario, ScriptedTurn

SCENARIO = ScriptedScenario(
    name="terminal_failure",
    description="Every attempt at the lookup tool fails; the workflow gives up with no answer.",
    question="Look up a topic from a knowledge source that is permanently unavailable.",
    turns=(
        ScriptedTurn(
            kind="tool_call",
            tool_name="lookup",
            tool_args={"topic": "unavailable_topic"},
            force_tool_error=True,
        ),
        ScriptedTurn(
            kind="tool_call",
            tool_name="lookup",
            tool_args={"topic": "unavailable_topic"},
            force_tool_error=True,
        ),
    ),
)
