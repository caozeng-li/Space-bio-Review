"""Small generation boundary; persistence and execution annotations stay outside it."""

import types
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model

from .diagnostics import digest


class ProductBindingError(ValueError):
    """A model-authored unit that cannot be bound to the supplied materials."""

    def __init__(self, location=(), reason="binding_failed", *, issues=None):
        self.issues = issues or [{"location": list(location), "type": reason}]
        self.issue = self.issues[0]
        super().__init__(self.issue["type"])


def generation_annotation(annotation):
    origin, args = get_origin(annotation), get_args(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return generation_type(annotation)
    if origin in (Union, types.UnionType):
        return Union[tuple(generation_annotation(a) for a in args)]
    if origin is Annotated:
        return Annotated[generation_annotation(args[0]), *args[1:]]
    if origin is list:
        return list[generation_annotation(args[0])]
    if origin is dict:
        return dict[args[0], generation_annotation(args[1])]
    return annotation


@lru_cache(maxsize=128)
def generation_type(model):
    fields = {
        name: (generation_annotation(info.annotation), deepcopy(info))
        for name, info in model.model_fields.items()
    }
    return create_model(
        model.__name__ + "Submission", __config__=ConfigDict(extra="forbid"), **fields
    )


def example_for(schema, root=None, field=""):
    """A complete structural example, with no ellipses or invented reference choices."""
    root = root or schema
    if "$ref" in schema:
        return example_for(root["$defs"][schema["$ref"].split("/")[-1]], root, field)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return example_for(schema["anyOf"][0], root, field)
    if "default" in schema:
        return deepcopy(schema["default"])
    kind = schema.get("type")
    if kind == "object":
        return {
            key: example_for(value, root, key)
            for key, value in schema.get("properties", {}).items()
            if key in schema.get("required", [])
        }
    if kind == "array":
        return [example_for(schema["items"], root, field) for _ in range(schema.get("minItems", 0))]
    if kind in ("number", "integer"):
        return schema.get("minimum", 1)
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if schema.get("format") == "date":
        return "2026-01-01"
    if schema.get("pattern") or field.endswith("_id"):
        return "example-1"
    meanings = {
        "scope": "本意见讨论测量方案可支持的比较范围。",
        "assessment": "该终点可描述差异，但尚不能独立识别唯一机制。",
        "reasoning": "处理和观测时点同时变化，因此存在另一种可解释差异的因素。",
        "consequence": "描述性贡献仍成立；机制解释需要补充能区分因素的比较。",
        "rationale": "需区分观测结果的价值与机制推断的充分性。",
        "support_explanation": "用于说明适用边界，不证明本项目已完成验证。",
    }
    return meanings.get(field, "说明当前问题的依据、适用条件及尚不能确定的范围。")


@dataclass
class SubmissionContract:
    draft_type: type[BaseModel]
    result_type: type[BaseModel]
    assemble: object = None
    version: str = "1"
    directory: object = None
    refresh: object = None
    task_context: object = None
    accept_product: object = None

    @classmethod
    def for_type(cls, result_type):
        return cls(generation_type(result_type), result_type)

    def schema(self):
        schema = self.draft_type.model_json_schema()

        def close(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    node["required"] = list(node["properties"])
                    node["additionalProperties"] = False
                for value in node.values():
                    close(value)
            elif isinstance(node, list):
                for value in node:
                    close(value)

        close(schema)
        return schema

    def parse(self, data):
        return self.draft_type.model_validate(data)

    def product(self, draft):
        data = self.assemble(draft) if self.assemble else draft.model_dump(mode="json")
        return self.result_type.model_validate(data)

    def instructions(self):
        schema = self.schema()
        sample = example_for(schema)
        # This validates the generated shape, not the truth of illustrative scientific prose.
        self.draft_type.model_validate(sample)
        return {
            "schema": schema,
            "example": sample,
            "example_scope": "结构及科学表达示例，不是本项目事实；引用须取当前任务目录。",
            "fields_in_order": list(schema.get("properties", {})),
            "choices": self.directory() if self.directory else {},
        }

    def prompt(self):
        """Keep execution metadata off the model-facing payload boundary."""
        import json

        instructions = self.instructions()
        return (
            "现在只输出符合以下 schema 的唯一 JSON 对象。"
            "保留已得认识、科学条件和未知，不再开展研究。"
            "下列 schema、目录和字段顺序均为填写说明，不是输出字段或外层包装。\n"
            "字段顺序："
            + json.dumps(instructions["fields_in_order"], ensure_ascii=False)
            + "\n可选目录："
            + json.dumps(instructions["choices"], ensure_ascii=False)
            + "\nJSON Schema："
            + json.dumps(instructions["schema"], ensure_ascii=False)
            + "\n"
            + instructions["example_scope"]
            + "\n唯一输出对象示例：\n"
            + json.dumps(instructions["example"], ensure_ascii=False)
        )

    def metadata(self):
        return {
            "name": self.result_type.__name__,
            "version": self.version,
            "schema_id": digest(self.schema()),
            "directory_version": digest(self.directory()) if self.directory else None,
        }
