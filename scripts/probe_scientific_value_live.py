"""Frozen-input 2x2 scientific-value experiment; production is never patched."""

# Keep the scientific fixtures and protocol paragraphs intact.
# ruff: noqa: E501

import argparse
import asyncio
import hashlib
import json
import random
import re
import statistics
import time
from copy import deepcopy
from pathlib import Path

import yaml
from lxml import html

from spacebio_review.persistence import write_json
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.v5.assessment import DECISION_INSTRUCTION
from spacebio_review.v5.assessment_authoring import decision_schema, decision_type
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.citation_writing import author_messages
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime

ARMS = ("baseline", "contribution", "examples", "combined")
CASES = ("sample2_original", "sample2_facts", "new_question", "routine", "unanswerable")
SCIENCE = ("scientific_objectives", "innovation", "expected_outcomes")

CONTRIBUTION = """本次评价围绕具体知识贡献形成判断。直接在现有综合评价和各项 reason 中完成下列论证，
不另写研究计划或补充一轮评价：以已有研究作为比较基线，判断本申请实际安排能新增什么认识，
说明这一步为何重要或为何贡献有限。研究领域的重要性本身不能替代本项目的贡献。
科学目标与意义：指出值得回答的具体未知，以及现有设计能够把认识推进到哪里。
创新性：比较已有工作，定位问题、概念、关系或方法的实际新意及其程度；方法成熟本身不决定问题是否原创。
预期成果与效益：按实际读出判断可合理期待的成果及其意义，区分有用的描述性发现、关联认识和因果机制。
对决定性局限，具体判断它削弱的是哪一项贡献、削弱到什么程度，随后评价仍能获得的认识；
申请承诺与实际可达成果的差距也计入判断，不替申请添加实验或换题。科学结果未知是研究问题，
而设计不能获得有解释力的结果是方法问题。探索性工作可以重要，也可以平庸，由具体比较决定。
研究基础、实施与安全按已有依据和实际阶段评价；计划中的合理适配仍是拟开展工作，不算已完成验证。
每项 reason 将贡献或能力、决定性限制的实际影响与本轮国标档位联系起来，说明为何取该档及档内位置。
各指标按各自作用评价，同一限制确有不同影响时分别解释。分值由论据决定，不预设总分或支持结论。
"""

EXAMPLES = """以下三个虚构对照只示范判断方法，不是本申请事实，也不提供目标分数。
例一：两个项目均用成熟衍射方法研究蛋白晶体。甲比较两种已有结构模型作出不同预测的关键状态，
所需分辨率已经验证，结果可排除其中一个模型；乙在已有多次一致结构测定的同一状态重复测量，
没有精度提升、未决争议或新用途。甲的新意可来自问题与比较，乙不能仅因实验在新地点完成就获得同等创新评价。
例二：一项描述性观测能区分两种关于材料相变先后顺序的解释，尚不能确定全部分子机制。
其过程数据可有重要贡献，机制不完整主要限制机制承诺。另一项只采集终点照片，两个解释对此预测相同，
照片也没有独立的数据用途；不能把前者的探索价值套用到后者。
例三：一项测量中的光学参数仍须在已验证范围内优化，技术路径有依据；另一项的检测极限比目标效应大两个数量级，
且无替代终点。前者按准备程度判断，后者无法兑现核心认识。问题重大不能补偿第二项方案没有有效读出的缺陷。
"""

