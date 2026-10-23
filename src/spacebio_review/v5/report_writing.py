"""Shared review material -> section drafts -> one complete authored report."""

from __future__ import annotations

import json

from spacebio_review.agents.diagnostics import digest
from spacebio_review.authored_markdown import compile_blocks, plain_text
from spacebio_review.persistence import write_json

VOICE = """你是科学项目评审报告的主评审者，面向评审委员会形成统一、自然、有具体依据的中文报告。
完整申请、专家意见、关键原文和主审评价决定是各次写作共同的背景，保持既定评价的含义与程度。
综合评价已由主审直接依据申请和专家意见形成，存放在 decision.overall_judgment 中。
各指标围绕自身评价问题展开；同一事实的详细解释放在最相关章节，其他章节说明它的不同影响。
重要论据保留具体发现、对象、比较及成立条件，篇幅随论证需要分配。
这是对申请现有方案的评审，专家意见应组织为专业判断及论据，内部问答和研究过程留在工作记录中。
采用具体研究发现时，将对应原文 citation 字段的短标记（如 [证据12]）放在论述后；
已有论据的短标记随论据保留，直接选择材料提供的标记即可。无需编写证据哈希或 evidence: 链接。
代码将短标记映射回永久引用身份，统一生成正式编号和书目，不再由另一模型重选来源。
正文使用普通 Markdown；代码也负责正式分值、等级与评分表。
"""

WRITE = """只撰写 current_criterion 对应的指标正文，直接输出 Markdown。
完整申请和专家意见是论据来源，decision 中本项判断确定评价方向，已完成的章节供协调详略。
先给出有实质内容的判断，再以申请安排、已有研究或平台先例说明依据及其影响。
通常约600—1200中文字，重要论据充分展开，简单问题可以更短。
每段集中说明一个论点，通常100—200中文字，较长论证拆成几个连贯段落。
加粗用于段首的简短判断句，机构名和论文名保持普通文本。
涉及多个研究或方法的比较时，可以使用简洁表格；适合连贯说明的内容仍用自然段，
表格与文字互相补充，不逐项复述。结构和详略服从当前指标的内容。
项目介绍和整体取舍已由综合评价负责，本节只展开本项论证。
直接输出当前章节正文，不输出章节标题、分数或参考文献列表。
"""

SYNTHESIZE = """依据共同背景和各指标草稿，一次完成全文统稿，直接输出完整、连贯的中文报告正文。
综合评价原文沿用 decision.overall_judgment，不依据各节草稿重新作整体评价。
其余章节以草稿为底稿，完成以下三项工作：
1. 合并重复。同一研究先例、具体发现或方法局限，只在最相关的一章完整展开；其他章保留
它对本项评价的不同影响，用简短承接说明，不重复罗列同一组研究及发现。项目介绍由综合评价负责，
各指标直接进入本项判断，删并重复的前言与结尾小结。
2. 调整段落。每段集中一个论点，通常100—200中文字。草稿中把几个论点塞在一起的长段，
拆为连贯短段；多项限制用简短小标题或加粗的段首判断句组织。机构名和论文名使用普通文本。
适合比较的内容可以用简洁表格，其他内容用段落，避免表格和文字逐项重复。
3. 保留论据。具体研究发现、对象、比较、条件和引用是报告的实质，删并重复时保留在适当位置，
不压缩成结论提要，也不新增文献综述、机制推演或实验建议。共同申请、专家意见与原文用于理解
并保持既有判断的含义和程度。涉及论据位置调整时，直接在本次全文中完成。
这是最终可出版的全文，不是编辑计划或修改要求。篇幅随草稿内容，统稿后应更紧凑、更便于阅读。
按 report_outline 的顺序和标题输出所有章节，每章以“## 标题”开始，章内可按需使用三级标题。
从“## 综合评价”开始，直接输出正文，不加整篇标题、评分表、参考文献列表、代码围栏或编辑说明。
"""


class ReportWriting:
    def __init__(self, system, store):
        self.system, self.store = system, store

    def load(self, state):
        if state.report_writing_ref:
            return json.loads(
                self.store._path("report-writing", state.report_writing_ref).read_text("utf-8")
            )
        return {"drafts": {}}

    def outline(self):
        return [
            {"section_id": "summary", "title": "综合评价", "title_en": "Overall assessment"},
            *[
                {"section_id": item.id, "title": item.label, "title_en": item.label_en}
                for item in self.system.criteria
            ],
        ]

    def operations(self, work):
        return [
            *[
                "write_section:" + item.id for item in self.system.criteria
                if item.id not in work["drafts"]
            ],
            "synthesize_report",
        ]

    @staticmethod
    def output_tokens(operation, config):
        if operation == "synthesize_report":
            return config.max_tokens
        return min(config.max_tokens, 4096 + config.reasoning_reserve)

    def messages(self, work, operation, review_input, decision, *, estimate=False):
        # All calls receive the same original material and decision. Research-stage
        # delivery instructions no longer govern the report-writing task.
        common = {
            key: value for key, value in review_input.items()
            if key != "evaluation_instructions"
        }
        common["decision"] = decision
        drafts = [
            {"id": item.id, "title": item.label,
             "body": self.store.author_text(work["drafts"][item.id])["content"]}
            for item in self.system.criteria if item.id in work["drafts"]
        ]
        kind, _, criterion_id = operation.partition(":")
        if estimate:
            existing = {row["id"] for row in drafts}
            for item in self.system.criteria:
                if item.id == criterion_id:
                    break
                if item.id not in existing:
                    drafts.append({"id": item.id, "body": "报告具体论证及其引用。" * 100})
        task = {"operation": operation, "report_outline": self.outline(), "drafts": drafts}
        if criterion_id:
            item = next(item for item in self.system.criteria if item.id == criterion_id)
            task["current_criterion"] = {
                "id": item.id, "title": item.label, "description": item.description,
            }
        return [
            {"role": "system", "content": VOICE},
            {"role": "user", "content": json.dumps(common, ensure_ascii=False)},
            {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
            {"role": "user", "content": SYNTHESIZE if kind == "synthesize_report" else WRITE},
        ]

    def accept(self, state, work, raw_ref, raw):
        kind, _, criterion_id = raw["operation"].partition(":")
        sections = None
        if kind == "synthesize_report":
            sections = self.sections({**work, "report": raw_ref})
            work["report"] = raw_ref
        else:
            work["drafts"][criterion_id] = raw_ref
        ref = "writing-" + digest(work)
        write_json(self.store._path("report-writing", ref), work)
        state.report_writing_ref = ref
        return sections

    def sections(self, work):
        """Read chapter boundaries from the one authored text; never merge draft prose."""
        ref = work["report"]
        raw = self.store.author_text(ref)
        blocks = compile_blocks(
            raw["content"], {"author_text_ref": ref}, citation_refs=raw.get("citation_refs"),
        )
        sections = []
        for node in blocks:
            if node.kind == "heading" and node.attrs.get("level") == 2:
                sections.append({
                    "title": plain_text(node.children).strip(), "body": {"blocks": []},
                })
            elif sections:
                sections[-1]["body"]["blocks"].append(node.model_dump(mode="json"))
            else:
                raise ValueError("report_must_start_with_chapter")
        outline = self.outline()
        if ([section["title"] for section in sections] != [item["title"] for item in outline]
                or any(not section["body"]["blocks"] for section in sections)):
            raise ValueError("report_chapters_do_not_match_outline")
        return [{**item, "body": section["body"]} for item, section in zip(outline, sections)]
