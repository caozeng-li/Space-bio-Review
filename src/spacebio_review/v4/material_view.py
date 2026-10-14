"""Exact reading projection shared by professional and global scientific work."""

from copy import deepcopy

from .records import content_references

VERSION = "research-reading-view-1"


def reading_view(evidence, sources, research, *, application=()):
    """Keep each original once, adjacent to its first actual use, with local pointers."""
    locations = {}

    def references(value):
        refs = list(content_references(value))

        def explicit(node):
            if isinstance(node, dict):
                for field in ("evidence_ids", "sources"):
                    refs.extend(node.get(field, []))
                for child in node.values():
                    explicit(child)
            elif isinstance(node, list):
                for child in node:
                    explicit(child)

        explicit(value)
        return list(dict.fromkeys(key for key in refs if key in evidence))

    def originals(keys, path):
        result = {}
        for key in keys:
            row = evidence[key]
            source = sources[row["source"]]
            if key in locations:
                result[key] = {
                    "see": locations[key],
                    "title": source.get("title", ""),
                    "locator": row.get("locator", ""),
                    "reading_scope": row.get("reading_scope", ""),
                }
            else:
                result[key] = {
                    **deepcopy(row),
                    "title": source.get("title", ""),
                    "kind": source.get("kind", ""),
                }
                locations[key] = f"{path}.{key}"
        return result

    application_view = originals(application, "application")
    groups = {}
    for key, row in research.items():
        group = deepcopy({k: v for k, v in row.items() if k != "arguments"})
        group["originals"] = originals(references(group), f"research.{key}.originals")
        group["arguments"] = {}
        for identity, argument in row["arguments"].items():
            group["arguments"][identity] = {
                **deepcopy(argument),
                "originals": originals(
                    references(argument), f"research.{key}.arguments.{identity}.originals"
                ),
            }
        groups[key] = group
    return {
        "format_version": VERSION,
        "application": application_view,
        "research": groups,
        "available_originals": originals(
            [key for key in evidence if key not in locations], "available_originals"
        ),
        "sources": deepcopy(sources),
    }
