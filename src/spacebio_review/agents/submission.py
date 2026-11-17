"""Keep scientific text while explicitly detaching unverifiable citation links."""

from copy import deepcopy

from .diagnostics import digest


def at(data, path):
    for key in path:
        data = data[key]
    return data


def pointer(path):
    return "".join("/" + str(k).replace("~", "~0").replace("/", "~1") for k in path)


def repair_paths(data, errors):
    """Select complete lists/evidence units; never invite a rewrite of sibling findings."""
    paths = []
    for error in errors:
        path = list(error.get("location", []))
        if error.get("type") == "extra_forbidden":
            path = path[:-1]
        # A whole evidence unit owns kind, sources and quantitative provenance together.
        for size in range(len(path) + 1):
            try:
                node = at(data, path[:size])
            except (KeyError, IndexError, TypeError):
                break
            if isinstance(node, dict) and (
                "support_explanation" in node
                or (
                    "kind" in node
                    and "text" in node
                    and ("source_refs" in node or "quantitative_basis" in node)
                )
            ):
                path = path[:size]
                break
        for size, key in enumerate(path):
            if key in ("source_refs", "references"):
                path = path[: size + 1]
                break
        # Missing fields may be inserted, but never invent an absent parent or list index.
        while path:
            try:
                parent = at(data, path[:-1])
                if isinstance(parent, dict) or (
                    isinstance(parent, list)
                    and isinstance(path[-1], int)
                    and 0 <= path[-1] < len(parent)
                ):
                    break
            except (KeyError, IndexError, TypeError):
                pass
            path.pop()
        paths.append(tuple(path))
    unique = sorted(set(paths), key=lambda p: (len(p), pointer(p)))
    return [p for i, p in enumerate(unique) if not any(p[: len(q)] == q for q in unique[:i])]


def schema_at(schema, path):
    node = schema
    for key in path:
        while "$ref" in node:
            node = at(schema, node["$ref"].removeprefix("#/").split("/"))
        if "anyOf" in node:
            options = [n for n in node["anyOf"] if n.get("type") != "null"]
            node = options[0] if options else {}
            while "$ref" in node:
                node = at(schema, node["$ref"].removeprefix("#/").split("/"))
        node = (
            node.get("items", {})
            if isinstance(key, int)
            else node.get("properties", {}).get(key, {})
        )
    return deepcopy(node)


class ReplacementPlan:
    def __init__(self, candidate, errors, schema):
        self.candidate = deepcopy(candidate)
        self.candidate_id = digest(candidate)
        self.paths = {pointer(p): p for p in repair_paths(candidate, errors)}
        self.schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["replacements"],
            "properties": {
                "replacements": {
                    "type": "object",
                    "additionalProperties": False,
                    "minProperties": 1,
                    "required": list(self.paths),
                    "properties": {
                        key: schema_at(schema, path) for key, path in self.paths.items()
                    },
                },
            },
            "$defs": deepcopy(schema.get("$defs", {})),
        }

    def apply(self, patch):
        # This plan is bound to one frozen candidate in the calling runtime, not model input.
        if set(patch) != {"replacements"}:
            raise ValueError("invalid_patch_shape")
        replacements = patch["replacements"]
        if (
            not isinstance(replacements, dict)
            or not replacements
            or set(replacements) != self.paths.keys()
        ):
            raise ValueError("replacement_outside_allowed_paths")
        data = deepcopy(self.candidate)
        for key, value in replacements.items():
            path = self.paths[key]
            if not path:
                data = deepcopy(value)
            else:
                at(data, path[:-1])[path[-1]] = deepcopy(value)
        if not isinstance(data, dict):
            raise ValueError("expected_object")
        return data

    def context(self):
        units = {}
        for key, path in self.paths.items():
            try:
                units[key] = at(self.candidate, path)
            except KeyError:
                units[key] = {"missing": True}
        return {
            "original_invalid_values": units,
            "allowed_replacement_paths": list(self.paths),
            "replacement_rule": (
                (
                    'Fix all listed errors in one go, submitting replacement values for every '
                    'allowed path. The original erroneous value is not an optional value; '
                    'select supporting evidence for the original statement according to the '
                    'current catalog, preserving the original scientific meaning.'
                )
            ),
        }


def detach_citations(product, errors):
    if not errors or any(e.get("category") != "citation" for e in errors):
        return None, []
    data = deepcopy(product)
    issues, targets = [], {}
    for error in errors:
        location = error.get("location", [])
        if len(location) < 2 or location[-2] not in {"source_refs", "references"}:
            return None, []
        if not isinstance(location[-1], int):
            return None, []
        parent = data
        try:
            for key in location[:-1]:
                parent = parent[key]
            ref = parent[location[-1]]
            if not isinstance(parent, list) or not isinstance(ref, dict) or "source_id" not in ref:
                return None, []
        except (KeyError, IndexError, TypeError):
            return None, []
        issues.append(
            {**error, "reference": deepcopy(ref), "resolution": "detached_unverified_link"}
        )
        # Retain the declared provenance in the external diagnostic before downgrading
        # an unsupported statement. The model cannot submit or clear these issues.
        owner = data
        for key in location[:-2]:
            owner = owner[key]
        if isinstance(owner, dict) and "support_explanation" in owner:
            issues[-1]["declared_kind"] = owner.get("kind")
            issues[-1]["declared_quantitative_basis"] = deepcopy(owner.get("quantitative_basis"))
        targets.setdefault(tuple(location[:-1]), set()).add(location[-1])
    for path, indices in targets.items():
        parent = data
        for key in path:
            parent = parent[key]
        for index in sorted(indices, reverse=True):
            del parent[index]
        owner = data
        for key in path[:-1]:
            owner = owner[key]
        if not parent and isinstance(owner, dict) and "support_explanation" in owner:
            if owner.get("kind") in {"application", "external"}:
                owner["kind"] = "unknown"
            quantitative = owner.get("quantitative_basis")
            if quantitative and quantitative.get("origin") == "quoted":
                quantitative["origin"] = "unknown"
    return data, issues
