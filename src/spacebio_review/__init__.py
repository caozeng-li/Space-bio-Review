"""Scientific project review through a continuing chief and topic researchers."""

__version__ = "5.0.0"


def __getattr__(name):
    if name in {"ReviewConfig", "ReviewResult", "run_review", "read_result"}:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(name)


__all__ = ["ReviewConfig", "ReviewResult", "run_review", "read_result", "__version__"]
