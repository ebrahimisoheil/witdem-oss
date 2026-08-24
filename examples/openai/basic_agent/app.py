"""Provider workload only; no Witdem-specific imports."""

from __future__ import annotations

import os


def run() -> str:
    from agents import Agent, Runner, function_tool

    @function_tool
    def lookup_weather(city: str) -> str:
        return f"The weather in {city} is sunny."

    agent = Agent(
        name="weather-agent",
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        instructions="Answer with the weather. Use the tool.",
        tools=[lookup_weather],
    )
    result = Runner.run_sync(agent, "What is the weather in Berlin?")
    return str(result.final_output)
