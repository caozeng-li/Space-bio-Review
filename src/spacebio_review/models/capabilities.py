"""Resolve actual formal request guarantees before spending research requests."""


def formal_capability(config, *, has_image=False, schema=None):
    thinking = (
        bool(config.extra_body.get("enable_thinking"))
        or (config.parameters.get("reasoning_effort") not in (None, "none"))
        or config.extra_body.get("thinking", {}).get("type") == "enabled"
    )
    mode = config.structured_output
    if has_image and not config.vision:
        raise ValueError("unsupported_image_input")
    if mode == "tool_call":
        raise ValueError("unsupported_structured_request")
    if mode == "json_schema":
        if not config.schema_supported or not config.capability_source:
            raise ValueError("undeclared_schema_capability")
        if has_image and not config.schema_with_images:
            raise ValueError("unsupported_schema_image_input")
        if thinking and not config.schema_with_thinking:
            raise ValueError("unsupported_schema_thinking")
        if schema is not None:
            check_schema(schema)
    if (
        "dashscope" in config.base_url
        and config.model.startswith("qwen3.5")
        and thinking
        and mode == "json_object"
    ):
        raise ValueError("unsupported_json_thinking_combination")
    reserve = config.reasoning_reserve if thinking else 0
    if thinking and reserve <= 0:
        raise ValueError("missing_formal_reasoning_reserve")
    body = config.max_tokens - reserve - 128
    if body < config.minimum_body_tokens:
        raise ValueError("insufficient_formal_body_budget")
    return {
        "level": "prompt_yaml"
        if mode == "yaml"
        else "schema_constrained"
        if mode == "json_schema"
        else "json_object",
        "source": "application_yaml_prompt"
        if mode == "yaml"
        else config.capability_source or "explicit_json_object_configuration",
        "verified": False if mode == "yaml" else config.capability_verified,
        "thinking": thinking,
        "reasoning_reserve": reserve,
        "reasoning_control": "service_setting"
        if "thinking_budget" in config.extra_body
        else "estimate_not_service_limit"
        if thinking
        else "disabled",
        "body_tokens_estimate": body,
        "protocol_reserve": 128,
    }


def check_schema(schema):
    """Conservative common subset. Never silently delete unsupported constraints."""
    unsupported = {"patternProperties", "unevaluatedProperties", "if", "then", "else", "not"}

    def visit(node):
        if isinstance(node, dict):
            if unsupported.intersection(node):
                raise ValueError("unsupported_schema_keyword")
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                raise ValueError("unsupported_open_schema_object")
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
