# SpaceBioReview v5

科学项目评审系统。主审按开放的知识职责安排持续科学作者；作者从计划书独立确定当前问题，研究、咨询并更新科学稿，主审随成果到达形成整体判断。代码负责资源计量、身份、调度、国标计分、引用和报告装配。

v5 是唯一生产评审路径，支持 `general` 和 `gbt42048_2022` 两套评价体系。局部接线通过不代表完整评审科学质量已经得到验证；当前工具参数稳定性、图表数值读取和科学外推仍有已知限制。

## 单次运行

```powershell
uv run spacebio-review --application data/sample1.md --output runs/my-review
```

主计划书使用 `--application`，附件可以重复指定 `--attachment`。服务凭据按 `.env.example` 配置。每次执行要求新的空输出目录，在同一次运行内完成材料准备、研究、评价和交付。科学追问和稿件修订可以持续进行；进程结束后不从旧目录重新启动会话、账本或交付。

`spacebio_review.read_result(path)` 可以原样读取已有 `review.json`，并在 `resolved_paths` 中解析报告的相对路径；保存的原稿、文献和报告是可阅读产物，不是执行检查点。

安装依赖使用 `uv sync`。本地文档解析由 Docling 及当前环境支持的设备完成，模型评审请求按配置访问远程服务。

## 实际流程

1. 解析计划书及附件，执行现有最低准入，或可选的国标计划书内容形式审查。
2. 主审接收完整短计划书；长材料通过同一资料入口读完实验链。首轮直接安排开放的知识职责，不强制先写完整评价草稿。
3. 专题登记立即返回，作者各自研究并持续发布科学稿。主审和作者均可定向咨询；现稿足够时可用短答解释，无须重写。作者等待具体答复时释放执行名额，主审可在任意新进展到达后继续判断。
4. 主审决定继续研究、转交负责人或结束投入。转交保留问题、稿件与原预算账户；已完成问题需要明确续问才开始下一轮研究。结束研究时，主审按精确稿件版本和顺序选择主文与附录，随后收束在途任务。资源触发收束时先冻结候选，主审在同一次综合原稿中明示选文。
5. 科学作者的选定稿件完整进入主文，主审写项目理解、跨问题综合、逐项理由与决定，以及可直接采用的摘要原段落。代码解析主审稿、计算国标结果、统一引用并装配中文报告；英文开启时逐节翻译已确定的主文（包括科学章节及其标题），翻译失败保留中文成果。

定向科学协作与多作者出版（Change 03、04）已接通。选编记录和已解析稿件、证据、原稿段落来源一起保存；后续作者更新或转交不会改变已保存报告。明确返回研究后可以重新选文，同一次运行仍沿用已有累计预算。

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

实际 HTTP 尝试及重试均计入本次运行的 `v5/budget.json`。主审、专题和辅助调用有明确归属；嵌入请求独立计数。主审保留完成评价和摘要的资源，作者等待咨询不占专题执行名额。同次运行内的科学追问和作者交接沿用累计消费，复用现稿短答不重新领取成稿周期。

## 输出

| 路径 | 内容 |
|---|---|
| `review.json` | 体系、准入、主审、动态议程、专题、实际阅读范围、未知、评价及技术状态 |
| `report.zh.md`、`appendix.md` | 科学作者原稿与主审综合共同组成的中文主报告、明确选择的附录（有选择时） |
| `report.en.md` | 开启英文且成功时的英文主报告 |
| `admission.json`、`admission.md` | 实际准入/内容形式审查记录 |
| `v5/budget.json` | 本次运行累计的实际请求与资源消耗 |
| `v5/sessions/`、`v5/notes/` | 持续会话的公开状态和不可变研究稿 |
| `v5/inquiries/` | 稳定科学问题、当前负责人、当前稿和预算账户引用 |
| `v5/chiefs/`、`v5/topics/`、`v5/deliveries/` | 动态议程、专题身份与交回的明确版本 |
| `v5/assessments/`、`v5/publications/`、`v5/delivery.json` | 最终评价、确定性出版产物与分语言交付状态 |
| `sources/` | 原始资料、取得的文本与图像 |
| `metrics.json` | 本次执行的请求并发和耗时记录；累计消耗以预算账本为准 |

状态根据实际产物形成。`completed` 表示本次启用的报告已经交付，不是科学质量认证；`partial` 保留已有研究稿和评价，`rejected` 只指准入拒止。没有报告时不写技术错误占位报告。公开报告路径以相对路径保存，可随整个运行目录移动。

## 局部验收与限制

实施依据和记录见 `todo/09-v5-continuous-research-runtime.md` 至 `todo/13-v5-entry-migration-and-local-acceptance.md`，局部证据位于 `runs/v5-local/change09/` 至 `change13/`。

当前改造范围和记录见 [todo/README.md](todo/README.md)。本轮使用改动相关的定向检查和受限局部探针，不启动从准入到最终报告的完整评审。[协作局部验证](tests/test_v5_collaboration.py) 使用真实运行时和脚本化模型覆盖等待、交叉咨询、贡献投递、回读复用与作者转交；[专题接入验证](tests/test_v5_inquiry_topics.py) 覆盖问题身份、版本冻结及原文锚点更新。[协作探针](examples/directed_collaboration_probe.py) 已完成 dry 和三次 live（共20次调用）：预设咨询与两个 fixture 起始稿产生了有用知识连接，稿件回读与程序性重复成稿已修，科学外推问题仍存在；没有验证模型自主提问。具体消耗与复验结果见 [03 局部记录](todo/03-directed-scientific-collaboration.md#局部验证)。科学稿、评价映射和翻译分别通过局部入口验证；真实探针须有停止点和调用上限。

[多作者成稿探针](examples/multi_author_report_probe.py) 使用 sample2 原文整理的两篇预制科学稿，默认 dry；`--live` 只调用主审与映射各一次，`--fallback` 验证冻结候选的原稿选文。覆盖国标/通用装配、旧版选文、引用身份、原稿来源及单节翻译的定向验证见 [04 实际记录](todo/04-multi-author-report-composition.md#局部验证记录)。预制稿进入主文的成功不表示模型已学会自主发现这些知识，科学判断仍须看实际结果。

Change 10 的文献研究未稳定形成有效科学稿，独立图表片段出现数值与外推错误；Change 11 的受限 sample2 实验未形成有效研究稿。Change 12 的固定材料评价在 4 次真实请求内提交六项评分和摘要，但仍有无依据的安全与领先水平判断；计分前整体结论与计算结果冲突的问题已通过调整生成顺序处理，仅完成离线复验。入口切换及确定性装配不能代替科学质量证据。后续完整运行的质量结论应由实际结果判断。
