# SpaceBioReview

V4.4 科学项目评审 MAS，用于辅助立项与资源配置。单一模型生成全局有序题单，代码截取并派给按学科与方法能力配置的专家；随后一次综合直接形成最终主文与评定，另行写作仅负责摘要和专题附录。

当前默认使用新综合与交付链路，旧逐维综合、专家原文拼装、旧摘要及出版编辑链已删除，没有回退开关。报告交付完成不等于评审语义正确；科学质量仍需单独研究。实施与验证记录见 [实施结果](todo/change-implementation-results.md)。

## 运行与恢复

```powershell
uv run spacebio-review --application data/sample1.md --output runs/my-review
uv run spacebio-review --application data/sample1.md --output runs/my-review --resume
```

主申请通过 `--application` 指定，附件重复使用 `--attachment`。服务凭据按 `.env.example` 配置。新运行使用空输出目录；恢复要求原始材料、配置一致。

当前检查点格式为 `v4.4-direct-publication-1`，证据按实际阅读内容保存。旧 `v4.3-*`、`v4.2-*`、`v4.1-*` 和更早检查点不能直接恢复，请使用新的输出目录。旧运行可以通过 `runner.read_result(path)` 原样读取；局部探针可只读提取历史申请与专家产物。不自动升级、不改写、不回退到旧执行流程。题单生效前可仅改变 `max_questions` 后恢复；生效后数量和题单均冻结。所选共同评价 Skill 及其参考材料也计入恢复指纹。

