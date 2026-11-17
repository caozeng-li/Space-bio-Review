# SpaceBioReview

[English](README_EN.md) | 简体中文

SpaceBioReview 是用于科学项目申请评审的 Python 程序，提供命令行和异步 Python API。输入申请书与附件，输出 Markdown 报告、结构化结果和运行记录。

## 快速开始

使用 Python 3.11 或 3.12 和 uv。以下命令均在仓库根目录执行；仓库默认使用 Python 3.11。

```sh
uv sync --frozen
uv run playwright install chromium
```

将 [.env.example](.env.example) 复制为 `.env`，填写 `API_KEY`。默认配置通过 DashScope 的 OpenAI 兼容接口调用模型与嵌入服务。Chromium 用于文献获取时的浏览器回退。

运行内置申请示例：

```sh
uv run spacebio-review --application examples/applications/space-cell-pilot.md
```

默认结果目录为 `runs/review-YYYYMMDD-HHMMSS/`，英文报告为其中的 `report.en.md`。

## 命令行

申请书支持 Markdown、TXT、PDF、DOCX 等格式。通过 `--attachment` 添加附件，通过 `--output` 指定结果目录：

```sh
uv run spacebio-review --application examples/applications/flight-cell.md --attachment examples/applications/flight-attachment.md --output runs/flight-review
```

| 参数 | 说明 |
| --- | --- |
| `--application` | 主申请书路径，必填 |
| `--attachment` | 附件路径，可重复指定 |
| `--output` | 输出目录；每次运行使用新目录或空目录 |
| `--env-file` | 环境变量文件，默认 `.env` |
| `--help` | 查看全部参数 |

配置文件的覆盖参数见下表。命令行传入的相对路径均相对于当前工作目录。

## 配置

| 文件 | 配置内容 | 指定方式 |
| --- | --- | --- |
| [config/runtime.yaml](config/runtime.yaml) | 模型、角色映射、并发、预算与会话容量 | `--runtime-config` |
| [config/review.yaml](config/review.yaml) | 评价体系、外部研究、专题并发与运行变体 | `--review-config` |
| [config/evidence.yaml](config/evidence.yaml) | 文献检索、材料解析、缓存与阅读窗口 | `--evidence-config`，默认读取 runtime 中的 `evidence_config` |
| [config/admission.yaml](config/admission.yaml) | 最低准入与可选的计划书内容形式审查 | `--admission-config` |
| [config/reporting.yaml](config/reporting.yaml) | 国标报告的英文译本与翻译分批大小 | `--reporting-config` |
| [config/expertise.yaml](config/expertise.yaml) | 专业与方法技能目录 | runtime 中的 `expertise_config` |

`runtime.yaml` 中的 `skills_dir`、`expertise_config`、`evidence_config` 和 `embedding.cache_dir` 相对于该配置文件所在目录解析。

### 模型与服务

在 `runtime.yaml` 中修改 `models` 下各模型的 `model`、`base_url` 和 `api_key_env`；通过 `roles` 和 `auxiliary` 将任务分配给相应模型配置。嵌入服务单独在 `embedding` 中配置。

| 默认用途 | 模型 |
| --- | --- |
| 主审、专题专家、翻译 | `deepseek-v4-pro` |
| 来源阅读、视觉辅助 | `qwen3.5-397b-a17b` |
| 准入、检索重排 | `deepseek-v4.1-flash` |
| 文本嵌入 | `text-embedding-v4` |

文献服务的凭据和联系邮箱见 [.env.example](.env.example)。启用网页检索需填写 `TAVILY_API_KEY`；检索源列表在 `evidence.yaml` 的 `discovery.enabled` 中配置。

### 常用设置

- **报告语言**：`review.yaml` 的 `evaluation_system: general` 生成英文报告；改为 `gbt42048_2022` 生成国标体系的中文报告。后者可通过 `reporting.yaml` 的 `gbt_english_translation: true` 额外生成英文译本。
- **外部研究**：`external_research_enabled` 默认为 `true`；设为 `false` 时仅使用所提供的材料。
- **并发**：`review.yaml` 的 `parallel_topics` 默认为 `4`；`runtime.yaml` 的 `concurrency.model_requests` 和 `concurrency.tools` 分别默认为 `4` 和 `8`。
- **运行预算**：`runtime.yaml` 的 `budget` 默认设置 `max_seconds: 14400`、`max_model_calls: 600` 和 `max_embedding_calls: 600`。
- **阅读窗口**：`evidence.yaml` 的 `reading_tokens` 默认为 `16000`，`max_reading_tokens` 为 `32000`。

`review.yaml` 的 `ablations` 提供 `single_research_agent`（单专家）、`static_research_committee`（固定专家组）和 `disable_consultation`（关闭咨询）三个开关，默认全部关闭。前两个开关互斥。

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

`run_review()` 的文件列表以主申请书开头，其余为附件，返回 `ReviewResult`。在已有异步程序中直接使用 `await run_review(...)`。

`ReviewConfig` 接受 `runtime_config`、`review_config`、`evidence_config`、`admission_config`、`reporting_config`、`env_file` 和 `output`。`read_result()` 接受已保存的 `review.json` 文件路径，返回原始数据 `data` 和已解析为绝对路径的 `resolved_paths`。

## 输出与运行状态

运行产物随各阶段完成写入输出目录：

| 文件或目录 | 内容 |
| --- | --- |
| `review.json` | 结构化结果，包括运行状态、评价、调用用量与产物路径 |
| `report.en.md` / `report.zh.md` | 对应评价体系与语言设置的最终报告 |
| `application.json` | 载入后的申请与附件信息 |
| `admission.json` / `admission.md` | 准入结果 |
| `metrics.json` | 模型请求与调度统计 |
| `sources/` | 来源材料与解析结果 |
| `v5/` | 会话、专题与研究记录 |

命令行向终端输出进度日志，结束时打印状态和 `review.json` 路径。退出码为 `0`（`completed`）、`2`（`rejected`）或 `1`（`partial`、`failed` 或启动错误）。排查未完成的运行时，可查看 `review.json` 中的 `stage`、各阶段状态和 `technical_limits`。

## 代码结构

| 路径 | 职责 |
| --- | --- |
| [src/spacebio_review/cli.py](src/spacebio_review/cli.py)、[runner.py](src/spacebio_review/runner.py) | 命令行、Python API 与结果导出 |
| [src/spacebio_review/v5/](src/spacebio_review/v5/) | 主审与专家会话、任务协作、预算、评价与报告写作 |
| [src/spacebio_review/evidence/](src/spacebio_review/evidence/) | 文献发现、材料获取与解析、检索和来源存储 |
| [src/spacebio_review/agents/](src/spacebio_review/agents/) | 智能体运行时、上下文与消息协议 |
| [src/spacebio_review/models/](src/spacebio_review/models/)、[tools/](src/spacebio_review/tools/) | 模型客户端、请求调度与工具接口 |
| [src/spacebio_review/review/](src/spacebio_review/review/)、[publication/](src/spacebio_review/publication/) | 评价体系、计分、报告装配与渲染 |
| [skills/](skills/) | 专业、方法与评价提示 |
| [examples/](examples/) | 申请样例与模块调用示例 |
| [scripts/](scripts/) | 开发检查与报告交付辅助脚本 |

## 实验材料

- [experiments/](experiments/)：实验输入、配置、各方法报告与运行脚本。
- [results2/README.md](results2/README.md)：评价结果及图表入口。
- [results2/scripts/](results2/scripts/)：评价数据整理、统计与绘图脚本。
