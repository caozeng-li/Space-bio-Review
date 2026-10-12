"""One task directory for reads, generation and immutable reference binding."""

import json
import re
from copy import deepcopy

from spacebio_review.agents.contracts import ProductBindingError


class TaskContext:
    def __init__(self, *, compact_sources=False, structured_refs=False, inherited=False):
        self.evidence, self.results = {}, {}
        self.aliases, self.reverse = {}, {}
        self.frozen = False
        self.compact_sources = compact_sources
        self.structured_refs = structured_refs
        self.active = set()
        self.inherited = inherited
        self.members = {}
        self.book = None
        if inherited:
            from .statements import StatementBook

            self.book = StatementBook(self)

    def add(self, values, prefix):
        target = self.evidence if prefix == "E" else self.results
        for key, value in values.items():
            if not isinstance(value, dict):
                continue
            if key in target:
                if prefix == "E" and target[key].get("material_version") != value.get(
                    "material_version"
                ):
                    raise ValueError("evidence_version_conflict")
                continue
            if self.frozen:
                raise ValueError("submission_directory_is_frozen")
            target[key] = deepcopy(value)
            alias = prefix + str(1 + sum(a.startswith(prefix) for a in self.reverse))
            self.aliases[key], self.reverse[alias] = alias, key
            self.active.add(key)

    def checkpoint(self, memory):
        """Public claims, counterevidence and explicit selections own the working set."""
        data = memory.model_dump() if hasattr(memory, "model_dump") else memory
        refs = set(data.get("active_refs", []))
        if self.inherited:
            self.active = {self.reverse.get(key, key) for key in refs}
            return
        if self.structured_refs:
            for field in (
                "current_claims",
                "counterevidence",
                "revisions",
                "unresolved",
                "next_steps",
            ):
                for note in data.get(field, []):
                    refs.update(note["cites"])
        else:
            refs.update(re.findall(r"\[@([^]]+)\]", json.dumps(data, ensure_ascii=False)))
        self.active = {self.reverse.get(key, key) for key in refs}
        # Complete prior arguments remain indivisible, including their common scope.
        if not self.structured_refs:
            self.active.update(self.results)

    def citation_choices(self):
        eligible = set(self.active)
        if self.book:
            eligible.update(self.book.evidence_refs())
        pending = list(eligible) if self.inherited else []
        while pending:
            key = pending.pop()
            result = self.results.get(key, {})
            dependencies = (
                result.get("evidence_refs", [])
                + result.get("result_refs", [])
                + result.get("scope_links", [])
            )
            for ref in dependencies:
                if ref not in eligible:
                    eligible.add(ref)
                    pending.append(ref)
        return {
            self.aliases[k]: k
            for k in self.reverse.values()
            if not self.structured_refs or k in eligible
        }

    def memory_type(self):
        if self.book:
            return self.book.memory_type()
        from pydantic import Field

        from spacebio_review.evidence.submissions import record, selection

        choices = self.citation_choices()
        refs = (list[selection(choices)], ...) if choices else (list[str], Field(max_length=0))
        note = record("PublicNote", text=(str, ...), cites=refs)
        return record(
            "PublicWorkingMemory",
            **{
                k: (list[note], ...)
                for k in (
                    "current_claims",
                    "counterevidence",
                    "revisions",
                    "unresolved",
                    "next_steps",
                )
            },
            source_ids=(list[str], ...),
            active_refs=refs,
        )

    def bind_memory(self, memory):
        if self.book:
            return self.book.bind(memory)
        from spacebio_review.agents.types import WorkingMemory

        data = memory.model_dump() if hasattr(memory, "model_dump") else deepcopy(memory)
        for field in ("current_claims", "counterevidence", "revisions", "unresolved", "next_steps"):
            for note in data.get(field, []):
                note["cites"] = [self.reverse.get(k, k) for k in note["cites"]]
        data["active_refs"] = [self.reverse.get(k, k) for k in data.get("active_refs", [])]
        return WorkingMemory.model_validate(data)

    def activate_read(self, payload):
        source = payload.get("source_id")
        for passage in payload.get("passages", []):
            handle = passage.get("evidence_handle")
            key = self.reverse.get(handle, handle)
            matches = [
                k
                for k, v in self.evidence.items()
                if v["reference"]["source_id"] == source
                and v["reference"].get("passage_id") == passage.get("passage_id")
                and ("text" not in passage or v.get("quote") == passage["text"])
                and (
                    "material_version" not in passage
                    or v.get("material_version") == passage["material_version"]
                )
            ]
            if key in matches:
                self.active.add(key)
            elif len(matches) == 1:
                self.active.update(matches)

    def activate(self, refs):
        self.active.update(self.reverse.get(key, key) for key in refs)

    def collect(self, node):
        if self.frozen:
            return
        if isinstance(node, dict):
            for field in ("evidence", "evidence_resolution"):
                if isinstance(node.get(field), dict):
                    self.add(node[field], "E")
            for child in node.values():
                self.collect(child)
        elif isinstance(node, list):
            for child in node:
                self.collect(child)

    def prose_view(self, text, refs):
        """Adapt only archived, explicitly bound markers; ordinary prose stays literal."""
        allowed = {self.reverse.get(k, k) for k in refs}
        parts, start = [], 0
        for match in re.finditer(r"(?<!\\)\[@([^]]+)\]", text):
            key = self.reverse.get(match[1], match[1])
            if key not in allowed:
                continue
            preceding = text[start : match.start()]
            if preceding or not parts:
                parts.append({"text": preceding, "cites": []})
            parts[-1]["cites"].append(self.aliases.get(key, key))
            start = match.end()
        if start < len(text) or not parts:
            parts.append({"text": text[start:], "cites": []})
        return parts

    def project(self, node, field="", source=None):
        if isinstance(node, dict):
            if self.book and node.get("statement_id") in self.book.aliases:
                return {"use": self.book.aliases[node["statement_id"]]}
            if "cites" in node and "text" in node:
                return {**node, "cites": [self.aliases.get(k, k) for k in node["cites"]]}
            if self.structured_refs and "text" in node and "evidence_refs" in node:
                refs = node["evidence_refs"] + node.get("result_refs", [])
                return {
                    **{
                        k: self.project(v, k, source)
                        for k, v in node.items()
                        if k not in {"text", "evidence_refs", "result_refs"}
                    },
                    "content": self.prose_view(node["text"], refs),
                    "sources": [self.aliases.get(k, k) for k in refs],
                }
            source = node.get("source_id", source)
            if self.compact_sources and node.get("abstract"):
                record = next(
                    (
                        k
                        for k, v in self.evidence.items()
                        if v["reference"]["source_id"] == source
                        and v.get("quote") == node["abstract"]
                    ),
                    None,
                )
                if record:
                    node = {k: v for k, v in node.items() if k != "abstract"}
                    node["abstract_ref"] = self.aliases[record]
            if self.compact_sources and "text" in node:
                key = self.reverse.get(node.get("evidence_handle"), node.get("evidence_handle"))
                record = self.evidence.get(key)
                if record is None and source and node.get("passage_id"):
                    key, record = next(
                        (
                            (k, v)
                            for k, v in self.evidence.items()
                            if v.get("reference", {}).get("source_id") == source
                            and v["reference"].get("passage_id") == node["passage_id"]
                            and v.get("quote") == node["text"]
                        ),
                        (None, None),
                    )
                if record and record.get("quote") == node["text"]:
                    return {"evidence_ref": self.aliases[key]}
            return {
                self.aliases.get(k, k): self.project(v, k, source)
                for k, v in node.items()
                if k
                not in {"reference_mapping", "contract_version", "evidence", "citation_content"}
                and not (self.compact_sources and k == "evidence_resolution")
            }
        if isinstance(node, list):
            return [self.project(v, field, source) for v in node]
        if isinstance(node, str) and field in {
            "evidence_handles",
            "evidence_handle",
            "result_handles",
            "evidence_refs",
            "result_refs",
            "scope_links",
            "cites",
            "active_refs",
        }:
            return self.aliases.get(node, node)
        if isinstance(node, str):
            return re.sub(
                r"(?<!\\)\[@([^]]+)\]", lambda m: "[@" + self.aliases.get(m[1], m[1]) + "]", node
            )
        return node

    def restore(self, node, path=()):
        issues = []
        result = self._restore(node, path, issues)
        if issues:
            raise ProductBindingError(issues=issues)
        return result

    def _bind(self, text, allowed, path, issues):
        try:
            return self.bind_text(text, allowed)
        except ProductBindingError as exc:
            issues.extend({**issue, "location": list(path)} for issue in exc.issues)
            return text

    def _restore(self, node, path, issues):
        if isinstance(node, dict):
            result = {}
            for k, v in node.items():
                prefix = {
                    "evidence_refs": "E",
                    "evidence_handles": "E",
                    "result_refs": "R",
                    "result_handles": "R",
                }.get(k)
                if prefix:
                    if any(a not in self.reverse or not a.startswith(prefix) for a in v):
                        issues.append(
                            {
                                "location": list((*path, k)),
                                "type": f"invalid_{k}: choose from the current {prefix} directory",
                            }
                        )
                        continue
                    result[k] = [self.reverse[a] for a in v]
                else:
                    result[k] = (
                        self.reverse.get(v, v)
                        if k == "evidence_handle"
                        else v
                        if k in {"text", "scope_note"}
                        else self._restore(v, (*path, k), issues)
                    )
            if isinstance(result.get("text"), str):
                explicit = any(
                    k in result
                    for k in ("evidence_refs", "result_refs", "evidence_handles", "result_handles")
                )
                allowed = set(
                    result.get("evidence_refs", result.get("evidence_handles", []))
                ) | set(result.get("result_refs", result.get("result_handles", [])))
                result["text"] = self._bind(
                    result["text"],
                    allowed if explicit else set(self.reverse.values()),
                    path,
                    issues,
                )
                if not explicit:
                    refs = list(dict.fromkeys(re.findall(r"\[@([^]]+)\]", result["text"])))
                    result["evidence_refs"] = [r for r in refs if r in self.evidence]
                    result["result_refs"] = [r for r in refs if r in self.results]
            if isinstance(result.get("scope_note"), str):
                result["scope_note"] = self._bind(
                    result["scope_note"], set(self.reverse.values()), (*path, "scope_note"), issues
                )
                result["scope_links"] = re.findall(r"\[@([^]]+)\]", result["scope_note"])
            return result
        return (
            [self._restore(v, (*path, i), issues) for i, v in enumerate(node)]
            if isinstance(node, list)
            else self._bind(node, set(self.reverse.values()), path, issues)
            if isinstance(node, str)
            else node
        )

    def bind_text(self, text, allowed):
        errors = []
        # Use the existing range binder for equivalent citation typography. It
        # still resolves every member against the task's actual source directory.
        text = re.sub(
            r"\[\s*@?([ER])(\d+)\s*[-–—]\s*@?(?:\1)?(\d+)\s*\]",
            r"[\1\2-\1\3]",
            text,
        )

        def explicit(match):
            alias = match[1]
            key = self.reverse.get(alias, alias)
            if key not in allowed:
                errors.append("inline_reference_not_bound:" + alias)
                return match[0]
            return "[@" + key + "]"

        text = re.sub(r"(?<!\\)\[\s*@\s*([^\]\s]+)\s*\]", explicit, text)

        def bind(match):
            keys = []
            for token in re.findall(r"[ER]\d+(?:-[ER]?\d+)?", match.group()):
                if "-" in token:
                    start, end = token.split("-")
                    end = end[1:] if end[0] in "ER" else end
                    first, last = int(start[1:]), int(end)
                    if first > last or last - first > len(self.reverse):
                        errors.append("invalid_inline_reference_range")
                        continue
                    aliases = [start[0] + str(i) for i in range(first, last + 1)]
                else:
                    aliases = [token]
                for alias in aliases:
                    key = self.reverse.get(alias)
                    if key not in allowed:
                        errors.append("inline_reference_not_bound:" + alias)
                        continue
                    keys.append(key)
            return "".join("[@" + key + "]" for key in dict.fromkeys(keys))

        text = re.sub(
            r"[（(\[]\s*[ER]\d+(?:-[ER]?\d+)?"
            r"(?:\s*[,，、]\s*[ER]\d+(?:-[ER]?\d+)?)*\s*[)）\]]",
            bind,
            text,
        )
        if errors:
            raise ProductBindingError(
                issues=[{"location": [], "type": reason} for reason in dict.fromkeys(errors)]
            )
        return text

    def freeze(self):
        self.frozen = True

    def tool_arguments(self, raw):
        data = json.loads(raw)
        if self.structured_refs and "memory" in data:
            value = self.memory_type().model_validate(data["memory"])
            data["memory"] = self.bind_memory(value).model_dump()

        def convert(value, field=""):
            if isinstance(value, dict):
                return {k: convert(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [convert(v, field) for v in value]
            if isinstance(value, str) and field == "evidence_handles":
                return self.reverse.get(value, value)
            return value

        return json.dumps(convert(data), ensure_ascii=False)

    def mapping(self):
        return {
            a: {
                "identity": k,
                "value": deepcopy(self.evidence[k] if a.startswith("E") else self.results[k]),
            }
            for a, k in self.reverse.items()
        }

    def source_view(self):
        if not self.compact_sources:
            return {
                "evidence": {self.aliases[k]: v for k, v in self.evidence.items()},
                "research": {self.aliases[k]: self.project(v) for k, v in self.results.items()},
            }
        # The complete immutable records remain in mapping(). Only their presentation changes.
        sources, evidence, groups = {}, {}, {}
        for key, value in self.evidence.items():
            if key not in self.active:
                continue
            common = {
                field: value[field]
                for field in ("title", "kind", "authors", "publication_date", "url")
                if value.get(field)
            }
            common["source_id"] = value["reference"]["source_id"]
            identity = json.dumps(common, sort_keys=True)
            if identity not in groups:
                groups[identity] = "S" + str(len(groups) + 1)
                sources[groups[identity]] = common
            evidence[self.aliases[key]] = {
                "source": groups[identity],
                **{
                    field: value["reference"][field]
                    for field in ("passage_id", "visual_id")
                    if value["reference"].get(field)
                },
                **{
                    field: value[field]
                    for field in ("locator", "quote", "reading_scope")
                    if value.get(field)
                },
            }
        research = {}
        from .generation import render_parts

        def prose(value, text, parts, refs):
            if self.structured_refs:
                return (
                    self.project(value[parts])
                    if parts in value
                    else self.prose_view(value.get(text, ""), refs)
                )
            return (
                render_parts(value[parts], self.aliases)
                if parts in value
                else self.project(value.get(text, ""))
            )

        for key, value in self.results.items():
            if self.inherited:
                break
            if self.structured_refs and key not in self.active:
                continue
            artifact = value.get("artifact_ref", key)
            group = research.setdefault(
                artifact,
                {
                    "title": value.get("title", ""),
                    "origin": value.get("origin", "professional"),
                    "author": value.get("author", ""),
                    "scope": prose(
                        value, "scope_note", "scope_parts", value.get("scope_links", [])
                    ),
                    "sources": self.project(value.get("scope_links", []), "scope_links"),
                    "unknowns": value.get("unknowns", []),
                    "limitations": value.get("limitations", []),
                    "arguments": {},
                },
            )
            argument = {
                "content": prose(
                    value,
                    "text",
                    "parts",
                    value.get("evidence_refs", []) + value.get("result_refs", []),
                ),
                "sources": [
                    self.aliases.get(k, k)
                    for k in value.get("evidence_refs", []) + value.get("result_refs", [])
                ],
            }
            if value.get("basis"):
                argument["basis"] = [
                    {
                        **(
                            {"parts": self.bound_parts(b["text"], b.get("evidence_refs", []))}
                            if self.structured_refs
                            else {
                                "content": self.project(b["text"]),
                                "sources": [
                                    self.aliases.get(k, k) for k in b.get("evidence_refs", [])
                                ],
                            }
                        ),
                        "kind": b.get("kind"),
                        "support": self.project(b.get("support_explanation", "")),
                        "quantity": b.get("quantitative_basis"),
                        **(
                            {"unresolved_locations": b.get("source_refs", [])}
                            if not b.get("evidence_refs")
                            else {}
                        ),
                    }
                    for b in value["basis"]
                ]
            group["arguments"][self.aliases[key]] = argument
        view = {"sources": sources, "evidence": evidence, "research": research}
        if self.book:
            view.update(self.book.view())
            view["retained_evidence"] = {
                a: {
                    "kind": self.evidence[k].get("kind"),
                    "title": self.evidence[k].get("title"),
                    "locator": self.evidence[k].get("locator"),
                }
                for a, k in self.citation_choices().items()
                if a.startswith("E") and k not in self.active
            }
        return view

    def bound_parts(self, text, refs):
        parts = self.prose_view(text, refs)
        if not any(p["cites"] for p in parts):
            parts[0]["cites"] = [self.aliases.get(k, k) for k in refs]
        return parts

    def messages(self, messages, *, formal=False):
        if formal and self.book:
            return self.book.formal_messages(messages)
        projected = deepcopy(messages)
        for message in projected:
            if message.get("role") == "assistant":
                continue
            content = message.get("content")
            texts = (
                [{"text": content}]
                if isinstance(content, str)
                else [v for v in content or [] if v.get("type") == "text"]
            )
            for item in texts:
                try:
                    value = json.loads(item["text"])
                except (ValueError, TypeError):
                    continue
                self.collect(value)
                item["text"] = json.dumps(self.project(value), ensure_ascii=False)
            if isinstance(content, str):
                message["content"] = texts[0]["text"]
        projected.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_sources": self.source_view(),
                        "usage": (
                            "引用选择填写 cites，正文只写论述，不手写引用标记。"
                            if self.structured_refs
                            else "所有输出文本引用使用 [@E1]、[@R1]，每个来源一个标记。"
                        )
                        + "E 是材料位置；R 是有来源的完整论述，必须保留共同范围和局部条件。"
                        "origin=delegated 是子任务资料，不代表本专家认可或原始事实。"
                        "编号只能取本任务已有目录；工作笔记不产生 R，委派论述由程序登记。"
                        "引用委派结论的依据时使用其已有 E，自己的解释和未知可直接陈述，"
                        "不要为工作笔记或推断自造引用。"
                        "未展示原文仍在档案，可使用本任务实际提供的引用阅读工具"
                        "按 evidence_handles 回读，或按 source_id/passage_id 读取。"
                        "project 等材料名称是背景标签，不是引用编号。"
                        "项目事实请引用相应原文 E 编号，综合判断请引用支持它的 R 编号。"
                        "可选不表示支持任何结论；新事实需回读原文。",
                    },
                    ensure_ascii=False,
                ),
            }
        )
        if formal and len(projected) > 1:
            # Keep the output contract last; source-use instructions are background,
            # not a replacement for the requested wire format.
            projected.insert(len(projected) - 2, projected.pop())
        if self.compact_sources:
            from spacebio_review.agents.context import deduplicate_content

            return deduplicate_content(projected)
        return projected