FACT_NOTES = [
    (
        "研究对象、比较与形态读出",
        """申请的对象是小鼠小肠隐窝向类器官形成的过程，在地面和在轨第3、6、9天观察大小和管腔数目。
原文未给出管腔数目的计数定义，不能直接替换为出芽数、隐窝数或认定多管腔必然病变。
已有肠类器官研究表明，尺寸与形态受到细胞状态、腔体液体转运和力学过程影响，因此形态变化不能直接等同于整体肠道功能或损伤。
申请安排地面镜像装置中的培养及成像适配，未列在轨1g或独立辐射实验臂，故空间—地面对比不能单独识别微重力和辐射的各自贡献。
其能够直接观察的是两种条件下形成过程的形态差异；申请对微重力特异作用及人体肠道健康的延伸需要额外依据。
生物学重复数、实验单元、统计分析和对照同步细节未充分交代。原有检索没有建立足够的全面优先权证据，不能据此断言世界首次。""",
    ),
    (
        "线粒体相关读出和已有生物学认识",
        """申请安排类核荧光形态、返回样品全长mtDNA PCR及测序、DCF和Fluo8等信号观测。
普通荧光形态不能单独证明相分离动态，测序变异频率也不等于直接测量所有化学损伤或新生突变速率。
通用DCF/Fluo8信号不能仅凭名称确认为线粒体特异ROS或钙信号，定位和适配需按实际方法判断；申请允许地面调试及其他钙氧染料。
所附空间多组学研究为线粒体应激相关研究提供背景；类核组装、复制应激和ROS/钙研究提供可能联系，不等于本申请已验证这些联系。
补充的Rodríguez-Colman等原始研究摘要报道小鼠隐窝细胞间代谢协作，线粒体活动与ROS信号参与干细胞功能和分化，并通过类器官形成反映作用。
因此ROS改变不能预设为有害。该研究是地面生物学依据，未证明本申请在轨会出现何种效应。
现有申请没有扰动实验或明确的跨指标关联分析设计，不能提前认定能形成因果机制；同时不能把培养中的隐窝整体当作终末分化、缓慢分裂细胞。
约10天是否检出变异受样本、测量灵敏度和背景异质性等条件影响，申请未提供本项目检测下限。""",
    ),
    (
        "已有载荷、实施安排和研究基础",
        """申请把Matrigel铺板放在地面操作段落，并安排在镜像装置调试培养皿、换液、染料添加方式、浓度和成像参数。
在轨多次染色成像和返回后测序确在方案内。第10天仅写去掉培养基，没有写固定，也未说明后续保存温度和时长。
补充Liu等CTCEM论文公开摘要明确列出温度和CO2控制、自动换液、明场、荧光和激光共聚焦观察能力，并介绍细胞上行支持装置。
这些是公开平台能力，不等于申请已获配相应设备、已完成样品适配或已获得所有资源。
“自带显微镜”及设备费为零不能单独证明必须从零研制新载荷，也不能证明无需任何集成工作。
具体载荷接口、液路、样品保存与返回衔接仍未充分交代。申请给出团队与平台条件，未充分展示本项目预实验结果。
FLUMIAS-DEA的预染色方式是其具体任务安排，不能推成所有在轨系统必须预染色或不能在轨加液。""",
    ),
    (
        "安全与保存阶段",
        """申请实际使用荧光染料及Matrigel，风险章节以无技术风险概括，没有详细封装、废液或暴露控制说明。
所附SDS可用于识别具体试剂的危害，实际暴露风险还取决于用量、浓度、封装和操作；这些在申请中尚不明确。
不能由DMSO渗透性、游离高氯酸或高氯酸盐的一般资料直接推导本项目实际使用的微量染料盐必然产生严重乘员危害。
申请没有明确开放加液，不能将可能的操作方式写成已确定事实。Matrigel的储存、低温分装与已凝胶后的培养是不同阶段；
Corning资料同时说明低温处理和37摄氏度凝胶条件，不支持在轨活细胞培养全程维持负20摄氏度。
肿瘤来源的基质材料不等于培养物中含有可致瘤的活肿瘤细胞；需要考虑实际材料质量和污染控制。
现有材料支持安全控制内容交代不足，不能仅从这些未知确定具体不可接受的事故路径或否决事实。""",
    ),
]

COMMON_CONTROL = """以下为专用于评审实验的虚构完整案例。文献记录、预实验和平台条件都是本案例给定事实，
不是现实世界文献，不需要外部检索。请按给定国标评价，不将材料中的编号或虚构性质作为扣分因素。
项目利用既有空间生物培养载荷研究低风险酵母。已具备封闭培养、自动取样、温控和返回支持，已获配资源。
使用常规培养、细胞计数和功能测定方法。地面镜像预实验覆盖样品、检测范围与完整操作；团队有相关飞行经验。
每条件12个独立培养单元，安排随机化、盲法和预先规定的分析；样品保存、接口、预算及进度已落实。
样品与试剂封闭处理，完成匹配本项目实际材料和操作的安全验证，没有已知不可接受的安全风险。
项目是科学实验，不承担人员培训、设备验收或另行平台认证任务。"""

