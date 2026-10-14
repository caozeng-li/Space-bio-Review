"""Publication paragraphs retain local citation spans without Markdown bookkeeping."""

from spacebio_review.prose import cited_ids as cited_ids
from spacebio_review.prose import prose_type as prose_type

from .publication_document import Node


def prose_nodes(prose):
    paragraphs = []
    for paragraph in prose["paragraphs"]:
        children = []
        for part in paragraph["parts"]:
            children.append(Node(kind="text", text=part["text"]))
            children.extend(Node(kind="citation", target=key) for key in part["cites"])
        paragraphs.append(Node(kind="paragraph", children=children))
    return paragraphs
