# Change 35: 删除旧起题与再规划流程，接入单一派工路径

状态：旧执行流程已删除，默认装配已替换；不代表出题质量已验收。前置：[Change 32](change-32-single-model-question-generation.md)、[33](change-33-ranked-question-dispatch.md)、[34](change-34-question-generation-lifecycle.md)。

## 目标

新方案必须替换旧流程，而不是在 `allocate/merge/route_requests` 前再加一个出题层。本步完成默认入口装配和旧代码删除，不保留可自动回退的双规划引擎。真实默认可用性结论由 Change 36 给出，不能因离线装配完成就宣称已验收。

## 明确的后续评审边界

本轮采用固定题单的完整评审：问题生成者一次生成全部候选，代码选定本轮范围，专家仅评审入选题目。专家和综合阶段可以指出事实缺口、分析局限、未来实验与条件，但这些内容不自动成为新增题目或下一轮派工输入。

这是为贯彻“专家不参与问题生成”和全局前 N 截取所作的明确行为调整，不是仅把初始 `orient()` 改名。旧流程的自动补充研究轮次在新流程退役；不保留一条通过 followups 间接恢复多方起题的通道。

仍保留：

- 专家在当前题目内回读原文、检索、调用资料研究工具，以及现有工具/轮次/预算限制。
- 对题目前提的科学纠正、相反结论和未决条件；不得为维护题单而强迫专家接受错误预设。
- 现有专业论述、证据引用、综合、报告和翻译能力。
- 未决事项的来源及具体科学内容，供综合和交付如实表达。

本轮不支持运行中自动增加评审题、替换被截取题或由综合意见触发全局再规划。将来如确有需求，须另行定义如何由唯一出题者处理新材料及总上限，不能沿用旧合并图。本轮不预造接口。

## followups 的最小调整

沿用现有科学产物中的 `followups` 容器以避免重写研究/报告契约，但明确其含义是当前分析未解决的信息及影响，不是待执行的评审题提案。

修改 `research.py`、`synthesis.py` 中相关提示：能在当前任务内完成的资料分析就在当前任务内做；真正未解决的事项如实保留，不承诺下游 Agent 自动接单。不得通过提出 followup 逃避当前问题。

协调器不再把每条 followup 包装成 `pending` 调度请求。改为直接从实际专业/综合产物投影未决科学内容及来源，交给既有公开视图。无需 `canonical_id`、alias 合并、派工目标、处置动作或请求状态机。

仅保留同一产物内已经使用的精确身份复用，避免重复复制同一记录；不增加跨来源语义去重。如果不同论述中出现相近局限，交由综合的正常论证组织处理，不再引入一次请求归并调用。

## 默认路径

`runner.py` 使用同一个 `Preparation` 和 `Coordinator` 的新路径：

1. 读取与项目结构产物。
2. 组建现有专业能力专家并冻结配置。
3. 单一 `question_generation` 强模型生成有序题单。
4. 代码选择前 N 题，一次生效并派工。
5. 已选问题研究完成或预算停止后，综合现有成果一次，不因缺口再转回出题。
6. 冻结实际覆盖与未决事项，沿用报告交付。

这里的“一次综合”是一次业务阶段，仍可使用现有按维度、容量分批和归约调用；不是要求所有综合只能发一个请求。专家内部研究也不变成单请求。

默认装配必须按实际模型协议创建 Adapter。当前 `runner.py` 使用 `DashScopeModel` 的地方需要接通已有 Messages Adapter 的选择，不能只增加 yaml 但实际仍使用旧客户端。非问题生成路由的模型、参数与协议逐项保持不变。

## 删除与保留清单

| 范围 | 处置 |
|---|---|
| `preparation.py` 的 map.questions、orient、proposals 汇集 | 删除；保留读取、项目理解、目录组队 |
| `allocate()`、`route_requests()` 的 LLM 合并与分配契约 | 删除，不保留别名入口 |
| coordinator 的 apply_plan、root_question、merge graph、planning_gaps/gap_plan | 删除或用直接单题访问替代，不保留空图绕行 |
| 请求的 pending/alias/canonical 路由、补充波次与自动 revise/new_work | 删除；未决科学内容直接进入综合视图 |
| `planning.py` 的工作组编译、`planning_generation.py` 的 intent/joint_work | 删除已被取代的业务实现 |
| `planning_access.py`、`planning_batches.py` 的增量路由工作集 | 无剩余调用后删除，不改造成问题生成的读取规划器 |
| `planning_preparation.py`、`planning_coordinator.py`、`planning_legacy.py` | 新默认路径和测试迁移完成后删除，不保留候选双栈或旧图导入器 |
| `planning_submission.py` | 仅迁移 Change 34 需要的提交能力，其余删除 |
| `models/messages.py` 及已证实通用协议能力 | 保留，供新的生成路由使用 |
| `config/planning.yaml` / `planning_runtime_config()` | 迁移实际需要的模型配置到明确问题生成路由，删除候选规划 overlay 与 planning_update 路由 |
| `supplemental_rounds` 及旧专用配置 | 默认配置删除，配置加载中明确说明退役，不静默忽略用户原设置 |
| 研究、资料检索、论述继承、公开引用渲染 | 保留，只有输入/未决视图必需的局部适配 |

删除前用实际引用搜索确定调用方；保留历史产物不等于保留线上旧执行引擎。不要为删除旧图顺带重构无关的证据或报告模块。

## 测试与历史记录迁移

旧 `test_planning*` 中纯合并图、required 槽、read_context 与路由动作测试不再作为新业务验收标准。能证明原生结构化输出、来源保存、确定性状态恢复的测试迁移到新 Interface；退休行为测试删除，不把它们全改成 skip。

`scripts/probe_planning.py` 不保留为新路线的替代入口。复用它真正通用的用量记录思路，实现窄的 `scripts/probe_question_generation.py`；旧脚本可在无调用方后移除，历史 `runs/` 记录保持只读。

新流程使用明确的新 checkpoint 格式/流程标识。旧 mytest22 以及旧 candidate 的 checkpoint 不自动继续到新流程，不自动迁移旧题目身份或已评结论；要求新输出目录，给出清楚提示。历史 review/report 和原始 JSON 继续可读；本轮不建设旧流程执行兼容层。

不修改 `runs/mytest22`、手动 complete 标记、`issues.md` 或历史费用记录。后续实施结果另行追加，不能把旧失败改写为新成功。

## 离线验证

- [x] 默认入口使用新的生成、选择、派工路径，不调用 orient/allocate/gap/routing。
- [x] 使用真实 Runner 装配和记录型模型供应商，验证只在生成阶段选择 Messages Adapter，其他路由原样。
- [x] 使用局部准备到派工测试，不执行真实 Research、Synthesis、Delivery；断言旧规划函数不存在或无入口。
- [x] 专家产物包含大量 followups 时不触发生成、路由、补充研究调用；内容和依据仍进入未决视图。
- [x] 专家发现题目前提错误时可给出反证和限制，不要求改题、合并或重新派工才允许完成。
- [x] 真实公开视图函数读取单题产物和未决信息，现有引用身份保持，内部批次/派工元数据不进入正文输入。
- [x] 旧 checkpoint 恢复在任何付费调用前明确停止，源文件哈希不变。
- [x] 删除后的 import、相关测试及配置加载正常，没有仅靠未使用代码保住的双路径。

若最小公开视图适配发现必须大范围改变报告科学结构，先保留阻塞点并汇报，不在此步扩写另一套请求兼容框架。
