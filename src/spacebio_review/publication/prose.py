"""Copy the compiled scientific body into a publication tree."""

from spacebio_review.authored_markdown import Node


def prose_nodes(prose):
    """Publication annotates its own copy; the selected manuscript remains immutable."""
    return [Node.model_validate(block).model_copy(deep=True) for block in prose["blocks"]]
