"""Compile an authored manuscript, resolving its code-owned citation choices."""

from spacebio_review.prose import cited_ids, compile_authored_body


def compile_manuscript(raw):
    body = compile_authored_body(
        raw["content"],
        {"author_text_ref": raw["author_text_ref"], "session_id": raw["session_id"]},
        citation_refs=raw.get("citation_refs"),
    )
    if missing := set(cited_ids(body)) - set(raw["readings"]):
        raise ValueError("assembly_unread_reference:" + ",".join(sorted(missing)))
    return body