CONTROL_STORIES = {
    "new_question": """题目：空间暴露后的生长恢复轨迹。目标是区分一次已重复观察到的增殖下降，
究竟在返回后持续还是迅速恢复。这直接关系到现有累计生长损失模型的适用范围。
已核实两次飞行在相同菌株、培养条件中观察到末期增殖下降，但均只取得飞行终点，没有恢复阶段的数据。
两种解释都符合已有终点：持续状态改变预测返回后72小时仍低于基线；暂时适应预测24小时内恢复。
本项目保持前两次条件，在飞行中及返回后0、6、24、48、72小时以同一功能测定获得独立单元数据，
同时设置同批同步地面对照，记录温度和操作。预实验的检测能力足以区分上述预期差异。
不主张确定分子机制或分离所有空间环境因素。预期交付恢复时间轨迹及累计损失模型适用范围的数据依据。""",
    "routine": """题目：空间暴露后的生长恢复轨迹。目标是记录既定菌株返回后0、6、24、48、72小时的增殖水平。
已核实六次独立飞行在相同菌株、载荷、暴露时间及培养条件中完成同一时间序列，并均显示24小时内恢复。
现有结果精度充分且跨任务一致，没有尚待解决的矛盾、模型分歧或重复性疑问。本项目完全沿用这些条件和检测精度，
只在下一次飞行中追加同样的轨迹。没有扩大适用范围、改善测量、检验新解释或提出新的数据用途。
项目预期交付一份增殖轨迹数据和一篇论文，称之为空间环境研究成果；未提出额外应用需求。""",
    "unanswerable": """题目：空间暴露后的生长恢复轨迹。目标是区分一次已重复观察到的增殖下降，
究竟在返回后持续还是迅速恢复。这直接关系到现有累计生长损失模型的适用范围。
已核实两次飞行在相同菌株、培养条件中观察到末期增殖下降，但均只取得飞行终点，没有恢复阶段的数据。
两种解释都符合已有终点：持续状态改变预测返回后72小时仍低于基线；暂时适应预测24小时内恢复。
本项目决定在返航前将所有在轨与地面对照样品不可逆灭活，只返回灭活样品进行终点细胞计数；
不保存活样本，不在返回后测量功能，也不增加能区分两种解释的指标。两种解释预测相同的灭活终点计数。
灭活和计数操作经过地面验证且可靠，预实验只验证该操作，没有验证由灭活终点推断恢复轨迹。
核心预期成果仍是恢复时间轨迹；材料未提出这些已有终点计数的其他独立科学用途。""",
}


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def production_hashes():
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for base, pattern in (("src", "*.py"), ("config", "*.yaml"), ("skills", "*.md"))
        for p in sorted(Path(base).rglob(pattern))
    }


