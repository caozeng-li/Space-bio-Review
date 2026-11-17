"""Small generation boundary; persistence and execution annotations stay outside it."""

import types
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model

from .diagnostics import digest
from .serialization import output_example


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
        "scope": (
            'This opinion discusses the scope of comparisons that the measurement protocol can '
            'support.'
        ),
        "assessment": (
            'This endpoint can describe differences but cannot yet independently identify a '
            'unique mechanism.'
        ),
        "reasoning": (
            'Treatment and observation time points changed simultaneously, so there is an '
            'alternative factor that could explain the differences.'
        ),
        "consequence": (
            'The descriptive contribution still holds; mechanistic interpretation requires '
            'supplementary comparisons that can distinguish between factors.'
        ),
        "rationale": (
            'The value of the observed results must be distinguished from the sufficiency of '
            'mechanistic inference.'
        ),
        "support_explanation": (
            'Used to illustrate the boundaries of applicability, not to prove that this project'
            ' has completed validation.'
        ),
    }
    return meanings.get(field, (
        'Explain the basis, applicable conditions, and scope that cannot yet be determined for '
        'the current issue.'
    ))


@dataclass
class SubmissionContract:
    draft_type: type[BaseModel]
    result_type: type[BaseModel]
    assemble: object = None
    directory: object = None
    refresh: object = None

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
            "example_scope": (
                'Examples of structure and scientific expression; not facts of this project. '
                "Citations must use the current task's catalog."
            ),
            "fields_in_order": list(schema.get("properties", {})),
            "choices": self.directory() if self.directory else {},
        }

    def prompt(self, output_format="json"):
        """Keep execution metadata off the model-facing payload boundary."""
        import json

        instructions = self.instructions()
        if output_format == "yaml":
            return (
                (
                    'Now output only the unique YAML object conforming to the following schema,'
                    " without code fences. Use YAML's |- block scalar for free text such as "
                    'body and explanations, indented by two spaces; do not wrap body text in '
                    'double quotes, and write quotation marks, backslashes, and reference '
                    'markers within it literally, without JSON escaping. Enumeration values, '
                    'numbers, and dates remain on a single line; lists, numbers, and booleans '
                    'retain their original type. Retain acquired insights, scientific '
                    'conditions, and unknowns; do not conduct further research. The following '
                    'JSON Schema only describes the field structure, the actual output is YAML.'
                    '\nField order:'
                )
                + json.dumps(instructions["fields_in_order"], ensure_ascii=False)
                + '\nOptional catalog:'
                + json.dumps(instructions["choices"], ensure_ascii=False)
                + "\nJSON Schema："
                + json.dumps(instructions["schema"], ensure_ascii=False)
                + "\n"
                + instructions["example_scope"]
                + '\nExample of the sole output object:\n'
                + output_example(instructions["example"], instructions["schema"], "yaml")
            )
        return (
            (
                'Now output only the unique JSON object conforming to the following schema. '
                'Retain acquired insights, scientific conditions, and unknowns; do not conduct '
                'further research. The following schema, catalog, and field order are for '
                'filling instructions only, not output fields or outer wrappers.\nField order:'
            )
            + json.dumps(instructions["fields_in_order"], ensure_ascii=False)
            + '\nOptional catalog:'
            + json.dumps(instructions["choices"], ensure_ascii=False)
            + "\nJSON Schema："
            + json.dumps(instructions["schema"], ensure_ascii=False)
            + "\n"
            + instructions["example_scope"]
            + '\nExample of the sole output object:\n'
            + json.dumps(instructions["example"], ensure_ascii=False)
        )

    def metadata(self):
        return {
            "name": self.result_type.__name__,
            "schema_id": digest(self.schema()),
        }
