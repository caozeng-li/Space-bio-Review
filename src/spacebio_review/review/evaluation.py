"""The two evaluation systems and their authoritative scoring rules."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RuleValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ScoreBand(RuleValue):
    minimum: int
    maximum: int
    description: str


class Criterion(RuleValue):
    id: str
    label: str
    label_en: str
    description: str
    max_score: int | None = None
    bands: tuple[ScoreBand, ...] = ()


class ScoreGrade(RuleValue):
    id: str
    minimum: int
    maximum: int
    label: str
    label_en: str
    recommendation: str


class EvaluationSystem(RuleValue):
    id: Literal["general", "gbt42048_2022"]
    version: str
    label: str
    label_en: str
    scope: str
    skill: str
    core: str
    criteria: tuple[Criterion, ...]
    score_grades: tuple[ScoreGrade, ...] = ()

    @property
    def aspects(self):
        return tuple(item.id for item in self.criteria)

    def snapshot(self):
        return self.model_dump(mode="json")

    def prompt(self, aspects=None):
        rows = [self.label, "适用范围：" + self.scope, self.core]
        for criterion in self.criteria:
            if aspects is not None and criterion.id not in aspects:
                continue
            cap = f"（满分 {criterion.max_score}）" if criterion.max_score is not None else ""
            rows.append(f"{criterion.id}：{criterion.label}{cap}。{criterion.description}")
            rows.extend(
                f"  {band.minimum}—{band.maximum} 分：{band.description}"
                for band in criterion.bands
            )
        if self.score_grades and aspects is None:
            rows.append("各项采用整数分，直接相加；单项满分已体现权重。")
            rows.extend(
                f"总分 {grade.minimum}—{grade.maximum}：{grade.id}，{grade.label}"
                for grade in self.score_grades
            )
            rows.append("原文区间在 85 分重叠，本系统约定 85 分归 A。")
        return "\n".join(rows)

    def grade_for(self, total):
        return next(g for g in self.score_grades if g.minimum <= total <= g.maximum)


GENERAL = EvaluationSystem(
    id="general",
    version="general-1",
    label="通用科学项目评价",
    label_en="General scientific project evaluation",
    scope="科学与技术研究项目",
    skill="exploration-review",
    core="评价研究的知识贡献、方法与实施条件，区分申请陈述、外部知识、解释和未知。",
    criteria=tuple(
        Criterion(id=key, label=label, label_en=english, description=description)
        for key, label, english, description in (
            ("scientific_value", "科学价值", "Scientific value", "问题及预期认识的价值。"),
            ("innovation", "创新性", "Innovation", "相对已有研究的新意。"),
            ("rigor", "严谨性", "Rigor", "设计、测量、分析与推断的关系。"),
            ("feasibility", "可行性", "Feasibility", "实施条件与技术可实现性。"),
            ("safety", "安全性", "Safety", "人员、样本、环境与操作安全。"),
            ("management", "项目管理", "Management", "分工、资源、进度与关键依赖。"),
        )
    ),
)


def _criterion(key, label, english, description, cap, bands):
    return Criterion(
        id=key,
        label=label,
        label_en=english,
        description=description,
        max_score=cap,
        bands=tuple(ScoreBand(minimum=lo, maximum=hi, description=text) for lo, hi, text in bands),
    )


GBT42048 = EvaluationSystem(
    id="gbt42048_2022",
    version="gbt42048-experiment-1",
    label="GB/T 42048—2022 已有载荷实验项目评价",
    label_en="GB/T 42048-2022 evaluation of experiments using existing payloads",
    scope="基于在轨已有载荷实施的实验项目，包括实验模块、实验单元或实验样品。",
    skill="gbt42048-experiment-review",
    core="依据 GB/T 42048—2022 第 6 章适用要求、第 7.2 节和表 B.2，"
    "围绕计划书及专业研究评价六项指标。已进入评审的项目按现有材料完成六项评分。",
    criteria=(
        _criterion(
            "scientific_objectives",
            "科学目标与意义",
            "Scientific objectives and significance",
            "关注科学问题前沿性、国家重大需求、战略方向与发展规划、载人航天规划布局。",
            24,
            (
                (17, 24, "科学目标明确、意义重大，面向国际前沿"),
                (9, 16, "科学目标基本明确、意义较大"),
                (0, 8, "科学目标较明确，具有一定研究意义"),
            ),
        ),
        _criterion(
            "innovation",
            "创新性",
            "Innovation",
            "依据比较对象和证据判断原创思想及国际、国内创新水平。",
            22,
            (
                (16, 22, "具有原创科学思想，创新性强，达到国际领先水平"),
                (8, 15, "具备一定创新性，达到国际先进、国内领先水平"),
                (0, 7, "创新性一般"),
            ),
        ),
        _criterion(
            "experiment_feasibility",
            "实验方案与工程可实现性",
            "Experimental design and engineering feasibility",
            "研究方法合理性，以及依托已有载荷的资源、接口、操作和地面支持条件。",
            18,
            (
                (13, 18, "实验方案合理可行，工程可实现性高"),
                (7, 12, "实验方案基本合理，工程可实现性较高"),
                (0, 6, "实验方案不合理"),
            ),
        ),
        _criterion(
            "safety",
            "安全性",
            "Safety",
            "在载人航天平台实施的安全性和可靠性，关注航天员、平台与有效载荷及废物处置。"
            "影响航天员或平台安全的项目可一票否决，须有具体事实与依据。",
            18,
            ((13, 18, "安全性合理可行"), (7, 12, "安全性基本合理可行"), (0, 6, "安全性不合理")),
        ),
        _criterion(
            "research_foundation",
            "研究基础",
            "Research foundation",
            "关注已开展的空间科学实验或地面预先实验及其实际支持；区分已有基础与拟开展工作。",
            6,
            (
                (5, 6, "研究基础良好，曾开展空间科学实验或地面预先实验"),
                (3, 4, "具有一定研究基础"),
                (0, 2, "研究基础不够充分"),
            ),
        ),
        _criterion(
            "expected_outcomes",
            "预期成果与效益",
            "Expected outcomes and benefits",
            "关注可获得的重大科学发现或应用效益及其依据。",
            12,
            (
                (9, 12, "预期可取得重大科学成果或显著应用效益等"),
                (5, 8, "预期可取得重要科学成果或应用效益"),
                (0, 4, "预期成果和效益不够突出"),
            ),
        ),
    ),
    score_grades=(
        ScoreGrade(
            id="A",
            minimum=85,
            maximum=100,
            label="优秀，优先支持",
            label_en="Excellent; priority support",
            recommendation="priority_support",
        ),
        ScoreGrade(
            id="B",
            minimum=60,
            maximum=84,
            label="合格，支持",
            label_en="Qualified; support",
            recommendation="support",
        ),
        ScoreGrade(
            id="C",
            minimum=0,
            maximum=59,
            label="不通过",
            label_en="Not passed",
            recommendation="not_support",
        ),
    ),
)


def get_evaluation_system(identity="general"):
    return {GENERAL.id: GENERAL, GBT42048.id: GBT42048}[identity]
