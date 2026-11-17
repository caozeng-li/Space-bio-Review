"""Run the production admission boundary and stop before chief research."""

import argparse
import asyncio
import json

from spacebio_review.runner import ReviewConfig
from spacebio_review.v5.entry import prepare_review


async def main(args):
    values = vars(args).copy()
    application = values.pop("application")
    attachments = values.pop("attachment")
    prepared = await prepare_review([application, *attachments], ReviewConfig(**values))
    try:
        print(
            json.dumps(
                {
                    "admission": prepared.admission.model_dump(mode="json"),
                    "chief_id": prepared.chief_id,
                    "research_started": False,
                    "usage": prepared.runtime.budget.usage(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await prepared.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.epilog = (
        "The evaluation system is read from review.yaml; "
        "formal-review enablement remains in admission.yaml."
    )
    parser.add_argument("--application", required=True)
    parser.add_argument("--attachment", action="append", default=[])
    parser.add_argument("--output", required=True)
    for name in ("runtime", "review", "evidence", "admission", "reporting"):
        parser.add_argument(f"--{name}-config", default=f"config/{name}.yaml")
    parser.add_argument("--env-file", default=".env")
    asyncio.run(main(parser.parse_args()))
