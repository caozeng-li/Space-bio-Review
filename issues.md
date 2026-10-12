# 运行问题记录

## mytest19：结果复核（2026-10-12）

复核对象为 `runs/mytest19_complete/`。`_complete` 是用户手动添加的目录后缀，不作为成功依据。本次只读核查保存的申请原文、专家产物、综合产物、提交记录和用量，没有调用真实 API、重跑评审或修改运行产物。

### 总体情况

- 中文报告、英文报告和专家附录均已交付，整体建议为“修改后再评”。报告识别出的单一天地对照难以拆分空间因素、静态形态不能直接证明相分离、样品保存方案需要澄清等主线，有实际材料依据。不是整份报告不可用。
- 22 个专业研究任务中，20 个形成已提交产物，2 个提交失败。`review.json.research_status` 为 `partial`，六方面实际覆盖均为 `partially_assessed`。报告生成完成不等于所有专业研究完成。
- 模型请求共 269 次，输入 6,057,704、输出 313,656 token，记录耗时约 71.9 分钟。数值来自 `metrics.json` 的 model 分组，不把工具调用数算成模型调用数。
- 本次专业正式提交记录仍为 `prompt_yaml`，没有启用“正式只选择既有论述”的新默认协议。以下问题是本次实际运行路径的问题，不能据此认定未启用的新协议也经过了整流程验证。

只记录会影响事实、证据支持、决策限定、实际覆盖或显著开销的问题；不将普通措辞、排版瑕疵、合理保留未知以及目录改名造成的旧路径变化列为缺陷。

### 1. 综合阶段把正确预算读成矛盾，并把年度计划写成已完成事实

**状态：确认，优先处理。**

- 报告“项目管理”开头写“总计100万元 vs. 表格列示40+30+30+20=120万元”，称预算存在额度与年限冲突。申请原表实际是年度安排 **40+30+30=100**；科目安排为 **0+30+30+20+20=100**，两者均自洽。额外的 20 不属于年度计划。
- 该错误已经存在于管理方面综合的原始模型响应，不是中文渲染或英文翻译产生；该综合产物保留的 `evidence-ab13f415db763f6f` / p38 也包含正确完整表格。英文报告继续继承了这一错误。
- 严谨性章节另将“2023年度目标”写成“地面方法学基础已建立——2023年完成……”。所引证据是年度目标和考核指标表，并非完成验收记录。年份已过去也不能证明目标已经达成。

**影响：** 同一综合环节既制造不存在的预算缺陷，也制造未经证实的项目优势，会直接扭曲对管理自洽性和准备程度的评价。这不是挑数字格式，而是改变事实。

**依据：** [报告预算断言](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:163)、[已完成断言](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:117)、[申请预算原表](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/sources/src-c63c4edaaaa8/original.md:120)、[年度目标表](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/sources/src-c63c4edaaaa8/original.md:108)、[管理综合原始响应](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/submissions/e0675b880d864051a69e0130cc2c9c7d/attempt-001.json)。

### 2. 返回后单次 mtDNA 检测被并入在轨时序观测，夸大了设计可支持的关联

**状态：确认，优先处理。**

- 申请方案是第 3、6、9 天成像，第 10 天后去掉培养基，返回地面后进行 PCR 和测序。没有设计第 3、6、9 天的 mtDNA 突变率时间序列。
- `research/q7/expert-2/r0` 的 b3 却构造“第3天 ROS 升高、第6天类核增大、第9天返回样品检出突变率上升”的候选级联。该例子被科学价值与严谨性综合继续采用。
- 科学价值章节进一步明确写成“在3/6/9天同步采集形态、亚细胞器形态、功能和基因组改变四类数据”。这已经不只是条件式设想，而是将申请没有的采样设计作为现有优势。

**影响：** 可以讨论早期成像与返回终点的关联，但不能据单次返回终点确定 mtDNA 损伤何时开始、是否发生在类核变化之后。把它写成完整时序数据集，会高估当前设计对“氧化应激→类核重塑→损伤累积”顺序的支持。不能仅把“第9天”改成“第10天”就算解决。

**依据：** [申请实际流程](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/sources/src-c63c4edaaaa8/original.md:63)、[专家原文](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/appendix.md:497)、[综合中的候选级联](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:69)、[同步四类数据断言](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:71)。

