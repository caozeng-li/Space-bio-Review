"""Explicit live v5 topic research; uses external services, never runs a full review."""

import argparse
import asyncio
from pathlib import Path

from spacebio_review.v5.topic import TopicResearch, TopicTask


async def main(args):
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    topics = await TopicResearch.from_config(
        args.runtime_config, output, args.application, env_file=args.env_file
    )
    try:
        task = (
            TopicTask.model_validate_json(Path(args.topic_file).read_text("utf-8"))
            if args.topic_file
            else TopicTask(responsibility=args.question)
        )
        result = await topics.run(topics.create(task))
        print(result.model_dump_json(indent=2))
    finally:
        await topics.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", required=True)
    parser.add_argument(
        "--question", default="How does sample storage affect fluorescence measurements?"
    )
    parser.add_argument(
        "--topic-file", help="A v5 TopicTask JSON file; replaces the old KnowledgeQuestion format"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-config", default="config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    asyncio.run(main(parser.parse_args()))
