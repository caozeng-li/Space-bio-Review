# 面向项目理解的科学项目评审 MAS 重构调研笔记

调研日期：2026-10-05。

本文重新寻找科学项目评审 MAS 的理论与实现依据。研究对象是项目计划书及其科学语境：系统需要解释项目、补足理解所需的知识、建立跨专业联系，并据此作出全面评估。专家意见、讨论记录和最终建议是工作产物，不应成为一轮又一轮再评审的主要对象。

本文区分三类陈述：**已有证据**指论文实际研究的任务与结果；**可迁移设计**指值得借鉴的机制；**本项目推论**指尚未在科学项目评审中得到验证的设计判断。正式发表状态以会议论文集、期刊原文为准；预印本和机构工程经验另行标注。技术栈沿用现有后端，本笔记不重新选择模型或框架。

调研范围包括 2024—2026 年正式发表的相关 MAS 与科学知识系统、其原始开源实现、当前科研资助机构指南，以及直接相关的评审实证研究；少数经典理论用于说明认知分工与共享表示的基础。检索围绕 complex information seeking、multi-agent knowledge integration、scientific grant evaluation、science traceability 等主题交叉展开。这是面向重构的目的性调研，不是穷尽性系统综述。源码核查为静态阅读，未复现实验或运行第三方系统。

## 一、先选择合适的问题范式

科学项目评审同时包含理解、调查、整合和评价。它与知识密集型复杂信息寻求（complex information seeking）、意义建构（sensemaking）及跨专业知识整合更接近，不能直接等同于有唯一标准答案的多轮辩论题。

这里的“全面”也不意味着无限扩展文献。它要求把项目的主要组成部分及其关系解释清楚：研究问题从何而来；申请人依赖哪些知识、假设和资源；拟议工作能够得到什么观测；这些观测能带来什么认识；科学价值与实施条件如何共同影响项目评价。

### 1.1 经典理论仍有用，但只是设计依据

