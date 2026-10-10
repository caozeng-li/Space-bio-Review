# SpaceBioReview

V4.1 科学项目评审 MAS，用于辅助立项与资源配置。按具体问题配置专业研究，综合科学价值、创新性、严谨性、可行性、安全与项目管理，形成支持条件及方案取舍。

## 运行与恢复

```powershell
uv run spacebio-review --application data/sample1.md --output runs/my-review
uv run spacebio-review --application data/sample1.md --output runs/my-review --resume
```

主申请通过 `--application` 指定，附件重复使用 `--attachment`。服务凭据按 `.env.example` 配置。新运行使用空输出目录；恢复要求原始材料、配置一致。

当前检查点格式为 `v4.1-3`，证据按实际阅读内容保存。`v4.1-2`（包括 `runs/mytest13_complete`）、`v4.1-1`（包括 `runs/mytest8`）、`v4r-2` 和旧 V3 检查点不能直接恢复，修复后请使用新的输出目录。旧运行可以通过 `runner.read_result(path)` 原样读取；不自动升级、不改写、不回退到旧执行流程。

Windows 项目环境通过 `uv sync` 安装 PyTorch 2.14.1 / torchvision 0.29.1 的 CUDA 13.0 构建，下载源在 `pyproject.toml` 中显式绑定到 PyTorch 官方索引（采用 [uv 官方配置方式](https://docs.astral.sh/uv/guides/integration/pytorch/)）。需要兼容的 NVIDIA 驱动；Docling 保持 `auto`，可用时选择 GPU。版面模型仍从 Hugging Face 缓存加载，`already cached` 是正常日志。OCR 等其他子组件可能仍使用 CPU；GPU 加速主要用于本地文档模型，百炼评审模型依然远程调用。更换环境后需重启已启动的 Python/评审进程。

## 实际流程

1. 解析材料、执行最低准入；分批理解项目路径并动态配置专家。
2. 专家定位与统筹形成专业问题。六方面不是六个固定专家。
3. 正式计划用 expert_tasks 指定成员及专家分析范围；旧提案标签不再取消派工。研究实际读取成员材料，scope_outcomes 记录每名专家完成的成员范围。
4. 综合按投影后的上下文容量组织完整论证，每个已评方面只发布一个最终产物；过大时先分批再归并，可回读原意见。补研按真实论证和已获准工作分类，非空新研究波次才计轮次。
5. 冻结科学结果及最新缺口处置，中文主报告使用最终综合，专家原文另存附录；只生成摘要和主报告英译。写作不再生成科学补研请求。
6. 请求、预算、恢复统一协调。专业失败不删除其他意见，英文失败不重生成已接受中文。

专业正文用完整论证块承载；身份、块编号、版本、来源映射由程序管理。目录可选或引用可解析不代表科学论断已被证实。中文主体正常不再调用模型重写。

摘要、正文和图像解释在返回给任务时保存阅读快照；搜索元数据补全或取得全文不会改写已读证据。内容变化产生新的证据身份，委派研究通过 `knowledge/*/evidence-readings.json` 传递快照。专业与综合草稿在文本中使用 `[@E1]`、`[@R1]` 引用，程序绑定来源并生成引用列表。补研请求同样绑定引用，携带来源快照进入路由，再在新任务中分配局部编号；`project` 等背景标签不属于引用目录。提交失败时支持对出错单元进行局部修复，其余内容由程序保留。生成协议版本为 `v4.1-generation-7`，参与执行缓存标识，避免复用旧协议结果。

## 配置

| 文件 | 用途 |
|---|---|
| `config/runtime.yaml` | 现有模型、服务、生成能力、正式提交路由和并发 |
| `config/review.yaml` | 总请求/时间预算、专业任务、补研轮数及并发；可选 `max_input_tokens`、`max_output_tokens` |
| `config/preparation.yaml`、`config/expertise.yaml` | 专长、方法 Skills、读取及评价语境 |
| `config/evidence.yaml` | 文献发现、获取和读取 |
| `config/admission.yaml` | 最低准入 |
| `config/reporting.yaml` | 摘要/翻译额度、上下文、按完整段落分批大小及有限修复 |

配置文件标签仍为 `version: v4`（配置格式），与检查点格式 `v4.1-3` 分开。仅维护当前配置和执行链路；V3 配置、评审及写作链路已移除。保留现有科学模型与百炼服务；配置中的 JSON object 模式不等于服务严格约束 schema。

预算在实际 HTTP 请求前预留，委派、模型重试、嵌入请求使用同一账本，其中生成请求和嵌入请求分别计数、分别限额。当前默认各 600 次，综合与交付在生成额度内预留 80 次及 1800 秒。成功后用服务用量对账；失败或用量不明保守保留预占。恢复不重置额度。写作额度是总额度内的子上限。

## 输出

| 路径 | 内容 |
|---|---|
| `review.json` | `schema_version: "4.1"`；项目、任务、产物、综合、覆盖和交付状态 |
| `report.md`、`report.en.md` | 程序装配的中文报告、对应英文；保留可用部分 |
| `appendix.md` | 专家完整原文、成员分析范围及后续问题；主报告使用相对链接 |
| `delivery.json` | 当前固定清单下的中文、英文和原文附录交付详情 |
| `v4/state.json` | 仅协调器写入的任务、当前版本和请求状态 |
| `v4/budget.json` | 跨恢复累计的请求及用量账本 |
| `v4/*-<hash>.json` | 不可变专业产物、方面判断、整体判断、清单和提交记录 |
| `v4/receipts/` | 正式输出及当时阅读记录/目录的恢复入口 |
| `sources/`、`knowledge/` | 原始资料及文献研究产物 |
| `metrics.json` | 当前会话耗时和用量；累计额度以预算账本为准 |

`committed` 表示产物保存并登记，科学未知也可正常提交。`research_status` 与 `coverage` 表示实际研究范围；`scientific_status` 表示综合是否形成，不是科学真伪认证。`completed` 表示当前范围的双语主报告和原文附录交付完成，不意味着六方面全部已评。`partial` 保留已有结果，`rejected` 仅指准入拒止。

## 验证与限制

当前实现入口见 `src/spacebio_review/runner.py`，协调流程见 `src/spacebio_review/v4/`。本次清理范围与验证见 [implementation/cleanup-2026-10-08.md](implementation/cleanup-2026-10-08.md)。`todo/` 保留当前检索、嵌入缓存、研究到交付三个 change 的实施记录。

新检查点不恢复旧运行；持久化报告路径为相对路径，`read_result()` 的 `resolved_paths` 按读取目录解析。

只执行选定的局部边界，不运行历史 `tests/`、完整测试目录或完整评审：

```powershell
uv run python -m pytest tests_current/test_work_scope.py -q
uv run python -m pytest tests_current/test_reading_submission.py -q
uv run python -m pytest tests_current/test_delivery_boundaries.py -q
```

当前测试入口目录为 `tests_current/`。`scripts/v41_fixture.py` 及 `scripts/fixtures/v41/` 是当前局部测试仍在使用的夹具；旧完整流程检查和失效探针已移除。

`scripts/probe_mytest16_retrieval.py`、`probe_mytest16_cache.py`、`probe_mytest16_completion.py` 保留为当前实现的局部真实探针，依赖对应历史运行材料，显式执行才调用服务。`examples/` 保留独立运行时、准入和文献示例，`scripts/check_gpu_environment.py` 用于环境检查。清理验收未执行这些真实请求，也不以局部测试结果证明完整评审的科学质量。
