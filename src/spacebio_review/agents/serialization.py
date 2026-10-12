"""Wire formats for formal products; their domain schema stays unchanged."""

import json

import yaml


class _LiteralText(str):
    pass


class _BlockDumper(yaml.SafeDumper):
    pass


_BlockDumper.add_representer(
    _LiteralText,
    lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|"),
)


def output_example(value, schema, output_format="json"):
    if output_format == "json":
        return json.dumps(value, ensure_ascii=False)

    def prose(node, definition):
        while "$ref" in definition:
            definition = schema["$defs"][definition["$ref"].split("/")[-1]]
        if "anyOf" in definition:
            definition = definition["anyOf"][0]
            return prose(node, definition)
        if isinstance(node, dict):
            return {
                key: prose(item, definition.get("properties", {}).get(key, {}))
                for key, item in node.items()
            }
        if isinstance(node, list):
            return [prose(item, definition.get("items", {})) for item in node]
        if isinstance(node, str) and not any(
            key in definition for key in ("enum", "const", "pattern", "format")
        ):
            return _LiteralText(node)
        return node

    return yaml.dump(
        prose(value, schema),
        Dumper=_BlockDumper,
        allow_unicode=True,
        sort_keys=False,
    )


def _unique(pairs, duplicate_reason="duplicate_object_key"):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise ValueError("expected_string_key")
        if key in result:
            raise ValueError(duplicate_reason)
        result[key] = value
    return result


class _ProductLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        return _unique(
            (self.construct_object(key, deep=True), self.construct_object(value, deep=True))
            for key, value in node.value
        )


def parse_output(raw, output_format="json"):
    if output_format == "yaml":
        try:
            value = yaml.load(raw, Loader=_ProductLoader)
        except yaml.YAMLError as exc:
            raise ValueError("invalid_yaml_or_product") from exc
    else:
        value = json.loads(
            raw, object_pairs_hook=lambda pairs: _unique(pairs, "duplicate_json_key")
        )
    if not isinstance(value, dict):
        raise ValueError("expected_object")
    return value