| 理论或研究 | 已有证据 | 对本项目的启示 |
|---|---|---|
| Pirolli 与 Card：sensemaking，2005 | 对分析工作的认知研究区分信息获取、加工与组织解释；最终知识产品依赖材料与解释框架的相互作用。[原文](https://www.researchgate.net/profile/Peter-Pirolli/publication/215439203_The_sensemaking_process_and_leverage_points_for_analyst_technology_as_identified_through_cognitive_task_analysis/links/02bfe50f09ca94efc0000000/The-sensemaking-process-and-leverage-points-for-analyst-technology-as-identified-through-cognitive-task-analysis.pdf) | 专家应共同完善“项目如何产生知识”的解释，而不只是交换赞成或反对意见。 |
| 共享团队心智模型，2010 | 23 项研究的元分析指出，共享知识的结构组织与团队过程相关，内容和结构均与绩效相关。[作者机构原文](https://atlas.northwestern.edu/papers/sharedTeam.pdf) | 共享项目对象、术语和研究关系，比共享全部聊天历史更值得优先设计。人类团队结果不能直接当作 LLM 团队有效性的证明。 |
| 团队信息共享，2009 | 72 项研究的元分析支持信息共享与团队绩效、知识整合的关联，也表明效果受任务、信息分布和讨论结构影响。[原始研究记录](https://pubmed.ncbi.nlm.nih.gov/19271807/) | 把各专家独有的知识贡献变成可复用材料；多样角色本身不足以保证整合。 |
| Hearsay-II 黑板架构，1980 | 通过共享的分层问题表征协调不同知识源，完成合作解释。[原文](https://mas.cs.umass.edu/Documents/Erman_Hearsay80.pdf) | 可采用轻量共享工作区；不需要因此引入图数据库、事务系统或形式化论证裁判。 |

上述理论支持“围绕共同对象协作”的研究方向，但不要求照搬传统黑板控制器，也不证明采用某种数据结构就能获得可靠评审。

## 二、近年 MAS 与知识研究系统：哪些机制真正相关

### 2.1 STORM：先研究，再组织知识产品

**发表：NAACL 2024 主会。** STORM 通过不同视角提出问题、让有检索能力的专家回答，再形成提纲与带引用的长文。在其百科式写作任务中，相比所用基线，组织性与覆盖面获得改善。它同时报告来源偏差和不恰当关联等局限。[正式论文](https://aclanthology.org/2024.naacl-long.347/)

**可迁移设计：** 在写评审意见前，先完成有目的的项目研究；让角色差异体现在问题与证据需求上。**本项目推论：** 把通用主题替换成明确的项目对象与评价语境，避免把评审报告写成领域综述。论文没有证明 STORM 能判断资助价值，也没有证明多视角数量越多越好。

### 2.2 Co-STORM：围绕知识组织协作，而非围绕答案胜负协作

**发表：EMNLP 2024 主会。** Co-STORM 用不同视角的专家与主持者探索复杂主题，将发现组织为动态知识结构，再生成有来源的报告。主持者利用已检索但尚未使用的信息提出新方向，缓解重复讨论。论文研究的是信息寻求与学习，不是资助评审。[正式论文](https://aclanthology.org/2024.emnlp-main.554/)

**可迁移设计：** 让发现与它回答的问题关联；综合者不仅汇总，还连接不同领域的认识。**本项目推论：** 采用项目中心的共享认知结构；去掉人机交互需求，把新方向限定在理解项目所需的范围。无需模拟每位专家依次发言，也不应把动态拓展变成无限探索。

### 2.3 ExtAgents：分开持有材料，逐步整合相关知识

**发表：ACL 2026 主会，2025 年先发布预印本。** ExtAgents 针对超出上下文窗口的外部知识，以分布式材料处理、全局知识同步和渐进整合进行推理；在多跳问答及长综述任务中获得所报告的改善。[正式论文](https://aclanthology.org/2026.acl-long.468/)

**可迁移设计：** 专家保存本地深入材料，向共享层提交与当前任务相关的知识，而非反复转交全部原始材料。**本项目推论：** 建立“简洁共享项目表征＋专题研究材料＋按需原文访问”。其问答结果不等于项目评价能力；本项目也不需要复刻论文全部迭代排序环节。

### 2.4 Virtual Lab：角色应当拥有专业任务，而非只有角色名称

**发表：Nature，2025。** Virtual Lab 通过 PI 与不同学科代理的研究会议，完成纳米抗体设计工作，并有实验验证。该系统包含科学批评角色与人类高层反馈。[期刊原文](https://www.nature.com/articles/s41586-025-09442-9)

**可迁移设计：** 根据科学对象配置专业能力，让不同代理负责具体研究贡献。**本项目推论：** 生物学、测量方法与空间实施专家应处理不同知识问题，并在共同研究关系上相接。该论文支持跨学科协作的可行性，不证明自动评审质量；其批评者与长会议结构没有必要整体继承。

### 2.5 Co-Scientist：任务对象决定哪些协作机制值得保留

**发表：Nature，2026-05-19；早期版本为 2025 年预印本。** Co-Scientist 以专业化代理、异步任务与文献工具生成、比较和演化科研假设，并报告生物医学应用验证。[期刊原文](https://www.nature.com/articles/s41586-026-10644-y)

**可迁移设计：** 面向科学目标进行专业分工，连接已有知识与跨领域解释。**本项目推论：** 可借鉴专业角色与任务调度；不采用假设淘汰赛和持续演化作为评审主线。生成新研究方案与评价一个既定申请是不同任务，该成果不能用于论证“评审必须多轮自我批评”。

### 2.6 PaperQA2：文献调查是认知基础，不应只是事后补证

**状态：2024 年预印本及持续维护的开源项目；本次未核实该标题已有正式期刊版本。** PaperQA2 将论文发现、相关片段提取、问题相关摘要和有引用的回答结合起来，论文报告了文献问答、总结及矛盾发现任务的表现。[论文](https://arxiv.org/abs/2409.13740) · [官方代码](https://github.com/Future-House/paper-qa)

**可迁移设计：** 专家研究问题驱动检索，证据加工返回适用语境与来源。**本项目推论：** 文献代理参与正常研究过程；调查包括背景、先例、正面依据、替代解释和方法限制，不能只接受“请证明这里有问题”的检索请求。文献回答优于某些基线不等于科研资助判断优于人类。

### 2.7 OpenScholar：高质量综合需要匹配任务的证据供应

**发表：Nature，2026-02-04；DOI 中的 2025 不代表发表年份。** OpenScholar 结合科学文献检索、重排及有引用的综合生成，在多领域文献综合任务上有正式验证。[期刊原文](https://www.nature.com/articles/s41586-025-10072-4)

**可迁移设计：** 将文献发现、段落选择和综合表达分开，优先保证证据与科学问题匹配。**本项目推论：** 它适合作为文献服务设计依据，无需更换现有模型或建立大规模本地文献库。原系统包含反馈修订；不必把这一机制上升为整个 MAS 的组织原则。

### 2.8 Anthropic Research：并行研究适合可分解的信息任务

**证据类型：机构工程经验，2025；非同行评审论文。** 其主代理分配相对独立的研究任务，子代理并行调查并返回压缩结果；文章强调明确委派范围、工具设计和协调成本。[官方技术说明](https://www.anthropic.com/engineering/multi-agent-research-system)

**可迁移设计：** 研究任务应说明目标、必要语境、范围和交付内容；不要让所有代理搜同一组关键词。**本项目推论：** 专题分工与最终整合可沿用现有异步后端实现，公共证据缓存与私有研究上下文分离即可。其内部效果数据不是本项目有效性的外部保证。

### 2.9 Scaling Agent Systems：MAS 优势取决于任务结构

**状态：预印本；本次采用 2026-04-08 的 v3。** 最新版本研究 260 种配置、6 个任务集，发现协作收益与任务可分解性、协调开销和模型能力有关，顺序性强的任务可能因多代理而变差。[原文 v3](https://arxiv.org/abs/2512.08296v3)

**可迁移设计：** 并行化真正独立的调查，将必须结合上下文的关系分析交给明确的整合环节。**本项目推论：** 以专题并行、少量跨专题联合解释替代全连接交谈。本文不复用旧版的配置数和统计数字，也不把其经验关系写成普遍定律。

### 2.10 MAST 与 MAD-M²：错误研究不等于应建立更多检查角色

**MAST：NeurIPS 2025 Datasets and Benchmarks Track。** 该工作从 1,600 多条多代理运行轨迹研究系统设计、代理间不协调和任务验证等失败类型。[正式论文](https://papers.nips.cc/paper_files/paper/2025/hash/b1041e52d3be19f0a9bc491657488e4a-Abstract-Datasets_and_Benchmarks_Track.html) 其启示是任务分工、信息传递和完成目标需要清楚，并不能推出“再增加一位审查代理”就是解决办法。

**MAD-M²：ICLR 2026。** 该工作指出辩论可能继承错误记忆，通过记忆选择改善数学与逻辑推理；会议身份由官方材料与作者论文共同核实。[论文](https://arxiv.org/abs/2603.20215) · [ICLR 官方材料](https://iclr.cc/media/iclr-2026/Slides/10010659.pdf) 可借鉴“历史发言不天然可靠”；不由此引入新的记忆裁判，亦不能据此断言所有辩论都无效。

## 三、开源实现细读：可直接转化为后端设计的做法

以下是 2026-10-05 实际读取的源文件与函数，不只是 README 描述。它们是机制参考，不构成更换现有技术栈的建议。

| 项目与源码入口 | 实际实现 | 本项目如何吸收 |
|---|---|---|
| STORM：[`knowledge_curation.py`](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/storm_wiki/modules/knowledge_curation.py)，`_run_conversation`、`research` | 不同 persona 的研究会话在线程池执行，最终得到 `StormInformationTable`。 | 用现有 asyncio 并发运行专题研究；统一汇入知识产物，而非要求模拟逐位发言。 |
| Co-STORM：[`information_insertion_module.py`](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/collaborative_storm/modules/information_insertion_module.py)，`InsertInformationModule` | 以 question、query 构造信息意图，通过候选节点或逐层导航插入知识结构。 | 保存“这条证据回答项目的哪个问题”；组织键应包含项目对象与研究关系。 |
| Co-STORM：[`article_generation.py`](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/collaborative_storm/modules/article_generation.py)，`gen_section`、`forward` | 每个章节节点只取其引用材料，按节点生成，再由代码组装。 | 写作者读取章节所需认知与证据，不必重新扮演完整评审团；跨章节判断先由整合代理给出。 |
| ExtAgents：[`src/pipeline.py`](https://github.com/THUNLP-MT/ExtAgents/blob/d94f351fb95a45eaafb8d7e0672563669a465855/src/pipeline.py)，`process_example` | 材料分块提取，已有信息进入后续提取，按选择的信息进行 reduce。当前代码在单个样例内部逐 chunk 调用；线程池主要出现在跨样例调度。 | 借鉴信息分区与渐进整合思想；不能因论文强调并行就声称这份源码已经实现逐块并行。 |
| PaperQA：[`docs.py`](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/docs.py)，`retrieve_texts`、`aget_evidence` | 使用 MMR 选择片段，受控并发生成问题相关证据摘要，保留来源与相关性信息。 | 文献服务内部批量完成选择和阅读，减少专家对每个片段反复调工具。 |
| PaperQA：[`agents/tools.py`](https://github.com/Future-House/paper-qa/blob/57e89f7223b0960d5ee5ea048c69e3c47e088572/src/paperqa/agents/tools.py)，`GatherEvidence` | 当前实现会临时替换 session.question 再恢复，明确不支持同 session 中该工具与自身并发。 | 专题使用独立研究会话；可以共享文献内容缓存，不能不加区分地共享可变会话。 |
| OpenScholar：[`src/open_scholar.py`](https://github.com/AkariAsai/OpenScholar/blob/main/src/open_scholar.py)，`run` | 检索片段重排、生成、可选反馈与追加检索、可选引文归属是分开的步骤。 | 可以单独吸收检索与综合；无需把可选自反馈扩展为所有代理的必经工作。 |
| Virtual Lab：[`run_meeting.py`](https://github.com/zou-group/virtual-lab/blob/8a3a4fd9ccc0cd297bd523751e03bc9527c91832/src/virtual_lab/run_meeting.py)，`run_meeting` | 团队会议依序发言，给不同角色加入共享 messages；个人会议包含 Scientific Critic。 | 借鉴专业任务与议题上下文；避免照搬增长的共享聊天记录和默认批评循环。 |

这些实现共同提示：**协作接口可以是带来源的知识产物，不一定是自然语言会议。** 自主性体现在专家选择理解路径、问题、检索策略和专业解释；后端负责并发、材料供给、结果保存和报告组装。无需让 LLM 自己完成全部调度与格式工作。

## 四、科学项目评审理论与实践

### 4.1 机构指南：评审需要形成什么认识

| 来源与适用语境 | 原始要求或做法 | 可吸收的设计思想 |
|---|---|---|
| **NIH 2026 评审指南**，研究项目资助 | 关注项目将获得什么知识及其重要性；允许非假设驱动研究和方法开发；实质优势、风险与潜在进展共同进入判断；评审不承担替申请人重设计项目的任务。[官方 PDF](https://grants.nih.gov/sites/default/files/Simplified-Review-Reviewer-Guide-to-Evaluating-Applications.pdf) | 从研究贡献出发理解方案；局限影响某项认识时再解释其意义，避免以“没有缺点”代替高价值。 |
| **NIH 简化框架**，2025 起适用的多数研究项目资助 | 组织为研究重要性、严谨性与可行性、团队与资源适配三项；资源与专长按对拟议工作的充分性评价。[官网](https://grants.nih.gov/policy-and-compliance/policy-topics/peer-review/simplifying-review/framework) | 科学价值、获得知识的能力和实施支撑需要联系起来；一般声誉不能替代任务相关能力。 |
| **NSF Merit Review**，当前官网 | 科学贡献覆盖本领域与跨领域认识推进；总体资助决策还可考虑研究组合与项目目标。[官网](https://www.nsf.gov/funding/merit-review) | 单项目评估先形成科学认识；支持建议需要资助语境，不冒充跨项目资源配置结果。 |
| **ERC 2026 工作计划**，2025-07-08 官方说明 | 申请 Part I 聚焦科学想法与目标，Part II 说明实施；可行性在第二步评价。[官方说明](https://erc.europa.eu/news-events/news/erc-work-programme-2026-adopted) | 理解科学想法与分析实现路径具有不同认知任务；可借鉴顺序，不照搬机构筛选轮次。 |
| **NSFC 2026 申请通告** | 按自由探索类、目标导向类研究属性组织相关项目分类评审。[官方通告](https://www.nsfc.gov.cn/p1/3381/2824/99667.html) | 评价语境应可配置；探索价值与需求牵引价值可以采用不同解释重点，不把某一家机构的维度固定为所有项目的标准。 |

这些材料是现行评审规范与组织实践，不是证明某一 MAS 更有效的实验。尤其不能将 NIH、NSF 的规则直接称作中国空间项目的适用制度。

**本项目推论：** 评价语境与项目内容共同决定评价重点。系统需要理解“做什么、为什么值得做、怎样产生有意义的结果、依靠哪些条件”，再形成科学判断。重点既包含优势也包含局限；不需要把每个维度变成独立打分代理。

### 4.2 人类评审研究对合作设计的提醒

**负面信息可能主导后续注意。** Lane 等在两项真实资助机会中开展实地实验，涉及 369 名评审与 97 个项目。看到更低的他人评分引发的降分大于看到更高评分引发的升分；理由分析显示，负面信息使评价更关注弱点。论文发表于 *Management Science* 68(6)，卷期年为 2022，在线发表于 2021。[原始研究](https://doi.org/10.1287/mnsc.2021.4107)

可吸收的是对协作任务与信息暴露方式的谨慎：先让专家研究项目，再交换知识及其解释。不能据此宣称 LLM 必然具有同样效应，也不能推导出科学讨论没有价值。

**一致性不足说明评价不能只追求统一标签。** Pier 等对 25 份 NIH R01 肿瘤学申请的模拟评审发现较低的评分及优缺点一致性。材料集中于原本获资助或修改后获资助的申请，不能把发现外推为全部项目评审都没有区分能力；研究也不是对所有会评讨论效果的检验。*PNAS*，2018。[原始全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC5866547/)

可吸收的是保留专业判断的依据与适用前提，并解释不同认识对项目的含义。相同推荐类别不表示形成了同样深入的理解。

**与内容无关的相似性可能影响评分。** Fuchs-Schündeln 与 Holub 的 *Nature Human Behaviour* 2026 研究分析 37,073 份 ERC 申请，发现同工作国家或同国籍与较高评价相关，委员会层面的结果属于相关性证据。[原始研究](https://www.nature.com/articles/s41562-026-02554-w)

本系统据此按项目科学内容组织专长、按具体研究需要解释团队能力。但没有依据认为多个 LLM 天然无偏或专业人设天然消除偏差。

### 4.3 科学评价与项目补完的区别

从上述指南和研究出发，可以区分四项工作：

| 工作 | 本系统如何处理 |
|---|---|
| 解释项目及其领域语境 | 主体任务：把内容、背景与研究意图说明白 |
| 评价拟议工作的知识贡献与实现条件 | 主体任务：形成全面的前瞻判断 |
| 指出有意义的改进方向 | 作为评价产物，但按现有方案与修改后可能性分别表达 |
| 替申请人重新设计方案、确保所有问题解决 | 不作为完成科学评审的前提 |

这是本项目的任务划分，不是逐字复述某一资助机构流程。研究计划中的未知可以是科学目标、解释前提或材料限制；其性质决定评价意义，不能一概转化为待清除的问题。

### 4.4 空间科学的先行表示：Science Traceability Matrix

NASA 的 Science Traceability Matrix 将科学目标、测量对象、观测量、仪器性能与任务条件联系起来。[官方培训入口](https://science.nasa.gov/researchers/pi-launchpad-sessions/)；[STM 要素讲义，2021](https://science.nasa.gov/wp-content/uploads/2023/04/PILaunchpad__STM__JLeisner210615.pdf)

这提供了直接的领域先例：需要解释科学目标怎样通过实际观测与能力实现。“科学问题到测量能力”的链条不是本 MAS 首创。

本项目拟在认知层吸收这种关系表达，并加入已有知识、不同结果的解释和科学贡献，用于分配专业研究任务、组织接口协作与综合评价。无需同时引入完整工程需求追溯和审计机制。

NASA 的 Life Detection Knowledge Base 也体现了以共享专业知识连接科学目标、测量和技术能力的需求。[官方介绍](https://science.nasa.gov/astrobiology/researchers/life-detection-resources/ldkb/) 它提供应用动机，不是一个可直接替换本系统的科研资助评审 MAS。

### 4.5 空间生命科学：条件如何改变认识

NASA HRP 的研究者说明列出在轨人类研究的乘员时间、运输与低温储存约束，并说明部分研究实施细节会在获选后的定义阶段继续完善。[官方说明](https://www.nasa.gov/hrp/for-prospective-researchers/)

这支持两项场景判断：任务条件要通过“能得到怎样的数据”进入科学理解；立项评审不能默认所有后续实施细节都已经定稿。该来源具体针对 NASA HRP，不能据此推断中国空间站某一实验柜的实际配额或样品容量。

对本项目的空间生命科学材料，适合集中研究以下关系：

- 特殊环境提供什么认识机会，地面模拟与真实飞行分别回答什么；
- 暴露、培养、测量和保存条件如何改变可观察的生物过程；
- 体外、细胞、动物与人体认识之间存在怎样的迁移条件；
- 一条路线获得的结果怎样解释另一条路线，以及何时只能形成互补描述；
- 数据、方法和资源建设本身可以带来哪些后续科学价值。

这些关系同时容纳科学机会与现实限制。它们应是专家协同解释的对象，而不只是风险检查项。

### 4.6 直接相关的 AI 资助评审研究

**Sandström 与 Thelwall，2026-03，预印本。** 研究开放权重 LLM 对 1994 年瑞典医学资助材料的评分；模型分数与专家均分弱正相关，作者指出样本与历史材料等限制。[原文](https://arxiv.org/abs/2603.14565)

**Thelwall 等，2026-09，预印本。** *Scoring Grant Applications with Large Language Models* 比较六个开放权重模型对 2,267 份 ESRC/EPSRC 申请的评分。最佳设置与专家平均评分的平均秩相关约为 0.26，与委员会均分约为 0.17；这些结果不足以支持模型替代最终会评，也不能外推为所有模型与方法的上限。[原文](https://arxiv.org/abs/2609.25327)

**GRAIL，IJCAI 2026 Demo Track。** 系统支持资助机会发现与申请写作。它属于相邻应用，而不是针对既定项目开展全面科学评估的直接同任务系统。[正式论文](https://www.ijcai.org/proceedings/2026/998)

这些工作意味着“用 LLM 处理或评价科研申请”已非空白。我们更合适的论文定位是：外部知识支持下，多个专业智能体如何共同理解一个拟议研究，并形成可解释的前瞻评价。检索未发现完全相同的实现，不能据此声称首个系统。

## 五、从调研收敛到本项目的设计选择

以下为本项目的综合判断，不声称已经被上述论文直接证明。

### 5.1 将共同工作对象定义为“项目知识结构”

共享材料应首先表达研究问题、知识背景、核心假设、研究活动、可观测结果和可能贡献。关系比孤立条目更重要：某个实验在回答哪个问题；某项测量能支持哪个层级的推断；某项资源条件影响哪部分科学产出。

这可以用少量结构化对象与关系实现。其用途是让不同专业贡献能够对接，不是构造支持／攻击边、判定论证胜负，也不是追求形式逻辑完备。申请人声称、文献已知和系统推断要能区分，避免把一种表述当成另一种事实。

### 5.2 按认知贡献分工，避免按“正方／反方／裁判”分工

一个适合本任务的分工方向是：科学背景与前沿定位、理论机制与跨领域联系、方案与可获得知识、领域约束与实施条件。它们是能力配置方向，应根据项目合并或细化，不能每个项目机械新增所有角色。

每个专题产物应包含：对项目的解释、相关知识、专业评价及对其他专题有用的联系。专家在初次研究时就应同时认识项目价值与限制，而非先提交结论，再等待另一代理找错。

### 5.3 协作发生在知识相接处

初次研究可以并行；需要交流的是某个联系尚不清楚，或其他专题的发现改变了该领域对项目的理解。例如：方法专家说明成像只能测尺度演化，机制专家据此解释哪些相分离机制仍然无法区分，领域专家再说明这类观察即使不能识别机制，是否仍有独特的空间科学价值。

这种交流产生新的联合认识，不要求每位专家评判其他人的整份报告，也不以形成一致意见为完成条件。最终可以保留多种合理解释，并说明它们对评价的不同含义。

### 5.4 研究充分性服务于项目解读

检索不需要证明每个细节，也不需要清除全部不确定性。主体工作应围绕几项关键认识展开：项目主张是什么、与已有知识的关系是什么、方案能产出什么、价值与风险如何理解。未找到的文献、无法判断的接口和待研究的科学问题，应作为认识状态呈现，不自动转成新的任务链。

可保留一次有限的交叉整合机会，但不能把“必须没有任何疑问”设为报告生成条件。限制探索范围与保留不确定性，是任务建模选择，不是牺牲严谨性。

### 5.5 从整合认识到前瞻贡献评价

研究计划的结果尚未发生。本系统需要进一步解释：如果得到几种合理的观测或产物，分别能够新增、限制或澄清哪些认识？这一前瞻分析由领域、方法与实施专长共同完成，不是预测实际实验结果，也不凭空给出成功概率。描述性、机制性、方法和资源贡献都可以进入同一整体评价。

这是依据科研评审任务提出的方法组织；它借鉴既有价值与可行性评价，但具体的“可能结果—可得认识—贡献”表示尚未在本系统中得到效果验证。

### 5.6 论文贡献应落在项目评估的认知结构上

仅把 STORM 换成项目评审提示词，或把四个专家输出拼成报告，方法辨识度不足。值得发展的贡献是：

1. **项目中心的任务建模：** 把申请书中的科学问题、知识前提、方法、可获得认识和贡献组织成同一评估对象。
2. **互补且关系导向的协作：** 专题独立深入，围绕跨专题关系交换知识，形成单个专题无法单独给出的项目解释。
3. **前瞻贡献综合：** 解释可能结果对应的知识贡献及条件，再据整合认识形成价值、创新性、可行性和总体建议。

这是一个有明确场景特征的系统方法主张，不是已经得到证明的性能结论，也不应把已有多视角检索、共享工作区或异步调度单独宣称为原创。本次只明确方法定位，不设计实验。

## 六、主要资料索引

### MAS、认知理论与实现

- Shao, Y., et al. (2024). *Assisting in Writing Wikipedia-like Articles From Scratch with Large Language Models*. NAACL. [来源](https://aclanthology.org/2024.naacl-long.347/)
- Jiang, Y., et al. (2024). *Into the Unknown Unknowns: Engaged Human Learning through Participation in Language Model Agent Conversations*. EMNLP. [来源](https://aclanthology.org/2024.emnlp-main.554/)
- Liu, Z., et al. (2026). *Scaling External Knowledge Input Beyond Context Windows of LLMs via Multi-Agent Collaboration*. ACL. [来源](https://aclanthology.org/2026.acl-long.468/)
- Swanson, K., et al. (2025). *The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies*. Nature. [来源](https://doi.org/10.1038/s41586-025-09442-9)
- Gottweis, J., et al. (2026). *Accelerating scientific discovery with Co-Scientist*. Nature. [来源](https://doi.org/10.1038/s41586-026-10644-y)
- Skarlinski, M. D., et al. (2024). *Language agents achieve superhuman synthesis of scientific knowledge*. 预印本. [来源](https://arxiv.org/abs/2409.13740)
- Asai, A., et al. (2026). *Synthesizing scientific literature with retrieval-augmented language models*. Nature. [来源](https://doi.org/10.1038/s41586-025-10072-4)
- Anthropic. (2025). *How we built our multi-agent research system*. 工程说明. [来源](https://www.anthropic.com/engineering/multi-agent-research-system)
- Kim, Y., et al. (2025/2026). *Towards a Science of Scaling Agent Systems*. 预印本 v3. [来源](https://arxiv.org/abs/2512.08296v3)
- Cemri, M., et al. (2025). *Why Do Multi-Agent LLM Systems Fail?* NeurIPS Datasets and Benchmarks Track. [来源](https://papers.nips.cc/paper_files/paper/2025/hash/b1041e52d3be19f0a9bc491657488e4a-Abstract-Datasets_and_Benchmarks_Track.html)
- Tian, H., et al. (2026). *Multi-Agent Debate with Memory Masking*. ICLR. [来源](https://arxiv.org/abs/2603.20215)
- Pirolli, P., & Card, S. (2005). *The sensemaking process and leverage points for analyst technology as identified through cognitive task analysis*. International Conference on Intelligence Analysis. [作者提供原文](https://www.researchgate.net/profile/Peter-Pirolli/publication/215439203_The_sensemaking_process_and_leverage_points_for_analyst_technology_as_identified_through_cognitive_task_analysis/links/02bfe50f09ca94efc0000000/The-sensemaking-process-and-leverage-points-for-analyst-technology-as-identified-through-cognitive-task-analysis.pdf)
- DeChurch, L. A., & Mesmer-Magnus, J. R. (2010). *Measuring shared team mental models: A meta-analysis*. Group Dynamics: Theory, Research, and Practice, 14(1), 1–14. [来源](https://doi.org/10.1037/a0017455)
- Mesmer-Magnus, J. R., & DeChurch, L. A. (2009). *Information sharing and team performance: A meta-analysis*. Journal of Applied Psychology, 94(2), 535–546. [来源](https://doi.org/10.1037/a0013773)
- Erman, L. D., Hayes-Roth, F., Lesser, V. R., & Reddy, D. R. (1980). *The Hearsay-II speech-understanding system: Integrating knowledge to resolve uncertainty*. ACM Computing Surveys, 12(2), 213–253. [原文](https://mas.cs.umass.edu/Documents/Erman_Hearsay80.pdf)

### 科学评审与场景

- NIH. (2026-01-07). [Evaluating Applications Under NIH’s Simplified Review Framework for Research Project Grants](https://grants.nih.gov/sites/default/files/Simplified-Review-Reviewer-Guide-to-Evaluating-Applications.pdf). 官方评审指南。
- NIH. [Simplified Peer Review Framework](https://grants.nih.gov/policy-and-compliance/policy-topics/peer-review/simplifying-review/framework).
- NSF. [How We Make Funding Decisions](https://www.nsf.gov/funding/merit-review).
- ERC. (2025-07-08). [ERC Work Programme 2026 adopted](https://erc.europa.eu/news-events/news/erc-work-programme-2026-adopted).
- 国家自然科学基金委员会. (2026). [关于2026年度国家自然科学基金项目申请与结题等有关事项的通告](https://www.nsfc.gov.cn/p1/3381/2824/99667.html).
- Lane, J. N., et al. (2022; online 2021). [Conservatism Gets Funded? A Field Experiment on the Role of Negative Information in Novel Project Evaluation](https://doi.org/10.1287/mnsc.2021.4107). *Management Science, 68*(6), 4478–4495.
- Pier, E. L., et al. (2018). [Low agreement among reviewers evaluating the same NIH grant applications](https://pmc.ncbi.nlm.nih.gov/articles/PMC5866547/). *PNAS, 115*(12), 2952–2957.
- Fuchs-Schündeln, N., & Holub, F. (2026). [Homophily in grant evaluations](https://www.nature.com/articles/s41562-026-02554-w). *Nature Human Behaviour*.
- NASA. [PI Workshop Sessions: Science Traceability Matrix](https://science.nasa.gov/researchers/pi-launchpad-sessions/).
- NASA. [For Prospective Researchers](https://www.nasa.gov/hrp/for-prospective-researchers/). HRP。
- NASA. [Life Detection Knowledge Base](https://science.nasa.gov/astrobiology/researchers/life-detection-resources/ldkb/).
- Sandström, U., & Thelwall, M. (2026). [Can Large Language Models Evaluate Grant Proposal Quality? Revisiting the Wennerås and Wold Peer Review Data](https://arxiv.org/abs/2603.14565). 预印本。
- Thelwall, M., et al. (2026). [Scoring Grant Applications with Large Language Models](https://arxiv.org/abs/2609.25327). 预印本。
- Tang, Z., & Kejriwal, M. (2026). [GRAIL: An Agentic AI Architecture for Interactive Grant Proposal Writing](https://www.ijcai.org/proceedings/2026/998). IJCAI Demo Track。
- Hollan, J., Hutchins, E., & Kirsh, D. (2000). [Distributed cognition: Toward a new foundation for human-computer interaction research](https://doi.org/10.1145/353485.353487). *ACM Transactions on Computer-Human Interaction, 7*(2), 174–196. 配套方法中的共享认知表征理论参照。

最终方法定义见[设计与建模笔记](科学项目评审MAS设计与建模笔记.md)，其场景适配解释见[需求满足说明](科学项目评审MAS需求满足说明.md)。
