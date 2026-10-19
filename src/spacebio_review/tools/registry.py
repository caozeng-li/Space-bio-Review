from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, ValidationError


class ToolResult(BaseModel):
    ok: bool
    content: Any = None
    source_ids: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    reading_scope: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[Any], Awaitable[ToolResult]]
    read_only: bool = True
    timeout_seconds: float = 60
    exploration: bool | Callable[[str], bool] = False
    # Delegation is timed but must not hold a permit needed by its child tools.
    delegates: bool = False
    # Space for complete scientific prose; originals use the receiver's reading window.
    manuscript_tokens: int | Callable[[str], int] = 0

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None, *, inquiry=None):
        self.inquiry = inquiry
        self.argument_transform = None
        self.read_observers = []
        self.reading_windows = []
        self.material_providers = []
        self.original_page_readers = []
        self.reference_views = []
        self.reference_audits = []
        self.evidence_views = []
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def select(self, names: list[str]) -> ToolRegistry:
        selected = ToolRegistry([self._tools[n] for n in names], inquiry=self.inquiry)
        selected.read_observers = list(self.read_observers)
        selected.reading_windows = list(self.reading_windows)
        selected.material_providers = list(self.material_providers)
        selected.original_page_readers = list(self.original_page_readers)
        selected.reference_views = list(self.reference_views)
        selected.reference_audits = list(self.reference_audits)
        selected.evidence_views = list(self.evidence_views)
        selected.argument_transform = self.argument_transform
        return selected

    def record_read(self, payload):
        for observer in self.read_observers:
            observer(payload)

    def include(self, other: ToolRegistry) -> None:
        self.read_observers.extend(other.read_observers)
        self.reading_windows.extend(other.reading_windows)
        self.material_providers.extend(other.material_providers)
        self.original_page_readers.extend(other.original_page_readers)
        self.reference_views.extend(other.reference_views)
        self.reference_audits.extend(other.reference_audits)
        self.evidence_views.extend(other.evidence_views)
        for tool in other._tools.values():
            self.add(tool)
        if other.inquiry is not None:
            if self.inquiry is not None and self.inquiry is not other.inquiry:
                raise ValueError("Only one inquiry controller per task")
            self.inquiry = other.inquiry

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def read_only(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.read_only)

    def is_exploration(self, name: str, arguments: str) -> bool:
        tool = self._tools.get(name)
        if not tool:
            return False
        return tool.exploration(arguments) if callable(tool.exploration) else tool.exploration

    def is_discovery(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.exploration)

    def delegates(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.delegates)

    def manuscript_tokens(self, name: str, arguments: str) -> int:
        tool = self._tools.get(name)
        estimate = tool.manuscript_tokens if tool else 0
        try:
            return estimate(arguments) if callable(estimate) else estimate
        except (ValidationError, ValueError, KeyError, OSError):
            # Execution owns argument/source errors and returns the usual tool result.
            return 0

    def parse_arguments(self, name: str, arguments: str) -> BaseModel:
        """Apply the same tool contract to execution and its runtime consumers."""
        if self.argument_transform:
            arguments = self.argument_transform(arguments)
        return self._tools[name].arguments.model_validate_json(arguments)

    async def execute(self, name: str, arguments: str) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error="tool_not_available")
        try:
            args = self.parse_arguments(name, arguments)
        except ValidationError as exc:
            # Explain expected shape without reflecting possibly sensitive input values.
            shapes = {
                "model_type": "JSON object with the fields specified in the schema",
                "list_type": "JSON array",
                "dict_type": "JSON object",
                "string_type": "string",
                "missing": "required field",
                "json_invalid": "Complete JSON object with quoted strings correctly escaped",
            }
            errors = [
                {
                    "location": e["loc"],
                    "type": e["type"],
                    "expected": e.get("ctx", {}).get(
                        "expected",
                        shapes.get(e["type"], "See the corresponding field in the tool schema"),
                    ),
                    **(
                        {"detail": e["ctx"]["error"]}
                        if e["type"] == "json_invalid" and e.get("ctx", {}).get("error")
                        else {}
                    ),
                }
                for e in exc.errors()
            ]
            return ToolResult(ok=False, error="invalid_arguments", content=errors)
        except (ValueError, TypeError):
            return ToolResult(ok=False, error="invalid_arguments")
        try:
            result = await asyncio.wait_for(tool.handler(args), timeout=tool.timeout_seconds)
            if not isinstance(result, ToolResult):
                return ToolResult(ok=False, error="invalid_tool_result")
            if result.ok and isinstance(result.content, (dict, list)):
                # Attach handles beside the positions actually returned, not a global catalogue.
                choices = {k: v for view in self.evidence_views for k, v in view().items()}

                def enrich(node, source=None):
                    if isinstance(node, dict):
                        source = node.get("source_id", source)
                        pid, vid = node.get("passage_id"), node.get("visual_id")
                        if source and bool(pid) != bool(vid):
                            matches = [
                                handle
                                for handle, item in choices.items()
                                if item["reference"]
                                == {
                                    "source_id": source,
                                    "passage_id": pid,
                                    "visual_id": vid,
                                }
                                and ("text" not in node or item.get("quote") == node["text"])
                                and (
                                    not node.get("material_version")
                                    or item.get("material_version") == node["material_version"]
                                )
                            ]
                            if node.get("evidence_handle") in matches:
                                pass
                            elif len(matches) == 1:
                                node["evidence_handle"] = matches[0]
                        for value in list(node.values()):
                            enrich(value, source)
                    elif isinstance(node, list):
                        for value in node:
                            enrich(value, source)

                enrich(result.content)
            return result
        except TimeoutError:
            return ToolResult(ok=False, error="tool_timeout")
        except Exception:
            return ToolResult(ok=False, error="tool_execution_failed")