### 3. 专家附录给出了缺少依据或推导的定量检出阈值

**状态：确认存在证据越界；不是认定所有成像风险分析都错误。**

- `research/q13/expert-2/r0` 的 b6 从 FlightScope 相邻图像相关系数 0.5–0.8，推到测量 CV 为 15–30%、三重复需要 40–60% 效应才能以 α=0.05、power=0.8 检出；b7 又断言效应超过 2 倍且相关系数至少 0.5 时仍可检出。
- 所引 `evidence-dab3db346b3d8ee7` / p21 只报告 FlightScope 在稳态、非抛物线飞行中的相邻图像相关系数。没有上述 ROS 测量 CV、三重复功效分析或检出阈值。
- 产物没有给出从图像相关系数到本项目生物学重复方差的模型、参数来源或计算过程，申请也未给出能完成这项计算的仪器及效应量信息。

**影响：** 这些数字看起来像经过定量验证的可接受标准，实际上目前材料不足以支持，可能误导后续设备验收或实验设计。主报告没有照抄全部阈值，影响主要在专家附录和可复用的研究产物，不应夸大为所有最终结论都依赖这些数字。

**依据：** [附录阈值](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/appendix.md:708)、[完整专家产物及引用快照](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/v4/research-909ee20ca3ee610916db05f37239cf206bda8ee5361ff98d49dfb859781a36fa.json)。

### 4. 部分决定性批评把“未说明”推进成了“已经存在的硬件或安全缺陷”

**状态：确认，影响决策限定。**

- 设备费为 0，加上方案使用“自带显微镜”，可以要求澄清资源来源，却不能单独证明预算与设备需求“存在事实矛盾”。报告一方面承认可能使用已有平台或其他渠道支持，另一方面仍在摘要、整体判断和管理章节将其写成已成立的矛盾。
- 安全章节由“添加染料”和“去掉培养基”直接推断“意味开放性或半开放性液体转移”。原文没有说明流路开放程度；封闭流路同样可以完成这两项操作。要求提供密封、废液管理方案合理，认定实际操作形式则缺乏依据。

**影响：** “无法核实风险是否受控”和“已经确认不安全/自相矛盾”是不同判断。当前限定在段落间流转时被加强，使报告显得比证据更确定，也容易对申请形成不公平的负面评价。这里不否定要求申请补充资源与安全说明的必要性。

**依据：** [摘要支持条件](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:13)、[开放操作推断](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:153)、[预算事实矛盾与替代解释并存](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:167)、[申请实际操作表述](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/sources/src-c63c4edaaaa8/original.md:63)。

### 5. 一个论断的引用被展开成整块论述的全部来源，削弱了证据对应关系

**状态：确认，程序路径可定位。**

- 整体判断中“多组学研究已确认线粒体应激是……中心生物学枢纽”一句后出现 18 个来源编号。实际直接支持它的是 [23] 的多组学研究；同串里的 [51] 是申请年度考核表，[70] 是申请自己的线粒体信号研究方案，不能作为这一外部研究结论的直接证据。
- 上游整体综合引用了直接 E 和科学价值综合的整个 b1。交付层 `source_keys()` 遇到 R 后，递归展开该块所有 `evidence_refs`、`result_refs` 及产物共同 `scope_links`，把整条依赖关系都显示成当前位置的来源编号。它没有区分“该句直接依据”和“被引用论述的其他依据/背景”。
- 科学价值 b1 自身对该多组学事实只引用对应 E；问题在继续引用整个混合论述块并展开时明显扩大。因此不能简单归结为模型在每个事实后主动选择了 18 个来源。

**影响：** 不只是引用串太长、阅读体验差，还会让申请自述和邻接论点的文献看起来支持当前事实。引用身份存在、编号正确，不等于局部证据支持关系正确。修复方向应保留论断级依据与论述沿袭关系的区别，不是机械限制引用数量。

**依据：** [最终引用串](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:53)、[科学价值原始块](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/v4/aspect-final-65dc5a22b897ea0ad3ef209ef4ea8b8552b921ad1b5cfbef5b5a2f1eae4aacb3.json)、[递归展开实现](D:/master/SpaceBioReview/demo_11/src/spacebio_review/v4/delivery.py:83)。

