"""Small JSON-schema builders shared by proposal admission contracts."""

from typing import Literal

from pydantic import ConfigDict, create_model


def record(name, **fields):
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def selection(values):
    return Literal[tuple(values)]
