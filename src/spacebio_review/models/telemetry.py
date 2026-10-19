"""Public allowlist of effective request settings."""


def request_settings(kwargs):
    """Called at the provider boundary on the actual request, not later configuration."""
    keys = (
        "model", "max_tokens", "temperature", "top_p", "seed",
        "reasoning_effort", "tool_choice",
        "stream", "stream_options",
    )
    result = {k: kwargs[k] for k in keys if k in kwargs}
    extra = kwargs.get("extra_body", {})
    result.update(
        {
            k: extra[k]
            for k in (
                "temperature",
                "top_p",
                "seed",
                "reasoning_effort",
                "enable_thinking",
                "thinking_budget",
            )
            if k in extra
        }
    )
    result["response_format"] = kwargs.get("response_format", {}).get("type", "text")
    result["tools_enabled"] = bool(kwargs.get("tools"))
    return result
