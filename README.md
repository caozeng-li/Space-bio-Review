# SpaceBioReview v5

科学项目评审系统。持续主审阅读计划书，按科学问题直接研究或委派专题研究员；同一主审在已有研究语境中形成最终评价与摘要。代码负责资源计量、身份、国标计分、引用和报告装配。

v5 是唯一生产评审路径，支持 `general` 和 `gbt42048_2022` 两套评价体系。局部接线通过不代表完整评审科学质量已经得到验证；当前工具参数稳定性、图表数值读取和科学外推仍有已知限制。

## 运行与恢复

```powershell
uv run spacebio-review --application data/sample1.md --output runs/my-review
uv run spacebio-review --application data/sample1.md --output runs/my-review --resume
```

主计划书使用 `--application`，附件可以重复指定 `--attachment`。服务凭据按 `.env.example` 配置。新运行要求空输出目录。恢复只支持 `v5-review-1` 检查点，并要求计划书、附件、有效配置、能力目录和 Skills 内容一致。已保存的研究稿、原文和资源用量继续使用，完成的报告不会重新调用模型。

历史 v4 运行不能续跑或自动迁移。`spacebio_review.read_result(path)` 可以原样读取历史 `review.json`，并在 `resolved_paths` 中解析报告的相对路径；不会改写历史结果。

安装依赖使用 `uv sync`。本地文档解析由 Docling 及当前环境支持的设备完成，模型评审请求按配置访问远程服务。

## 实际流程

1. 解析计划书及附件，执行现有最低准入，或可选的国标计划书内容形式审查。
2. 持续主审接收完整短计划书；长材料通过同一资料入口阅读完整实验链。主审维护公开工作稿和动态议程。
3. 主审可以直接检索、获取和阅读原文，也可以批量委派独立专题。同一专题的续问进入原会话，研究员不再委派下一层研究者。研究说明和实际引用原文一同返回主审。
4. 同一主审提交各项评价；国标总分、等级和安全否决后的支持结论由代码计算，再反馈主审形成综合评价和摘要。
5. 主审正文和明确选定版本的专题说明直接装配中文报告。英文开启时只翻译已经确定的公开内容，翻译失败保留中文成果。

主审与专题的技术中断和科学未知分别记录。报告没有向申请方布置补材料、补实验或修改路线等整改任务。没有固定题单、按题专家数量或按专题数量计算的充分率，也没有独立综合作者或出版作者重写科学正文。

## 配置

| 文件 | 用途 |
|---|---|
| `config/review.yaml` | `version: v5`、评价体系、是否允许外部资料研究、同时执行的专题数、历史截止日期 |
| `config/runtime.yaml` | 主审与专题模型、准入/重排/视觉/翻译辅助模型、全局资源、会话资源、请求并发与嵌入服务 |
| `config/expertise.yaml` | 学科和方法能力目录；由主审按需选择 |
| `config/evidence.yaml` | 外部文献发现、获取与文档读取 |
| `config/admission.yaml` | 最低准入和 `formal_review_enabled` 计划书内容形式审查开关 |
| `config/reporting.yaml` | `english_enabled` 英文开关及翻译分批大小 |

国标配置：

```yaml
# config/review.yaml
version: v5
evaluation_system: gbt42048_2022
external_research_enabled: true
parallel_topics: 4
historical_as_of: null
```

改为 `evaluation_system: general` 使用通用定性体系。国标只评价基于已有载荷实施的实验项目，六项满分依次为 24、22、18、18、6、12；general 保留六方面的 A—E 等级及依据置信，不生成国标总分。

`formal_review_enabled` 在本次迁移中保留原来的 `false`。启用时只审查计划书必要内容覆盖，不核查征集主题、资格、保密审批、诚信、签章或配套十二份国标。关闭时真实记录为 `skipped`，不伪装成通过。

