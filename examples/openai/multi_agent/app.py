"""OpenAI multi-agent workload without Witdem-specific imports."""

from __future__ import annotations

import os


def run() -> str:
    from agents import Agent, Runner, handoff

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    billing = Agent(name="billing", instructions="Answer billing questions briefly.", model=model)
    general = Agent(name="general", instructions="Answer general questions briefly.", model=model)
    triage = Agent(
        name="triage",
        instructions="Route the user to the right specialist.",
        model=model,
        handoffs=[handoff(billing), handoff(general)],
    )
    result = Runner.run_sync(triage, "I need help understanding my invoice")
    return str(result.final_output)
