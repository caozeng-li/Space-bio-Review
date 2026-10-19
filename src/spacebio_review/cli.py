import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from .runner import ReviewConfig, run_review


def build_parser():
    parser = argparse.ArgumentParser(description="v5 科学项目评审：持续主审、专题研究与直接交付")
    parser.add_argument("--application", required=True, help="主计划书文件")
    parser.add_argument("--attachment", action="append", default=[], help="附件，可多次指定")
    parser.epilog = "每次运行使用新的输出目录；评价体系由 review 配置，交付选项由 reporting 配置。"
    for name in ("runtime", "review", "admission", "reporting"):
        parser.add_argument(f"--{name}-config", default=f"config/{name}.yaml")
    parser.add_argument("--evidence-config", help="覆盖 runtime 中的 evidence_config 路径")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default=f"runs/review-{datetime.now():%Y%m%d-%H%M%S}")
    return parser


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    args = vars(build_parser().parse_args())
    paths = [args.pop("application"), *args.pop("attachment")]
    print("v5 持续主审与专题研究；评价体系、外部资料与英文交付按配置执行。", flush=True)
    try:
        result = asyncio.run(run_review(paths, ReviewConfig(**args)))
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"status={result.status}; reporting={result.reporting_status}; "
        f"science={result.scientific_status}; research={result.research_status}; "
        f"result={Path(result.result_path)}"
    )
    return 0 if result.status == "completed" else 2 if result.status == "rejected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
