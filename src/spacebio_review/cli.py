import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from .runner import ReviewConfig, run_review


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="V4.1 科学项目评审：问题研究、整体综合与双语交付")
    parser.add_argument("--application", required=True, help="主申请文件")
    parser.add_argument("--attachment", action="append", default=[])
    parser.epilog = "V4.1 按问题保存结果；--resume 在输入与配置一致时继续已有 V4.1 运行。"
    parser.add_argument("--resume", action="store_true", help="恢复 --output 指向的 V4.1 运行")
    for name in ["runtime", "review", "evidence", "admission", "reporting"]:
        parser.add_argument(f"--{name}-config", default=f"config/{name}.yaml")
    parser.add_argument("--evaluation-skill")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default=f"runs/review-{datetime.now():%Y%m%d-%H%M%S}")
    args = vars(parser.parse_args())
    paths = [args.pop("application"), *args.pop("attachment")]
    config = ReviewConfig(**args)
    print(
        "V4.1 问题研究与整体综合；模型与向量服务按配置启用；文献入口按 evidence 配置启用；"
        "通用网页搜索仅在配置 Tavily 后可用。",
        flush=True,
    )
    result = asyncio.run(run_review(paths, config))
    print(
        f"status={result.status}; reporting={result.reporting_status}; "
        f"science={result.scientific_status}; "
        f"research={result.research_status}; "
        f"tasks={result.tasks_used}; result={Path(result.result_path)}"
    )
    return 0 if result.status == "completed" else 2 if result.status == "rejected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