`external_research_enabled: false` 时主审和专题均只使用已提供的材料，不进行外部搜索或获取。`parallel_topics` 限制实际同时执行的专题数；`runtime.concurrency.model_requests` 则限制全局实际模型请求并发，二者职责不同。

英文交付默认保留开启。只要中文时设置：

```yaml
# config/reporting.yaml
version: v5
english_enabled: false
material_batch_chars: 18000
```

此时不发起翻译请求，不产生英文报告路径，中文与选定附录完成即为 `completed`。

`config/preparation.yaml`、`config/assessment.yaml` 和 `--assessment-config` 已删除：能力目录归 runtime，最终评价模型使用 `roles.chief`，翻译使用 `auxiliary.translation`。`--evaluation-skill` 也已删除，评价核心由 `review.evaluation_system` 选择。旧 `max_questions`、`question_attempts`、`parallel_questions`、`literature_enabled` 和旧 formal 路由不再接受；外部资料开关改用上述 `external_research_enabled`。不能将 v4 配置直接用于新入口。

实际 HTTP 尝试及重试均计入同一个 `v5/budget.json`。主审、专题和辅助调用有明确归属；嵌入请求独立计数。主审保留完成评价和摘要的资源，专题委派等待不占模型并发位置。恢复不会重置已经消耗的额度。

## 输出

| 路径 | 内容 |
|---|---|
| `review.json` | `v5-review-result-1`；体系、准入、主审、动态议程、专题、实际阅读范围、未知、评价及技术状态 |
| `report.zh.md`、`appendix.md` | 主审直接形成的中文主报告、选定版本的专题说明 |
| `report.en.md` | 开启英文且成功时的英文主报告 |
| `admission.json`、`admission.md` | 实际准入/内容形式审查记录 |
| `v5/review-state.json` | 输入配置指纹、材料、准入和主审身份，用于同版本恢复 |
| `v5/budget.json` | 跨恢复累计的实际请求与资源消耗 |
| `v5/sessions/`、`v5/notes/` | 持续会话的公开状态和不可变研究稿 |
| `v5/chiefs/`、`v5/topics/`、`v5/deliveries/` | 动态议程、专题身份与交回的明确版本 |
| `v5/assessments/`、`v5/publications/`、`v5/delivery.json` | 最终评价、确定性出版产物与分语言交付状态 |
| `sources/` | 原始资料、取得的文本与图像 |
| `metrics.json` | 本次执行的请求并发和耗时记录；累计消耗以预算账本为准 |

状态根据实际产物形成。`completed` 表示本次启用的报告已经交付，不是科学质量认证；`partial` 保留已有研究稿和评价，`rejected` 只指准入拒止。没有报告时不写技术错误占位报告。公开报告路径以相对路径保存，可随整个运行目录移动。

## 局部验收与限制

实施依据和记录见 `todo/09-v5-continuous-research-runtime.md` 至 `todo/13-v5-entry-migration-and-local-acceptance.md`，局部证据位于 `runs/v5-local/change09/` 至 `change13/`。

本轮使用各环节的专用离线探针和受限真实片段，不运行 `tests/`，不启动从准入到最终报告的完整评审。`scripts/probe_v5_entry.py` 检查配置、准备边界和恢复，`scripts/probe_v5_session.py`、`probe_v5_topic.py`、`probe_v5_chief.py` 分别覆盖持续会话、专题交接和主审协调。真实探针须明确停止点和总调用上限，不能以重跑完整流程补充局部验收。

Change 10 的文献研究未稳定形成有效科学稿，独立图表片段出现数值与外推错误；Change 11 的受限 sample2 实验未形成有效研究稿。Change 12 的固定材料评价在 4 次真实请求内提交六项评分和摘要，但仍有无依据的安全与领先水平判断；计分前整体结论与计算结果冲突的问题已通过调整生成顺序处理，仅完成离线复验。入口切换及确定性装配不能代替科学质量证据。后续完整运行的质量结论应由实际结果判断。