def make_prompt(arm):
    prompt = DECISION_INSTRUCTION
    if arm in ("contribution", "combined"):
        start = prompt.index("按申请实际目标与研究阶段评价")
        end = prompt.index("本步使用给定 JSON 接口")
        prompt = prompt[:start] + CONTRIBUTION + prompt[end:]
    if arm in ("examples", "combined"):
        prompt += "\n" + EXAMPLES
    return prompt


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    if (root / "design.json").exists():
        raise ValueError("Prepared experiment is immutable; use another output directory.")
    (root / "sources").mkdir(exist_ok=True)
    saved = next(Path("runs/mytest50_sample2/v5/assessments").glob("assessment-*.json"))
    original = load(saved)["review_input"]
    # Same date instruction in every case; historical application dates are not expiry dates.
    original["review_date"] = "2026-10-22；按当前科学项目评价，忽略申请中的2022年日期。"
    facts = deepcopy(original)
    facts["expert_opinions"] = [{"title": title, "body": body} for title, body in FACT_NOTES]
    facts["material_note"] = (
        "本组专题材料由实验者依据申请及来源整理，列出事实和未明确事项，不指定项目价值、分档或分数。"
        "原文仍随附，可直接用于判断。"
    )
    crypt = load(root / "sources/crypt-api.json")["resultList"]["result"][0]
    ctcem = (root / "sources/ctcem-browser.txt").read_text("utf-8")
    extra = [
        {
            "url": "https://pubmed.ncbi.nlm.nih.gov/28273069/",
            "title": crypt["title"],
            "availability": "abstract_only",
            "text": html.fromstring(crypt["abstractText"]).text_content(),
        },
        {
            "url": "https://link.springer.com/article/10.1007/s12217-023-10081-w",
            "title": "Research and Development of Cell Culture Devices Aboard the Chinese Space Station",
            "availability": "abstract_only",
            "text": "\n".join(re.findall(r"^L4[3-6]: (.+)$", ctcem, re.MULTILINE)),
        },
    ]
    if any(len(item["text"]) < 200 for item in extra):
        raise ValueError("Supplementary abstract was not captured completely.")
    facts["additional_originals"] = extra
    views = {"sample2_original": original, "sample2_facts": facts}
    for case, story in CONTROL_STORIES.items():
        views[case] = {
            "application": COMMON_CONTROL + "\n\n" + story,
            "expert_opinions": [],
            "originals": [],
            "evaluation": deepcopy(original["evaluation"]),
            "evaluation_instructions": deepcopy(original["evaluation_instructions"]),
            "review_date": original["review_date"],
        }
    cfg = load_config("config/runtime.yaml").model_dump(mode="json")
    cfg["budget"] = {"max_model_calls": 72, "max_embedding_calls": 0, "max_seconds": 14400}
    # Observe first attempts. A failed request remains a failed planned repetition.
    for model in cfg["models"].values():
        model["retries"] = 0
    (root / "probe-config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), "utf-8")
    for case, view in views.items():
        write_json(root / "inputs" / f"{case}.json", view)
    for arm in ARMS:
        path = root / "prompts" / f"{arm}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(make_prompt(arm), "utf-8")
    schedule = []
    for repeat in range(1, 4):
        jobs = [{"case": case, "arm": arm, "repeat": repeat} for case in CASES for arm in ARMS]
        random.Random(42048 + repeat).shuffle(jobs)
        schedule += jobs
    write_json(
        root / "design.json",
        {
            "source": str(saved),
            "arms": ARMS,
            "cases": CASES,
            "repeats": 3,
            "jobs": schedule,
            "production_hashes": production_hashes(),
            "input_hashes": {case: sha(view) for case, view in views.items()},
            "prompt_hashes": {arm: sha(make_prompt(arm)) for arm in ARMS},
            "model": cfg["models"]["scientific"],
            "main_comparison": "Within each input case only; input packages differ in more than factual accuracy.",
            "independence": "Fresh session and single call per job; no prior outputs, repeat labels or target scores in input. Provider sampling defaults, no seed override.",
            "interpretation": "Three repetitions describe observed variability; they do not establish statistical reliability or end-to-end improvement.",
            "full_review_runs": 0,
        },
    )
    (root / "protocol.md").write_text(
        """# 科学价值判断组合对照

实验在正式评价入口单次调用当前科学模型，thinking关闭；完整国标、输出接口和计分代码不变。
四组为现有任务、贡献判断任务、仅对照示例、两者结合，构成2×2组合。贡献任务替换原提示词中相应段落，不改变事实约束。
五种输入每种四组、各独立三次，共60次计划调用；三轮分批，每轮随机化顺序，同一共享限流器、最多3并发。
每次新会话、只有system与user消息；复用相同输入而不复用回复，重复编号不进入模型。保留服务默认采样参数，不声称获得统计独立随机种子。

## 材料与适用范围

- sample2_original：mytest50保存的真实评价输入，保留原有错误和缺失，只共同明确当前评价日期。
- sample2_facts：完整申请与原有全部原文保留，将四篇旧专家意见替换为人工整理的事实与未知说明，补入CTCEM与隐窝代谢原始研究摘要。未输入手工报告或目标分数。
- 三个虚构案例分别给出有用的新问题、重复已知结果、无法回答核心问题的设计。比较对象为明确的实验设定，不冒充真实文献。

facts组不仅改事实，也改变了旧稿措辞、长度和材料覆盖。两种sample2输入的分差不能归因于单一因素；提示词效应只作同一输入内比较。
补充文献只使用实际公开摘要，原文网页保存在sources。人工事实说明见inputs，所有输入在调用前冻结。

## 预先确定的观察项

记录六维分数、三项科学维度合计、总分、制度等级、否决、均值/样本标准差/极差/三次实际值。
人工精读各项理由：是否具体比较已有认识与可得贡献；局限是否按实际损害解释；是否编造新设计或夸大首次/机制；低价值和无有效读出案例是否仍能区分。
不以涨到某个分数或通过60分为成功条件。三次重复只能给初步稳定性观察。
记录所有接口失败，不用修复重试替换失败重复。原始回复先保存，随后使用正式schema验证和正式计分。
正式链路、配置及技能均保持不变，不运行检索、专家研究或报告生成；完成后核对文件摘要。
""",
        "utf-8",
    )
    print(json.dumps({"prepared": str(root), "jobs": len(schedule)}), flush=True)


async def run(root, concurrency, supplement=False):
    design = load(root / "design.json")
    schedule = design["jobs"]
    batch = ""
    if not supplement and all(
        (root / "outputs" / s["case"] / s["arm"] / f"repeat-{s['repeat']}" / "result.json").exists()
        for s in schedule
    ):
        summarize(root)
        return
    if not supplement and (root / "runtime").exists():
        raise ValueError(
            "Preserve the partial batch; prepare a fresh output directory to rerun it."
        )
    if supplement:
        rows = [load(p) for p in (root / "outputs").glob("*/*/repeat-*/result.json")]
        schedule = []
        for case in CASES:
            for arm in ARMS:
                matches = [r for r in rows if r["case"] == case and r["arm"] == arm]
                missing = 3 - sum(r["status"] == "complete" for r in matches)
                last = max((r["repeat"] for r in matches), default=3)
                schedule.extend(
                    {"case": case, "arm": arm, "repeat": last + i + 1}
                    for i in range(max(missing, 0))
                )
        if not schedule:
            return
        number = 1
        while (root / f"supplement-{number}-plan.json").exists():
            number += 1
        batch = f"-supplement-{number}"
        write_json(
            root / f"supplement-{number}-plan.json",
            {
                "jobs": schedule,
                "reason": "Fresh independent calls to obtain three valid decisions per cell. "
                "All primary failures remain recorded; no error feedback or repair prompt.",
            },
        )
    runtime = ResearchRuntime.from_config(root / "probe-config.yaml", root / f"runtime{batch}")
    gate = asyncio.Semaphore(concurrency)

    async def job(spec):
        name = f"{spec['case']}/{spec['arm']}/repeat-{spec['repeat']}"
        folder = root / "outputs" / name
        if (folder / "result.json").exists():
            return
        folder.mkdir(parents=True, exist_ok=False)
        async with gate:
            started = time.monotonic()
            view = load(root / "inputs" / f"{spec['case']}.json")
            system = EvaluationSystem.model_validate(view["evaluation"])
            instruction = (root / "prompts" / f"{spec['arm']}.txt").read_text("utf-8")
            instruction += "\n提交的 JSON 结构：\n" + json.dumps(
                decision_schema(system), ensure_ascii=False
            )
            messages = [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(view, ensure_ascii=False)},
            ]
            state = runtime.load(
                runtime.create(
                    SessionTask(
                        role="chief",
                        objective="根据给定申请和材料形成国标评价。",
                        application=view["application"],
                    ),
                    allowance=SessionAllowance(completion_calls=0),
                )
            )
            # Recover stable evidence IDs for the same short-citation projection as production.
            handles = dict.fromkeys(re.findall(r"evidence-[0-9a-f]{16}", messages[1]["content"]))
            state.evidence = {h: {} for h in handles}
            actual = author_messages(messages, state.evidence)
            write_json(folder / "request.json", actual)
            row = {**spec, "status": "running", "input_sha256": sha(actual)}
            print(json.dumps({"start": name}), flush=True)
            try:
                reply = await runtime._call_model(
                    state,
                    runtime.models[state.model_alias],
                    messages,
                    [],
                    purpose="completion",
                    stage="submit_assessment",
                    response_schema=decision_schema(system),
                )
                write_json(
                    folder / "response.json",
                    {
                        "message": reply.message,
                        "finish_reason": reply.finish_reason,
                        "input_tokens": reply.prompt_tokens,
                        "output_tokens": reply.completion_tokens,
                    },
                )
                value = (
                    decision_type(system).model_validate_json(reply.message["content"]).model_dump()
                )
                assessment = finalize_assessment(value, system)
                write_json(folder / "assessment.json", assessment)
                (folder / "review.md").write_text(
                    value["overall_judgment"]
                    + "\n\n"
                    + "\n\n".join(
                        f"## {c.label}：{value['dimensions'][c.id]['score']}/{c.max_score}\n\n"
                        + value["dimensions"][c.id]["reason"]
                        for c in system.criteria
                    ),
                    "utf-8",
                )
                row.update(
                    status="complete",
                    total=assessment["total_score"],
                    grade=assessment["score_grade"],
                    recommendation=assessment["recommendation"],
                    veto=value["safety_veto"]["applies"],
                    scores={key: entry["score"] for key, entry in value["dimensions"].items()},
                    input_tokens=reply.prompt_tokens,
                    output_tokens=reply.completion_tokens,
                )
                row["science_total"] = sum(row["scores"][key] for key in SCIENCE)
            except Exception as exc:
                row.update(status="failed", error=repr(exc))
            finally:
                row["seconds"] = round(time.monotonic() - started, 2)
                runtime.budget.finish(state.session_id)
                write_json(folder / "result.json", row)
                print(json.dumps(row, ensure_ascii=False), flush=True)

    try:
        for repeat in sorted({s["repeat"] for s in schedule}):
            await asyncio.gather(*(job(s) for s in schedule if s["repeat"] == repeat))
            summarize(root)
    finally:
        write_json(
            root / f"execution{batch}.json",
            {
                "usage": runtime.budget.usage(),
                "production_unchanged": production_hashes() == design["production_hashes"],
                "full_review_runs": 0,
            },
        )
        write_json(root / f"scheduler{batch}.json", runtime.scheduler.records)
        await runtime.close()
        summarize(root)


