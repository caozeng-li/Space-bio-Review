"""Explicit live knowledge research; consumes external services and model quota."""

import argparse
import asyncio
from pathlib import Path

from spacebio_review.agents import AgentRuntime
from spacebio_review.evidence import (
    EvidenceResources,
    KnowledgeQuestion,
    LiteratureResearcher,
    load_evidence_config,
)
from spacebio_review.models import load_config


async def main(args):
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), env_file=".env")
    resources = EvidenceResources.create(
        runtime, output, load_evidence_config("config/evidence.yaml")
    )
    literature = LiteratureResearcher.from_resources(resources)
    try:
        question = (
            KnowledgeQuestion.model_validate_json(Path(args.question_file).read_text("utf-8"))
            if args.question_file
            else KnowledgeQuestion(question=args.question, purpose="method")
        )
        result = await literature.investigate(question)
        print(
            result.answer.model_dump_json(indent=2) if result.answer else result.task_result.reason
        )
    finally:
        await literature.close()
        await resources.close()
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--question", default="How does sample storage affect fluorescence measurements?"
    )
    parser.add_argument("--question-file")
    parser.add_argument("--output", default="runs/literature-example")
    asyncio.run(main(parser.parse_args()))
