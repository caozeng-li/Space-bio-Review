# SpaceBioReview

V4.2 科学项目评审 MAS，用于辅助立项与资源配置。单一模型生成全局有序题单，代码截取并派给按学科与方法能力配置的专家，综合科学价值、创新性、严谨性、可行性、安全与项目管理。

当前已替换旧问题合并流程，但局部真实出题仍出现错误前提，科学质量验收尚未通过。实施与验证记录见 [Change 32-36](todo/change-implementation-results.md)。

## 运行与恢复

```powershell
uv run spacebio-review --application data/sample1.md --output runs/my-review
uv run spacebio-review --application data/sample1.md --output runs/my-review --resume
```

主申请通过 `--application` 指定，附件重复使用 `--attachment`。服务凭据按 `.env.example` 配置。新运行使用空输出目录；恢复要求原始材料、配置一致。

当前检查点格式为 `v4.2-questions-1`，证据按实际阅读内容保存。旧 `v4.1-*`、候选 planning 和更早检查点不能直接恢复，请使用新的输出目录。旧运行可以通过 `runner.read_result(path)` 原样读取；不自动升级、不改写、不回退到旧执行流程。题单生效前可仅改变 `max_questions` 后恢复；生效后数量和题单均冻结。

Windows 项目环境通过 `uv sync` 安装 PyTorch 2.14.1 / torchvision 0.29.1 的 CUDA 13.0 构建，下载源在 `pyproject.toml` 中显式绑定到 PyTorch 官方索引（采用 [uv 官方配置方式](https://docs.astral.sh/uv/guides/integration/pytorch/)）。需要兼容的 NVIDIA 驱动；Docling 保持 `auto`，可用时选择 GPU。版面模型仍从 Hugging Face 缓存加载，`already cached` 是正常日志。OCR 等其他子组件可能仍使用 CPU；GPU 加速主要用于本地文档模型，百炼评审模型依然远程调用。更换环境后需重启已启动的 Python/评审进程。

## 实际流程

1. 解析材料、执行最低准入；分批理解项目路径并动态配置专家。
2. 单一 `question_generation` 模型读取完整计划书、项目结构和激活专家能力，一次生成按重要性排序的题单。专家不参与出题，六方面不是六个固定专家。
3. 保存完整题单，代码严格取前 `max_questions` 项（默认 10，可配置），一题一位专家；不语义合并、不补题、不再次分配。模型输入中没有题数上限。
4. 专家在题内开展阅读、检索与研究；综合仍按上下文容量分批组织完整论证。未决事项作为科学局限保留，不触发自动补充研究波次。
5. 冻结科学结果、实际覆盖和未决内容，沿用现有报告及附录交付。
6. 请求、预算、恢复统一协调。专业失败不删除其他意见，英文失败不重生成已接受中文。

专业正文用完整论证块承载；身份、块编号、版本、来源映射由程序管理。目录可选或引用可解析不代表科学论断已被证实。中文主体正常不再调用模型重写。

摘要、正文和图像解释在返回给任务时保存阅读快照；搜索元数据补全或取得全文不会改写已读证据。内容变化产生新的证据身份，委派研究通过 `knowledge/*/evidence-readings.json` 传递快照。专业、综合和交付沿用现有论述与引用接口；未决内容保留来源，但不再进入请求路由。问题生成单独使用原生 schema、零修复和零重试；完整响应先保存，恢复本地解析或截取，不隐式重新付费生成。

## 配置

| 文件 | 用途 |
|---|---|
| `config/runtime.yaml` | 现有模型、服务、生成能力、正式提交路由和并发 |
| `config/review.yaml` | `max_questions`（默认 10）、总请求/时间预算、专业任务及并发；可选 `max_input_tokens`、`max_output_tokens` |
| `config/preparation.yaml`、`config/expertise.yaml` | 专长、方法 Skills、读取及评价语境 |
| `config/evidence.yaml` | 文献发现、获取和读取 |
| `config/admission.yaml` | 最低准入 |
| `config/reporting.yaml` | 摘要/翻译额度、上下文、按完整段落分批大小及有限修复 |

配置文件标签仍为 `version: v4`（配置格式），与检查点格式分开。`supplemental_rounds` 已退役，加载旧配置会明确报错。题数上限只控制后续研究，不约束完整题单的生成费用；完整原文超出模型容量时在发送前停止，不截原文或改成分段出题。非问题生成路由保持既有设置；JSON object 模式不等于服务严格约束 schema。

预算在实际 HTTP 请求前预留，委派、模型重试、嵌入请求使用同一账本，其中生成请求和嵌入请求分别计数、分别限额。当前默认各 600 次，综合与交付在生成额度内预留 80 次及 1800 秒。成功后用服务用量对账；失败或用量不明保守保留预占。恢复不重置额度。写作额度是总额度内的子上限。

## 输出

| 路径 | 内容 |
|---|---|
| `review.json` | `schema_version: "4.2"`；项目、任务、产物、综合、覆盖和交付状态 |
| `report.md`、`report.en.md` | 程序装配的中文报告、对应英文；保留可用部分 |
| `appendix.md` | 专家完整原文、成员分析范围及后续问题；主报告使用相对链接 |
| `delivery.json` | 当前固定清单下的中文、英文和原文附录交付详情 |
| `v4/state.json` | 仅协调器写入的任务、完整题单引用、选择设置和当前版本 |
| `v4/budget.json` | 跨恢复累计的请求及用量账本 |
| `v4/*-<hash>.json` | 不可变专业产物、方面判断、整体判断、清单和提交记录 |
| `v4/receipts/` | 正式输出及当时阅读记录/目录的恢复入口 |
| `sources/`、`knowledge/` | 原始资料及文献研究产物 |
| `metrics.json` | 当前会话耗时和用量；累计额度以预算账本为准 |

`committed` 表示产物保存并登记，科学未知也可正常提交。`research_status` 与 `coverage` 表示实际研究范围；`scientific_status` 表示综合是否形成，不是科学真伪认证。`completed` 表示当前范围的双语主报告和原文附录交付完成，不意味着六方面全部已评。`partial` 保留已有结果，`rejected` 仅指准入拒止。

## 验证与限制

当前实现入口见 `src/spacebio_review/runner.py`，协调流程见 `src/spacebio_review/v4/`。题单流程和局部验收见 `todo/change-32-*.md` 至 `todo/change-36-*.md`，实际结果见 [实施记录](todo/change-implementation-results.md)。

新检查点不恢复旧运行；持久化报告路径为相对路径，`read_result()` 的 `resolved_paths` 按读取目录解析。

离线测试不调用真实服务；局部真实出题使用独立探针，不执行完整评审：

```powershell
uv run python -m pytest -q
uv run python scripts/probe_question_generation.py --output runs/question-preview --preview
```

当前测试入口目录为 `tests/`。`scripts/v41_fixture.py` 及 `scripts/fixtures/v41/` 仍用于局部测试。探针预览不付费；`--live --case NAME` 才调用服务，且须有冻结预览与协议探针。历史完整矩阵已经结束，不能在原账本中隐式重抽。

旧探针依赖各自历史版本与本地材料，不是当前全流程入口；mytest16 completion 的旧 checkpoint 不可执行恢复，report-citations 的 L6 请求路由已退役。`examples/` 保留独立运行时、准入和文献示例，`scripts/check_gpu_environment.py` 用于环境检查。局部测试结果不代表完整评审的科学质量。
