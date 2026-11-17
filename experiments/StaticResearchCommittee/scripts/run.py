"""Launch this experiment group; use --dry-run to inspect commands."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from batch import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main("StaticResearchCommittee"))
