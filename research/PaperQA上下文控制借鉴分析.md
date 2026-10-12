# PaperQA 上下文控制借鉴分析

调研日期：2026-10-11。

研究对象为官方 [Future-House/paper-qa](https://github.com/Future-House/paper-qa)，固定到本次查询到的 main 提交 [`57e89f7223b0960d5ee5ea048c69e3c47e088572`](https://github.com/Future-House/paper-qa/commit/57e89f7223b0960d5ee5ea048c69e3c47e088572)。以下源码链接均固定该提交，行号按原始文件计算，不采用网页转换后的行号。

范围：针对本项目 mytest18 输入膨胀，核查证据存储、工具回复、最终生成、去重和预算控制。只读取上游源码并进行无模型 API 的局部验证，没有安装 PaperQA、调用真实模型或修改生产实现。本文明确区分上游事实、本项目设计判断和未验证效果。

## 一、结论

**有可借鉴之处，但不需要引入 PaperQA 依赖或替换现有评审流程。** 最有价值的是把内部证据档案、研究 agent 看到的工具结果、最终回答使用的证据视图分开，而不是每次请求都发送完整档案。

这个结论支持此前方案，并补充一项具体实现原则：正式生成应从任务和所需论证材料重新组装消息，不自动继承研究期所有工具原文。PaperQA 的默认条数限制和摘要机制不适合直接照搬，也不能代替本项目的完整请求 token 预算。

## 二、上游实际做了什么

### 2.1 内部状态与工具可见结果分离

`EnvironmentState` 持有 `Docs` 和 `PQASession`。工具执行时更新这些内部对象，返回给 agent 的是另行组织的字符串，并不自动序列化整份状态。[状态定义](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L47-L53)

- `PaperSearch` 把检索到的文档和文本加入 `Docs`，只返回状态，或论文标题、年份及状态；默认不返回论文元数据，更不返回所有论文原文。[入库与返回](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L188-L210)、[默认设置](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/settings.py#L648-L654)
- `GatherEvidence` 临时使用具体子问题收集证据，完成后恢复主问题；内部 session 保留证据集合。返回时先按本次问题过滤，再按相关性得分排序，只展示前 `agent_evidence_n` 条 `c.context`，外加新增条数和状态，默认展示 1 条。[收集过程](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L253-L275)、[工具输出](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L279-L311)、[默认条数](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/settings.py#L656-L661)
- `GenerateAnswer` 把内部 session 交给 `Docs.aquery`，返回回答和状态，不把 session 全部证据作为工具结果返回。[回答工具](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L346-L372)

**本项目判断：** 这直接对应 `TaskContext` 全量附加证据、文献委派返回原文后又导入证据视图的问题。应保留完整档案供引用解析和回读，但不要求每轮模型都重新阅读。这里借鉴的是状态与呈现分离，而不是把科研证据一律压成一句话。

### 2.2 正式回答使用单独的证据视图和全新消息

默认 `context_serializer` 对证据按得分排序，截取 `answer_max_sources`，再过滤低相关性条目。默认最大条数为 5；渲染的是 `c.context`、引用标识和文献引用，不是 `c.text.text` 中的全部原文。按问题分组也发生在上述筛选之后，不保证每个子问题都有保留项。[默认条数](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/settings.py#L138-L139)、[选择与渲染](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/settings.py#L1202-L1246)

`Docs.aquery` 调用这个 serializer 后，新建 system/user 消息进行回答，不回放 agent 的整个检索对话历史。配置允许时可另带上一版回答。[视图构建](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/docs.py#L643-L647)、[正式消息](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/docs.py#L656-L679)

**本项目判断：** 正式研究产物应使用当前任务、已有研究判断、所引用的完整论证单元及必要原文，避免再把所有历史工具观察与全量证据目录一起发送。不能仅保留 top 5：项目评审要求覆盖多个评价范围、关键反证和条件约束，单一相关性排序可能挤掉这些内容。选择时应保留待回答范围和已经使用的引用，并允许按稳定标识回读原文。

### 2.3 摘要、去重与缓存不能混为一谈

`aget_evidence` 默认检索 `evidence_k=10` 个候选，并对候选逐块执行面向问题的证据生成；默认摘要长度提示约 100 个词，默认不跳过摘要。这把较长原文转换为较短证据，但会新增模型调用。[默认配置](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/settings.py#L109-L129)、[逐块生成](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/docs.py#L521-L570)

session 中的上下文去重发生在上述生成完成之后。它避免重复 Context 入库，但仅凭这段代码不能认定同样检索在下一轮不会再付摘要费用。`PaperSearch.previous_searches` 记录的是同一查询的分页 offset，重复查询会继续取后面的结果，也不是回答缓存。[上下文去重](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/docs.py#L573-L585)、[搜索分页](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L167-L180)、[offset 更新](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L203-L204)

**本项目判断：** 不默认引入逐块模型摘要。mytest18 已经存在多个表示重复携带同一原文和元数据的浪费，先消除这种确定性冗余，不必先增加另一层生成成本、摘要失真风险和调用失败面。上游这些代码也不足以成为增加新缓存层的依据；现有 embedding 缓存与本次模型消息膨胀是不同问题。

### 2.4 PaperQA 没有替我们解决完整请求预算

默认 ToolSelector 循环持续追加 observation 和 action，再将整个消息列表交给 agent。这里未见按 token 预算裁剪历史的逻辑。存在可选步数上限，但默认 `max_timesteps=None`；默认超时 500 秒。这些限制约束执行过程，不等于限制每次请求大小。[消息生命周期](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/main.py#L295-L320)、[超时与步数配置](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/settings.py#L663-L684)

`Reset` 工具清空 session 的证据 contexts/context，不清空 Docs；它是主动清除当前证据的工具，不是自动 token 管理器，也不能据此推断旧对话历史已被清除。[Reset 实现](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py#L389-L402)

**本项目判断：** 不能因为采用“小工具回复”就认为多轮累计问题已解决。仍需在来源、论证材料、工具定义和输出要求组装完成后计算实际请求大小，并在调用前构造预算内工作集；不能继续让 V4 上下文直接绕过已有软预算。条数限制不等于 token 限制，论文检索多样性控制也不能解决同一证据被不同包装重复发送。

## 三、无 API 局部验证

为核实“档案与呈现分离”和“条数并非 token 上限”，从固定提交 `settings.py` 的 AST 中仅提取真实 `Settings.context_serializer` 方法，用合成对象执行其默认分支，没有执行 PaperQA 其他模块或模型调用。

| 输入 | 观察结果 | 能说明什么 |
|---|---|---|
| 40 条内部 Context；每条原文约 120,000 字符、`context` 为短证据 | 内部仍有 40 条，输出只包含 5 条短证据，共 254 字符，没有原文标记 `RAW-ARCHIVE` | 默认 serializer 不把档案原文直接灌入回答上下文，且不会因视图筛选删除内部档案 |
| 仍为 40 条，每条 `context` 改成 50,000 字符 | 输出成功生成，共 250,174 字符 | top 5 本身不保证请求长度；简短摘要只是默认生成提示，并非严格预算 |

验证边界：这是上游真实 serializer 函数与合成对象的局部实验，不是 PaperQA 端到端测试；没有测量模型计费 token、运行质量、科学证据完整性或本项目修复后的节省比例。

## 四、对现有方案的四项决策

| 决策 | 处理方式 | 与 PaperQA 的关系 |
|---|---|---|
| 证据档案与工作视图分离 | 采用。完整存储、稳定引用和可回读能力保留；模型仅接收当前任务所需证据。文献委派结果与来源目录不重复携带同一原文。 | 直接借鉴内部状态与工具输出分离，不照搬默认展示 1 条。 |
| 检索包装按实际载荷组织 | 维持既有方案。来源公共元数据集中表达，避免每个小段落重复；限制应覆盖包装后的模型载荷，而非只统计正文字符。 | PaperQA 小工具结果支持此方向，但没有提供可直接替换本项目段落/引用契约的实现。 |
| 正式生成重新组装消息 | 纳入方案。研究过程记录仍存档，正式生成只带所需材料和论证，不回放所有历史工具原文。 | 直接借鉴 `aquery` 的新消息构建；不丢弃本项目独有的范围覆盖、反证和论证引用。 |
| 完整请求的软预算生效 | 维持并落实。完整组装后测量，调用前选择/重排工作集，保留必要论证和回读入口。 | 必须由本项目完成；不能把 top-k、timeout 或上下文去重当成 token 预算。 |

不引入 PaperQA 运行时依赖，不改专家分工和报告结构，不默认增加逐块 LLM 摘要，不照搬 top 5 或通过“少读材料”换取表面节省。后续实现应优先做确定性的去重复与消息组装，再以保存的 mytest18 材料验证实际载荷下降、引用解析不变及范围覆盖不缺失。

尚未验证：这四项改动的最终 token 节省比例、长研究的质量影响、是否需要额外的工作集选择策略。本笔记提供实现依据，不宣称上游机制已证明能够解决本项目全部成本问题。