def summarize(root):
    rows = [load(p) for p in sorted((root / "outputs").glob("*/*/repeat-*/result.json"))]
    groups = []
    for case in CASES:
        for arm in ARMS:
            matched = [r for r in rows if r["case"] == case and r["arm"] == arm]
            complete = [r for r in matched if r["status"] == "complete"]
            group = {
                "case": case,
                "arm": arm,
                "complete": len(complete),
                "failed": len(matched) - len(complete),
                "metrics": {},
            }
            for key in (
                "total",
                "science_total",
                *SCIENCE,
                "experiment_feasibility",
                "safety",
                "research_foundation",
            ):
                vals = [r[key] if key in r else r["scores"][key] for r in complete]
                group["metrics"][key] = {
                    "values": vals,
                    "mean": statistics.mean(vals) if vals else None,
                    "sd": statistics.stdev(vals) if len(vals) > 1 else None,
                    "range": max(vals) - min(vals) if vals else None,
                }
            group["grades"] = [r["grade"] for r in complete]
            groups.append(group)
    write_json(root / "summary.json", {"rows": rows, "groups": groups})
    lines = [
        "# 三份有效决定的评分统计",
        "",
        "科学合计=科学目标24+创新22+预期成果12，共58分。",
        "部分组合包括独立补充调用；原定三轮及失败记录见 primary-and-supplement.md。",
        "",
        "|案例|组合|完成/失败|有效总分|均值|样本标准差|极差|有效科学合计|等级|",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g in groups:
        m = g["metrics"]["total"]
        lines.append(
            f"|{g['case']}|{g['arm']}|{g['complete']}/{g['failed']}|{m['values']}|"
            f"{round(m['mean'], 1) if m['mean'] is not None else ''}|"
            f"{round(m['sd'], 2) if m['sd'] is not None else ''}|{m['range']}|"
            f"{g['metrics']['science_total']['values']}|{','.join(g['grades'])}|"
        )
    (root / "scores.md").write_text("\n".join(lines) + "\n", "utf-8")
    print(
        json.dumps(
            {"summarized": len(rows), "failed": sum(r["status"] != "complete" for r in rows)}
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "summarize"))
    parser.add_argument("--output", default="runs/scientific_value_sample2")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--supplement", action="store_true")
    args = parser.parse_args()
    path = Path(args.output).resolve()
    if args.action == "prepare":
        prepare(path)
    elif args.action == "summarize":
        summarize(path)
    elif args.live:
        asyncio.run(run(path, args.concurrency, args.supplement))
    else:
        parser.error("run requires --live")
