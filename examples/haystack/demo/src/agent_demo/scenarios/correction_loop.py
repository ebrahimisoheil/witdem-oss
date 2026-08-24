"""correction_loop: a real repeated/correction operation before success.

Physical shape: interpret_query -> (generate_turn -> evaluate_answer) x 3.
The first two evaluations reject the answer for being below the quality
threshold, each rejection driving a fresh "regenerate then re-evaluate"
pass; only the third attempt is accepted. Zero tool spans -- this scenario
isolates the correction mechanism from tool use so the two are never
conflated in the physical shape.
"""

from __future__ import annotations

from agent_demo.fake_provider import ScriptedScenario, ScriptedTurn

SCENARIO = ScriptedScenario(
    name="correction_loop",
    description="First two answers are rejected on quality; the third is accepted.",
    question="Explain our return policy thoroughly.",
    turns=(
        ScriptedTurn(kind="final_answer", answer_text="Returns are accepted.", quality_score=0.3),
        ScriptedTurn(
            kind="final_answer",
            answer_text="Returns are accepted within 30 days.",
            quality_score=0.55,
        ),
        ScriptedTurn(
            kind="final_answer",
            answer_text=(
                "Returns are accepted within 30 days of delivery, unused and in original "
                "packaging with a receipt, for a full refund."
            ),
            quality_score=0.92,
        ),
    ),
)
