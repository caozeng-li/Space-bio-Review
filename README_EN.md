# SpaceBioReview

English | [简体中文](README_CN.md)

SpaceBioReview is a Python application for reviewing scientific research proposals, with a command-line interface and an asynchronous Python API. It takes a proposal and attachments and produces Markdown reports, structured results, and execution records.

## Quick start

Use Python 3.11 or 3.12 and uv. Run the commands below from the repository root; the repository defaults to Python 3.11.

```sh
uv sync --frozen
uv run playwright install chromium
```

Copy [.env.example](.env.example) to `.env` and set `API_KEY`. The default configuration uses DashScope's OpenAI-compatible endpoints for model and embedding requests. Chromium provides the browser fallback for source acquisition.

Run the included sample proposal:

```sh
uv run spacebio-review --application examples/applications/space-cell-pilot.md
```

Results are saved to `runs/review-YYYYMMDD-HHMMSS/` by default, with the English report in `report.en.md`.

## Command-line usage

Proposal inputs support formats including Markdown, TXT, PDF, and DOCX. Add attachments with `--attachment` and select an output directory with `--output`:

```sh
uv run spacebio-review --application examples/applications/flight-cell.md --attachment examples/applications/flight-attachment.md --output runs/flight-review
```

| Argument | Description |
| --- | --- |
| `--application` | Main proposal path; required |
| `--attachment` | Attachment path; repeat for multiple files |
| `--output` | Output directory; use a new or empty directory for each run |
| `--env-file` | Environment file; defaults to `.env` |
| `--help` | Show all arguments |

Configuration overrides are listed below. Relative paths passed on the command line are resolved from the current working directory.

## Configuration

| File | Settings | Override |
| --- | --- | --- |
| [config/runtime.yaml](config/runtime.yaml) | Models, role assignments, concurrency, budgets, and session capacity | `--runtime-config` |
| [config/review.yaml](config/review.yaml) | Evaluation system, external research, topic concurrency, and runtime variants | `--review-config` |
| [config/evidence.yaml](config/evidence.yaml) | Literature search, document parsing, caching, and reading windows | `--evidence-config`; defaults to runtime's `evidence_config` |
| [config/admission.yaml](config/admission.yaml) | Minimum admission checks and optional proposal format review | `--admission-config` |
| [config/reporting.yaml](config/reporting.yaml) | English translation of GB/T reports and translation batch size | `--reporting-config` |
| [config/expertise.yaml](config/expertise.yaml) | Domain and methodological skill catalogue | Runtime's `expertise_config` |

In `runtime.yaml`, `skills_dir`, `expertise_config`, `evidence_config`, and `embedding.cache_dir` are resolved relative to that configuration file's directory.

### Models and services

Edit `model`, `base_url`, and `api_key_env` under each entry in `runtime.yaml`'s `models` section. The `roles` and `auxiliary` mappings assign tasks to those model configurations. Configure embeddings separately under `embedding`.

| Default use | Model |
| --- | --- |
| Chief, topic researchers, and translation | `deepseek-v4-pro` |
| Source reading and visual assistance | `qwen3.5-397b-a17b` |
| Admission and search reranking | `deepseek-v4.1-flash` |
| Text embeddings | `text-embedding-v4` |

See [.env.example](.env.example) for literature-service credentials and contact email settings. Set `TAVILY_API_KEY` to enable web search. Select discovery providers through `discovery.enabled` in `evidence.yaml`.

### Common settings

- **Report language:** `evaluation_system: general` in `review.yaml` produces an English report. Set it to `gbt42048_2022` for a Chinese report using the GB/T evaluation system. For the latter, set `gbt_english_translation: true` in `reporting.yaml` to also produce an English translation.
- **External research:** `external_research_enabled` defaults to `true`; set it to `false` to use only the supplied materials.
- **Concurrency:** `parallel_topics` in `review.yaml` defaults to `4`. In `runtime.yaml`, `concurrency.model_requests` and `concurrency.tools` default to `4` and `8`.
- **Run budget:** The `budget` section in `runtime.yaml` defaults to `max_seconds: 14400`, `max_model_calls: 600`, and `max_embedding_calls: 600`.
- **Reading windows:** In `evidence.yaml`, `reading_tokens` defaults to `16000` and `max_reading_tokens` to `32000`.

The `ablations` section in `review.yaml` offers `single_research_agent` (one researcher), `static_research_committee` (a fixed committee), and `disable_consultation` (consultation disabled). All default to `false`; the first two are mutually exclusive.

## Python API

```python
import asyncio

from spacebio_review import ReviewConfig, read_result, run_review

result = asyncio.run(
    run_review(
        ["examples/applications/space-cell-pilot.md"],
        ReviewConfig(output="runs/api-review"),
    )
)
print(result.status, result.result_path)

saved = read_result(result.result_path)
print(saved["resolved_paths"])
```

`run_review()` takes a list with the main proposal first, followed by any attachments, and returns a `ReviewResult`. In an existing asynchronous application, use `await run_review(...)` directly.

`ReviewConfig` accepts `runtime_config`, `review_config`, `evidence_config`, `admission_config`, `reporting_config`, `env_file`, and `output`. `read_result()` takes the path to a saved `review.json` file and returns its original `data` plus `resolved_paths` containing absolute output paths.

## Outputs and run status

Artifacts are written to the output directory as their respective stages finish:

| File or directory | Contents |
| --- | --- |
| `review.json` | Structured results, including run status, assessment, usage, and artifact paths |
| `report.en.md` / `report.zh.md` | Final reports for the selected evaluation system and language settings |
| `application.json` | Loaded proposal and attachment information |
| `admission.json` / `admission.md` | Admission results |
| `metrics.json` | Model request and scheduling statistics |
| `sources/` | Source materials and parsed documents |
| `v5/` | Session, topic, and research records |

The CLI writes progress logs to the terminal and prints the final status and `review.json` path. Exit codes are `0` for `completed`, `2` for `rejected`, and `1` for `partial`, `failed`, or a startup error. For an unfinished run, inspect `stage`, individual stage statuses, and `technical_limits` in `review.json`.

## Code layout

| Path | Responsibility |
| --- | --- |
| [src/spacebio_review/cli.py](src/spacebio_review/cli.py), [runner.py](src/spacebio_review/runner.py) | CLI, Python API, and result export |
| [src/spacebio_review/v5/](src/spacebio_review/v5/) | Chief and researcher sessions, task coordination, budgets, assessment, and report writing |
| [src/spacebio_review/evidence/](src/spacebio_review/evidence/) | Literature discovery, acquisition, parsing, retrieval, and source storage |
| [src/spacebio_review/agents/](src/spacebio_review/agents/) | Agent runtime, context, and message protocols |
| [src/spacebio_review/models/](src/spacebio_review/models/), [tools/](src/spacebio_review/tools/) | Model clients, request scheduling, and tool interfaces |
| [src/spacebio_review/review/](src/spacebio_review/review/), [publication/](src/spacebio_review/publication/) | Evaluation systems, scoring, report assembly, and rendering |
| [skills/](skills/) | Domain, methodological, and evaluation prompts |
| [examples/](examples/) | Sample proposals and module usage examples |
| [scripts/](scripts/) | Development checks and report delivery utilities |

## Experiment materials

- [experiments/](experiments/): Experimental inputs, configurations, method reports, and run scripts.
- [results2/README.md](results2/README.md): Evaluation results and figures.
- [results2/scripts/](results2/scripts/): Evaluation data processing, statistics, and plotting scripts.
