"""Random VALID UI IR documents, built from the registry, with hostile strings. Used by test_fuzz.py."""

import random

from uigen.config import registry, tokens
from uigen.ir import UIDocument

NASTY = [
    "quote \" and 'single'",
    "back`tick ${x}",
    "</script><script>alert(1)</script>",
    "{curly} {{double}}",
    "line\nbreak\ttab",
    "emoji 🚀 ✓ — ü ñ 中文",
    "&amp; &lt; <b>bold</b>",
    "\\backslash\\n",
    "a" * 300,
    "x\u2028y\u2029z",
    "   spaces   ",
    "null",
    "undefined",
    "0",
    "<!-- comment -->",
    "\u202e rtl override",
]


def _text(rng):
    return rng.choice(NASTY) if rng.random() < 0.5 else rng.choice(["Save", "Orders", "Total", "Sign in"])


def _value(rng, spec):
    t = spec["type"]
    if t == "enum":
        return rng.choice(spec["values"])
    if t == "token":
        return rng.choice(sorted(tokens()[spec["group"]]))
    if t == "integer":
        return float(rng.randint(int(spec.get("min", 1)), int(spec.get("max", 6))))
    if t == "boolean":
        return rng.random() < 0.5
    if t == "string":
        return _text(rng)
    if t == "string_list":
        items = [_text(rng) for _ in range(rng.randint(1, 4))]
        return items + items[:1] if rng.random() < 0.3 else items  # sometimes duplicates
    if t == "string_matrix":
        return [[_text(rng) for _ in range(rng.randint(1, 3))] for _ in range(rng.randint(0, 3))]
    raise ValueError(t)


def random_node(rng, depth=0, name=None, inside_form=False):
    comps = registry()["components"]
    if name is None:
        pool = [n for n, s in comps.items() if not (inside_form and n == "Form")]
        if depth >= 3:
            pool = [n for n in pool if comps[n]["kind"] == "leaf"]
        name = rng.choice(pool)
    spec = comps[name]
    props = [
        {"name": p, "value": _value(rng, s)}
        for p, s in spec["props"].items()
        if s.get("required") or rng.random() < 0.5
    ]
    items = next((p["value"] for p in props if p["name"] == "items"), None)
    for p in props:
        if p["name"] == "active":
            p["value"] = float(rng.randrange(len(items))) if items else None
    props = [p for p in props if p["value"] is not None]
    text = _text(rng) if spec["text"] == "required" or (spec["text"] == "optional" and rng.random() < 0.5) else None
    children = []
    if spec["kind"] == "container":
        children = [
            random_node(rng, depth + 1, inside_form=inside_form or name == "Form") for _ in range(rng.randint(0, 4))
        ]
    return {
        "type": name,
        "variant": rng.choice(spec["variants"] + [None]),
        "text": text,
        "props": props,
        "children": children,
    }


def random_doc(seed):
    rng = random.Random(seed)
    root = random_node(rng, 0, rng.choice(["Stack", "Grid"]))
    return UIDocument.model_validate({"title": _text(rng), "root": root, "unsupported": []})
