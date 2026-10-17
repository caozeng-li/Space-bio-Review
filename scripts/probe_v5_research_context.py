"""Current phase/context checks, replacing the retired update_research gate probe.

The shared local probe covers initial and incremental notes, unabsorbed batches,
restoration, explicit replacements, long-application reading and completion budgets.
It uses fixed provider replies and performs no external requests or full review.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from probe_v5_explicit_phases import main as run_phase_context_checks

from spacebio_review.v5.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    assert config.session_overrides["chief"].context_tokens == 256_000
    assert config.session_overrides["researcher"].context_tokens == 128_000
    asyncio.run(run_phase_context_checks(args.output))


if __name__ == "__main__":
    main()