Windows 项目环境通过 `uv sync` 安装 PyTorch 2.14.1 / torchvision 0.29.1 的 CUDA 13.0 构建，下载源在 `pyproject.toml` 中显式绑定到 PyTorch 官方索引（采用 [uv 官方配置方式](https://docs.astral.sh/uv/guides/integration/pytorch/)）。需要兼容的 NVIDIA 驱动；Docling 保持 `auto`，可用时选择 GPU。版面模型仍从 Hugging Face 缓存加载，`already cached` 是正常日志。OCR 等其他子组件可能仍使用 CPU；GPU 加速主要用于本地文档模型，百炼评审模型依然远程调用。更换环境后需重启已启动的 Python/评审进程。

## 实际流程

1. 解析材料、执行最低准入；分批理解项目路径并动态配置专家。
2. 单一 `question_generation` 模型只读取完整计划书和激活专家能力，一次生成按重要性排序的题单。专家不参与出题，六方面不是六个固定专家。
3. 保存完整题单，代码严格取前 `max_questions` 项（默认 10，可配置），一题一位专家；不语义合并、不补题、不再次分配。模型输入中没有题数上限。
4. 专家以题目为议题、以申请原文为方案依据，不再默认读取完整项目解释稿。需要外部资料时，提出具体信息问题并选择申请原文，以 `application_statements` 精确交接；不自动把完整题干复制为事实背景。文献模块负责发现、获取和阅读，自然结束后通过一次原生 Schema 生成 `findings/applicability/unknowns`，代码绑定选定原文；专家仍可复核已取得资料。未决事项保留，不触发自动补充研究波次。
5. 冻结申请全文、专家论述、其实际沿袭的文献论述、各自共同范围、实际证据和同源评价标准，隔离任务状态与审计记录；一次全局综合直接形成六维最终正文、评分、整体建议、条件和专题安排。直接引用与沿袭关系分开，不将所有可用归档依赖自动展开为直接证据。
6. 摘要仅压缩已完成主文；专题附录按指定主文章节与完整专业材料展开。代码生成自然段、条件分组、评分表、引用编号、索引和书目；英文仅翻译公开文字。
7. 请求、预算、恢复统一协调。专业失败不删除其他意见，英文失败不重生成已接受中文；下游不自动修补或重抽。

专业正文用完整论证块承载；身份、块编号、版本、来源映射由程序管理。目录可选或引用可解析不代表科学论断已被证实。主笔依据明确分配的材料撰写读者稿，不再逐字段导出专家原文。

摘要、正文和图像解释在返回给任务时保存阅读快照；搜索元数据补全或取得全文不会改写已读证据。内容变化产生新的证据身份，委派研究通过 `knowledge/*/evidence-readings.json` 传递快照，只有实际交回的选定原文进入专家阅读记录。综合和写作使用独立的原生 schema 及 `paragraphs/parts/text/cites` 接口，内部意见身份不作为文献递归展开。问题生成和新下游均保存完整响应后本地解析，恢复不隐式重新付费生成。

## 配置

| 文件 | 用途 |
|---|---|
| `config/runtime.yaml` | 问题生成与研究模型、服务、正式提交路由和共享并发 |
| `config/assessment.yaml` | 全局综合、报告主笔和翻译模型，以及每阶段输出和上下文额度 |
| `config/review.yaml` | `max_questions`（默认 10）、总请求/时间预算、专业任务及并发；可选 `max_input_tokens`、`max_output_tokens` |
| `config/preparation.yaml`、`config/expertise.yaml` | 专长、方法 Skills、读取及评价语境 |
| `config/evidence.yaml` | 文献发现、获取和读取 |
| `config/admission.yaml` | 最低准入 |
| `config/reporting.yaml` | 写作请求子上限、翻译按完整段落分批大小 |

review/reporting 配置文件标签仍为 `version: v4`，与检查点格式分开。`--assessment-config` 可指定下游配置。题数上限只控制后续研究，不约束完整题单的生成费用；完整原文超出模型容量时在发送前停止，不截原文或改成分段出题。专家研究路由不变；旧 assessment/writer 运行时路由和报告修复配置已删除。

预算在实际 HTTP 请求前预留，委派、模型重试、嵌入请求使用同一账本，其中生成请求和嵌入请求分别计数、分别限额。当前默认各 600 次，综合与交付在生成额度内预留 80 次及 1800 秒。成功后用服务用量对账；失败或用量不明保守保留预占。恢复不重置额度。写作额度是总额度内的子上限。

## 输出

| 路径 | 内容 |
|---|---|
| `review.json` | `schema_version: "4.4"`；研究状态、全局综合、六维评定和交付收据 |
| `report.md`、`report.en.md` | 综合直接成稿的中文报告和对应英文；仅公开实际生成的文件路径 |
| `appendix.md` | 按科学主题撰写的连贯附录，不是专家记录导出 |
| `delivery.json` | 中文、英文和专题附录的交付详情，技术故障只记录在收据中 |
| `v4/state.json` | 仅协调器写入的任务、完整题单引用、选择设置和当前版本 |
| `v4/budget.json` | 跨恢复累计的请求及用量账本 |
| `v4/*-<hash>.json` | 不可变专业产物、冻结输入、全局评定、稿件单元、出版清单和提交记录 |
| `v4/receipts/` | 正式输出及当时阅读记录/目录的恢复入口 |
| `sources/`、`knowledge/` | 原始资料及文献研究产物 |
| `metrics.json` | 当前会话耗时和用量；累计额度以预算账本为准 |

`committed` 表示产物保存并登记。`research_status` 表示选中问题的研究执行情况，`coverage` 保留题目层面的范围记录，不决定六维综合是否存在。`assessment_status` 表示全局综合是否提交，`dimension_assessments` 保存六维评定；`scientific_status` 是兼容保留的产物形成状态，不是质量认证。`completed` 表示双语主报告和专题附录已交付。`partial` 保留已有结果，`rejected` 仅指准入拒止。未生成报告时不创建夹带技术信息的占位报告。

## 验证与限制

当前实现入口见 `src/spacebio_review/runner.py`，协调流程见 `src/spacebio_review/v4/`。Change 48-51 已更新论述交接、文献原生提交、专属出题上下文及原文委派，局部真实验收按 Change 52 进行；接口与科学判断分别验收。实际结果及未通过项见 [实施记录](todo/change-implementation-results.md)。没有线上语义审核或失败后切回旧链的开关。

本轮根检查点结构未变，仍使用 `v4.4-direct-publication-1`；请求与产物以 `questions-7`、`professional-input-2`、`review-knowledge-2`、`literature-4`、`frozen-review-4` 区分。正式路由、技能、完整输入与合同进入既有指纹；新配置不能直接恢复旧配置运行。旧文献 `scientific-2/3` 可只读投影，不作为新任务的缓存答案。

新检查点不恢复旧运行；持久化报告路径为相对路径，`read_result()` 的 `resolved_paths` 按读取目录解析。

离线测试不调用真实服务；局部真实出题使用独立探针，不执行完整评审：

```powershell
uv run python -m pytest -q
uv run python scripts/probe_question_generation.py --output runs/question-preview --preview
```

当前测试入口目录为 `tests/`。`scripts/v41_fixture.py` 及 `scripts/fixtures/v41/` 仍用于局部测试。探针预览不付费；`--live --case NAME` 才调用服务，且须有冻结预览与协议探针。历史完整矩阵已经结束，不能在原账本中隐式重抽。

`scripts/probe_review_quality.py` 验证冻结材料的综合与出版，`probe_review_evidence.py` 验证局部文献交接，`probe_review_reasoning.py` 诊断固定材料的模型理解，`probe_review_questions.py` 只测试出题。四组按 Change 47 固定矩阵使用同一个预算账本；先 `preview` 冻结请求，再执行指定用例，失败保留且不自动重抽。`probe_assessment_publication.py` 转发到新的综合探针。依赖旧综合/出版链的四个历史探针已删除，仍服务于研究、读取和缓存的探针保留。`examples/` 保留独立运行时、准入和文献示例，`scripts/check_gpu_environment.py` 用于环境检查。局部测试结果不代表完整评审的科学质量。

Change 52 使用 `scripts/probe_mytest25_interfaces.py` 的 B/C 固定提交与出题，以及独立局部专业/文献链探针；全部按固定矩阵顺序执行，共用一个预算账本。预览不付费，真实执行仅限已授权用例，不运行完整评审或购买综合与报告。
