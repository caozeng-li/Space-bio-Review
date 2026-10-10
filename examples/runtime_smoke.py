"""Explicit live task example; consumes model quota when invoked."""

import asyncio

from spacebio_review.agents import AgentRuntime, AgentTask, ContextView, RoleProfile
from spacebio_review.models import load_config
from spacebio_review.tools import ToolRegistry


async def main():
    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), env_file=".env")
    try:
        result = await runtime.run_task(
            AgentTask(
                task_id="example",
                kind="preparation",
                mode="direct",
                phase="example",
                objective="Explain descriptive versus causal scientific contributions.",
                expected_product="A short explanation",
                read_actions=0,
            ),
            RoleProfile(agent_id="example", responsibility="Explain scientific project goals."),
            ContextView(agent_id="example", phase="example"),
            ToolRegistry(),
            str,
        )
        print(result.model_dump_json(indent=2, exclude={"memory"}))
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