### 6. 两份专业意见仍因正式提交协议失败，最终摘要没有如实说明这类覆盖缺口

**状态：确认。格式失败与内容质量问题分开看。**

- 22 个专业任务中有 6 个首轮提交失败并进入一次修复；其中 4 个修复成功，`research/q8/expert-1/r0` 和 `research/q10/expert-1/r0` 最终失败。
- q8 首次把项目路径描述写成 `[@path-3观察或产物]`，触发未绑定引用；修复响应又带 YAML 围栏，补丁被拒绝。q10 两次正式响应均带 YAML 围栏，未被接受。相关响应均为 `finish_reason=stop`，不是达到输出上限。
- 对应内容是成像设备条件和天地培养体系等效性。其他专家仍有同题产物，所以不是两类问题完全无人分析；但确实缺失这位专家获分配的意见。
- `review.json.technical_limits` 保留了两次失败，六方面覆盖也均为部分评估。最终摘要却称“上述方面均已完成专业评审并形成明确判断”；“实际评审范围”又同时写“仅部分问题形成专业评审”和“有明确问题，无缺失”。报告没有将两次技术失败转成读者能理解的具体未完成范围。

**影响：** 首先损失已投入研究后的正式意见；其次，读者难以区分系统未完成分析与申请本身没有说明。不能仅因报告、翻译已生成就认为完整评审已成功，也不宜把已有部分评估说成完全失败。

**依据：** [q8 首次提交](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/submissions/d822f6a1248c414992f2d2c2361f3fa6/attempt-001.json)、[q8 修复](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/submissions/d822f6a1248c414992f2d2c2361f3fa6/attempt-002.json)、[q10 再次提交](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/submissions/6689a65f723a4babb85c6910934a97b6/attempt-002.json)、[摘要覆盖宣称](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:18)、[范围显示](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:40)、[运行状态](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/review.json)。

本次全流程共有 10 次正式修复响应，额外输入 264,913、输出 28,615 token。这些是应用提交修复，不是网络重试；`metrics.json` 中网络失败/重试为 0 不与上述情况矛盾。

### 7. 补研路由仍一次携带大上下文，花费较高却漏掉两项请求

**状态：确认本次开销与漏项；不把全部开销都判为浪费。**

- `prepare/route/7e2abae1a3ae60c6` 单次请求输入 **220,437**、输出 **14,792** token，其中思考 11,617 已计入输出。最终 85 项后续请求归并为 74 个 alias、8 个 partial、1 个 external_dependency，另 2 项因 `routing_omitted_request` 被挂起。
- 被遗漏的是辐射剂量监测安排和样品返回至测序的保存链管理方案。这些可以有依据地归为申请人补充或复用既有分析，不一定需要继续研究；问题是本次没有形成任何明确处置，而是模型漏列后由程序统一挂起。报告原样显示内部错误码。
- 当前路由入口同时传入全部待处理请求、现有问题、工作状态、项目与专家，以及全部当前结果块；只有超出模型容量时才拆分。一次没有超容量，不意味着为这项归并任务携带的上下文已经合理。

**影响：** 前面的研究材料在协调阶段再次集中发送，开销没有随任务职责收窄；付出大输入后仍有未处置请求。优先改进路由实际需要的工作视图和请求关联，而不是额外追加校验调用或整段重试。

**依据：** [实际用量](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/metrics.json)、[遗漏项交付](D:/master/SpaceBioReview/demo_11/runs/mytest19_complete/report.md:259)、[路由输入构造](D:/master/SpaceBioReview/demo_11/src/spacebio_review/v4/coordinator.py:389)、[路由容量策略](D:/master/SpaceBioReview/demo_11/src/spacebio_review/v4/preparation.py:318)。

开销背景：mytest18 为 209 次模型请求、输入 6,194,876、输出 243,822 token，记录耗时 43.9 分钟；mytest19 的输入仅略低，但请求数、输出和耗时更高。两次任务范围、检索及实际分支不同，不能据此计算同条件性能退化或宣称本轮优化无效。可以确定的是，本次整轮仍处于约 600 万输入 token 规模，正式修复与大上下文路由仍有明确的局部成本。
