"""Explicit live v5 session example; consumes model quota, never runs a review."""

import argparse
import asyncio
from pathlib import Path

from spacebio_review.v5 import ResearchRuntime, SessionTask


async def main(args):
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    runtime = ResearchRuntime.from_config(args.runtime_config, output, env_file=args.env_file)
    try:
        session_id = runtime.create(
            SessionTask(
                role="researcher",
                objective=(
                    "Briefly explain descriptive versus causal scientific contributions, "
                    "then submit the research note."
                ),
                application="This is an isolated explanation example, without a project proposal.",
            )
        )
        result = await runtime.run(session_id)
        print(result.model_dump_json(indent=2))
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-config", default="config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    asyncio.run(main(parser.parse_args()))
