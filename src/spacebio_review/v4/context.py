"""One task directory for reads, generation and immutable reference binding."""

import json
import re
from copy import deepcopy

from spacebio_review.agents.contracts import ProductBindingError


class TaskContext:
    def __init__(self, *, compact_sources=False):
        self.evidence, self.results = {}, {}
        self.aliases, self.reverse = {}, {}
        self.frozen = False
        self.compact_sources = compact_sources

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

    def collect(self, node):
        if self.frozen:
            return
        if isinstance(node, dict):
            if isinstance(node.get("evidence"), dict):
                self.add(node["evidence"], "E")
            for child in node.values():
                self.collect(child)
        elif isinstance(node, list):
            for child in node:
                self.collect(child)

    def project(self, node, field=""):
        if isinstance(node, dict):
            return {
                self.aliases.get(k, k): self.project(v, k)
                for k, v in node.items()
                if k not in {"reference_mapping", "contract_version", "evidence"}
            }
        if isinstance(node, list):
            return [self.project(v, field) for v in node]
        if isinstance(node, str) and field in {
            "evidence_handles",
            "evidence_handle",
            "result_handles",
            "evidence_refs",
            "result_refs",
            "scope_links",
        }:
            return self.aliases.get(node, node)
        if isinstance(node, str):
            return re.sub(
                r"\[@([^]]+)\]", lambda m: "[@" + self.aliases.get(m[1], m[1]) + "]", node
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
                        v if k in {"text", "scope_note"} else self._restore(v, (*path, k), issues)
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

        text = re.sub(r"\[\s*@\s*([^\]\s]+)\s*\]", explicit, text)

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
        def convert(value, field=""):
            if isinstance(value, dict):
                return {k: convert(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [convert(v, field) for v in value]
            if isinstance(value, str) and field == "evidence_handles":
                return self.reverse.get(value, value)
            return value

        return json.dumps(convert(json.loads(raw)), ensure_ascii=False)

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
        evidence = {
            self.aliases[k]: {
                field: value[field]
                for field in (
                    "reference",
                    "title",
                    "kind",
                    "locator",
                    "quote",
                    "reading_scope",
                    "authors",
                    "publication_date",
                    "url",
                )
                if field in value and value[field] is not None
            }
            for k, value in self.evidence.items()
        }
        research = {}
        for key, value in self.results.items():
            artifact = value.get("artifact_ref", key)
            group = research.setdefault(
                artifact,
                {
                    "title": value.get("title", ""),
                    "scope_note": self.project(value.get("scope_note", "")),
                    "scope_links": self.project(value.get("scope_links", []), "scope_links"),
                    "blocks": {},
                },
            )
            group["blocks"][self.aliases[key]] = self.project(
                {
                    field: value[field]
                    for field in ("text", "evidence_refs", "result_refs")
                    if field in value
                }
            )
        return {"evidence": evidence, "research": research}

    def messages(self, messages):
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
                        "usage": "E 是材料位置；R 是完整专业论证，必须保留 scope_note 和局部条件。"
                        "所有输出文本（包括补研请求）引用使用 [@E1]、[@R1]，每个来源一个标记。"
                        "编号只能取 task_sources 目录；project 等材料名称是背景标签，不是引用编号。"
                        "项目事实请引用相应原文 E 编号，综合判断请引用支持它的 R 编号。"
                        "可选不表示支持任何结论；新事实需回读原文。",
                    },
                    ensure_ascii=False,
                ),
            }
        )
        return projected
