"""Deterministic IR validators. The LLM proposes the IR; these decide if it is acceptable.

Each check returns (errors, warnings). Errors block and trigger the single repair call.
"""

from .config import registry, tokens
from .ir import iter_nodes

MAX_NODES = 400  # cost / abuse guard: a real screen rarely needs more
MAX_DEPTH = 12
MAX_TEXT = 500


def _type_ok(spec, value):
    t = spec["type"]
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return (
            isinstance(value, float)
            and value.is_integer()
            and spec.get("min", float("-inf")) <= value <= spec.get("max", float("inf"))
        )
    if t == "enum":
        return value in spec["values"]
    if t == "token":
        return isinstance(value, str) and value in tokens()[spec["group"]]
    if t == "string_list":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if t == "string_matrix":
        return isinstance(value, list) and all(
            isinstance(r, list) and all(isinstance(c, str) for c in r) for r in value
        )
    return False


def _describe(spec):
    t = spec["type"]
    if t == "enum":
        return f"one of {spec['values']}"
    if t == "token":
        return f"a {spec['group']} token: {sorted(tokens()[spec['group']])}"
    if t == "integer":
        return f"a whole number {spec.get('min')}..{spec.get('max')}"
    return t.replace("_", " ")


def check_registry(doc):
    """Types, variants, props, text and children against the component registry."""
    errors, warnings = [], []
    comps = registry()["components"]
    for path, node in iter_nodes(doc.root):
        spec = comps.get(node.type)
        if spec is None:  # schema enum makes this rare
            errors.append(f"{path}: unknown component '{node.type}'")
            continue
        if node.variant is not None and node.variant not in spec["variants"]:
            errors.append(f"{path} ({node.type}): variant '{node.variant}' not allowed, use one of {spec['variants']}")
        rule = spec["text"]
        if rule == "required" and not (node.text or "").strip():
            errors.append(f"{path} ({node.type}): text is required")
        if rule == "none" and node.text:
            errors.append(f"{path} ({node.type}): must not have text (put it in a prop or a child)")
        if spec["kind"] == "leaf" and node.children:
            errors.append(f"{path} ({node.type}): is a leaf component and cannot have children")
        seen = set()
        for p in node.props:
            if p.name in seen:
                errors.append(f"{path} ({node.type}): prop '{p.name}' given twice")
            seen.add(p.name)
            pspec = spec["props"].get(p.name)
            if pspec is None:
                errors.append(
                    f"{path} ({node.type}): unknown prop '{p.name}', allowed: {sorted(spec['props']) or 'none'}"
                )
            elif not _type_ok(pspec, p.value):
                errors.append(f"{path} ({node.type}): prop '{p.name}' must be {_describe(pspec)}, got {p.value!r}")
        for name, pspec in spec["props"].items():
            if pspec.get("required") and name not in seen:
                errors.append(f"{path} ({node.type}): missing required prop '{name}'")
    return errors, warnings


def check_structure(doc):
    """Size / depth / text-length limits, and a sensible root."""
    errors, warnings = [], []
    nodes = list(iter_nodes(doc.root))
    if len(nodes) > MAX_NODES:
        errors.append(f"document has {len(nodes)} nodes, limit is {MAX_NODES}")
    depth = max(path.count(".children[") for path, _ in nodes)
    if depth > MAX_DEPTH:
        errors.append(f"nesting depth {depth} exceeds limit {MAX_DEPTH}")
    for path, node in nodes:
        if node.text and len(node.text) > MAX_TEXT:
            errors.append(f"{path}: text longer than {MAX_TEXT} characters")
    if registry()["components"].get(doc.root.type, {}).get("kind") != "container":
        errors.append("root must be a container component (e.g. Stack or Grid)")
    return errors, warnings


def check_nesting(doc):
    """HTML nesting rules the registry cannot express."""
    errors, warnings = [], []

    def walk(node, path, in_form):
        if node.type == "Form" and in_form:
            errors.append(f"{path} (Form): a Form cannot be inside another Form (invalid HTML); use a Stack")
        for i, child in enumerate(node.children):
            walk(child, f"{path}.children[{i}]", in_form or node.type == "Form")

    walk(doc.root, "root", False)
    for path, node in iter_nodes(doc.root):
        active, items = node.prop("active"), node.prop("items")
        if isinstance(active, float) and isinstance(items, list) and active >= len(items):
            errors.append(
                f"{path} ({node.type}): active is {int(active)} but there are only {len(items)} items "
                f"(0-based index, 0..{len(items) - 1})"
            )
        for prop in ("items", "options"):
            values = node.prop(prop)
            if isinstance(values, list) and len(values) != len(set(values)):
                warnings.append(
                    f"{path} ({node.type}): duplicate {prop} {sorted({v for v in values if values.count(v) > 1})}"
                )
    return errors, warnings


def check_accessibility(doc):
    """IR-level a11y: labels/alt/text are enforced as required props in the registry;
    here we add checks the registry cannot express."""
    errors, warnings = [], []
    headings = [n for _, n in iter_nodes(doc.root) if n.type == "Heading"]
    if not headings:
        warnings.append("no Heading on the screen (screen readers use headings to navigate)")
    elif min(h.prop("level", 9) for h in headings) != 1:
        warnings.append("no level-1 Heading")
    for path, node in iter_nodes(doc.root):
        if node.type == "Image" and str(node.prop("alt", "")).strip().lower() in {"", "image", "picture", "img"}:
            errors.append(f"{path} (Image): alt text must describe the image")
    return errors, warnings


def coverage(doc):
    """Share of nodes expressed with real components rather than the Placeholder fallback."""
    nodes = [n for _, n in iter_nodes(doc.root)]
    placeholders = sum(1 for n in nodes if n.type == "Placeholder")
    return round(1 - placeholders / len(nodes), 3), placeholders


def validate(doc):
    errors, warnings = [], []
    for check in (check_structure, check_registry, check_nesting, check_accessibility):
        e, w = check(doc)
        errors += e
        warnings += w
    return errors, warnings
