"""Assemble immutable author blocks; the model selects, the program copies.

The only generated outputs are source selections, citation insertion positions,
and (for a decision) block selections and explicitly authored scalar values.
"""

from collections import Counter
from copy import deepcopy

from spacebio_review.authored_markdown import Node, plain_text
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, compile_authored_body


def record(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def choices(values):
    return {"type": "string", "enum": list(values)}


def array(items):
    return {"type": "array", "items": items}


def selection(properties):
    return {**record(properties), "required": []}


BIND = """你是装配员，正文已冻结。只返回引用注释计划，不能改写、补写、纠错正文。
逐块将应标注来源的陈述与提供的实际阅读原文对齐，尤其核对对象及条件限定。
annotations 的键只能选择 citation_positions.position；它指向一个正文段落、列表项或
表格单元格的末尾。每个位置先写 basis：用一句短语说明原文实际支持陈述的具体事实。
再在 sources 中按 source_id → unit_id → true 选择该事实所在的原文单元，只列需要的单元。
每个位置、来源与原文单元组合只出现一次，选择直接支撑具体论述的依据，不罗列相关文献。
正文不是待改写的草稿，不能把来源的措辞当成作者已经写出的内容。
source_id 下只能选择该来源实际阅读过的 unit_id。
每个原文段落旁的 unit_id 就是它所属的引用单元；passage_id 是原文段落位置。
阅读单元包含标题、正文及表格限定等完整上下文；根据单元实际内容匹配陈述，
程序绑定该单元已捕获的原文身份。不要拆出页眉或章节标题当作独立事实依据。
一个陈述需要多个单元时，分别选择；你不输出证据哈希、版本哈希或抄录原文。
basis 不抄录大段原文，只说明选择依据，不进入作者正文。先核对具体事实，再选择位置。
已有绑定引用专指 evidence: Markdown 链接，保持原样，不重复添加。
括号里的 E 字母、页码、文献号没有系统引用身份。按具体陈述匹配来源，不能只给全文总述加引。
原文页码、段落号、文献序号、蛋白名称等数字不是证据标识；不要依据数字相同建立关联。
只能给实际支持该陈述的原文加注；找不到时不制造引用，不改写陈述以迁就材料。
"""

LAYOUT = """你是报告装配员，作者的正文与决定已经冻结。只匹配已有章节并提取明说的值。
project 选择“项目理解/项目概况”章节，scope 选择“审议范围”章节，各指标 body 选择相应
指标章节，reason/support_scope 选择其对应章节。每个正文值只返回一个 section_id，
代码按原有标题层级复制该章节的完整正文，不拆段、不汇集其他栏目、不改写。
标量只提取作者明说的分数、等级、否决和建议，不重新评价，不推测遗漏的决定。
unknowns 选择每个未决事项自身的小节 section_id，不选择汇总它们的父章节；
无未知事项时返回空列表。选文列表只提取主审已经写明的候选编号及去向。"""


def unit_id(item):
    return (
        item.get("reading_structure", {}).get("unit_id")
        or item["reference"].get("passage_id")
        or item["reference"].get("visual_id")
    )


def support_schema(readings):
    """The generation grammar scopes existing native reading units by source."""
    units = {}
    for item in readings.values():
        units.setdefault(item["reference"]["source_id"], []).append(unit_id(item))
    return selection(
        {
            source: selection(
                {unit: {"type": "boolean", "enum": [True]} for unit in dict.fromkeys(values)}
            )
            for source, values in units.items()
        }
    )


class ManuscriptAssembly:
    def __init__(self, store, call):
        self.store, self.call = store, call

    def _reused(self, raw, previous):
        if not previous or not previous.get("author_text_ref"):
            return {}
        old = self.store.author_text(previous["author_text_ref"])
        nodes = previous["note"]["body"]["blocks"]
        counts = Counter(block["text"] for block in old["paragraphs"])
        return {
            block["text"]: [
                deepcopy(node) for node in nodes if node["origin"]["paragraph_id"] == block["id"]
            ]
            for block in old["paragraphs"]
            if counts[block["text"]] == 1
        }

    @staticmethod
    def originals(readings):
        """The assembler selects native source positions; stable citation IDs stay in code."""
        sources = render_originals(readings)
        for source in sources:
            for material in source["materials"]:
                for unit in material["units"]:
                    for passage in unit["passages"]:
                        passage.pop("citation", None)
                        passage.pop("evidence_handle", None)
                        passage["unit_id"] = unit["unit_id"]
        return sources

    async def assemble(
        self, state, raw, *, previous=None, decision_schema=None, purpose="auxiliary"
    ):
        readings = {key: state.evidence[key] for key in raw["readings"]}
        reused = self._reused(raw, previous)
        pending = [block for block in raw["paragraphs"] if block["text"] not in reused]
        # All originals already read for this author's work travel together. There
        # is no preliminary retrieval step that can silently discard the real source.
        selected = readings if pending else {}
        _, locations = prepare_body(raw, reused)
        pending_ids = {block["id"] for block in pending}
        available = {key: item for key, item in locations.items() if item["block"] in pending_ids}
        properties = {}
        if selected and available:
            properties["annotations"] = selection(
                {
                    position: record(
                        {
                            "basis": {"type": "string", "maxLength": 160},
                            "sources": {"$ref": "#/$defs/supports"},
                        }
                    )
                    for position in available
                }
            )
        plan = {}
        if decision_schema:
            from .assessment_authoring import authored_sections

            plan["decision"] = await self.call(
                state,
                "assembly_layout",
                LAYOUT,
                {"sections": authored_sections(raw["paragraphs"])},
                decision_schema,
                purpose=purpose,
            )
        if properties:
            citation_plan = await self.call(
                state,
                "assembly_bind",
                BIND,
                {
                    "blocks": [
                        {
                            **block,
                            "citation_positions": [
                                {
                                    "position": key,
                                    "text": plain_text([Node.model_validate(item["node"])]).strip(),
                                }
                                for key, item in available.items()
                                if item["block"] == block["id"]
                            ],
                        }
                        for block in pending
                    ],
                    "originals": self.originals(selected),
                },
                {**record(properties), "$defs": {"supports": support_schema(selected)}},
                purpose=purpose,
            )
            plan.update(citation_plan)
        artifact = {
            "author_text_ref": raw["author_text_ref"],
            "selected_readings": list(selected),
            "plan": plan,
        }
        write_json(self.store._path("assemblies", raw["author_text_ref"]), artifact)
        annotations = []
        for position, item in plan.get("annotations", {}).items():
            refs = []
            for source, units in item["sources"].items():
                refs.extend(
                    key
                    for key, original in selected.items()
                    if original["reference"]["source_id"] == source and unit_id(original) in units
                )
            annotations.append({"after": position, "references": refs})
        body = apply_annotations(raw, annotations, readings, reused)
        artifact.update(annotations=annotations, body=body)
        write_json(self.store._path("assemblies", raw["author_text_ref"]), artifact)
        return body, plan.get("decision")


def prepare_body(raw, reused=None):
    """Citation destinations are existing Markdown containers, never generated text."""
    reused = reused or {}
    blocks, locations = [], {}
    for block in raw["paragraphs"]:
        nodes = (
            deepcopy(reused[block["text"]])
            if block["text"] in reused
            else compile_authored_body(block["text"])["blocks"]
        )
        origin = {
            "author_text_ref": raw["author_text_ref"],
            "session_id": raw["session_id"],
            "paragraph_id": block["id"],
        }
        index = 0

        def visit(node):
            nonlocal index
            if node["kind"] in {"paragraph", "td", "th"}:
                index += 1
                locations[f"{block['id']}.{index}"] = {"block": block["id"], "node": node}
            else:
                for child in node["children"]:
                    visit(child)

        for node in nodes:
            node["origin"] = origin
            visit(node)
        blocks.extend(nodes)
    return {"blocks": blocks}, locations


def apply_annotations(raw, annotations, readings, reused=None):
    """Append citation nodes to selected containers; scientific nodes are copied intact."""
    body, locations = prepare_body(raw, reused)
    for item in annotations:
        node = locations[item["after"]]["node"]
        present = set(cited_ids(node))
        for ref in dict.fromkeys(item["references"]):
            if ref not in present:
                node["children"].append(Node(kind="citation", target=ref).model_dump(mode="json"))
                present.add(ref)
    if missing := set(cited_ids(body)) - readings.keys():
        raise ValueError("assembly_unread_reference:" + ",".join(sorted(missing)))
    return body
