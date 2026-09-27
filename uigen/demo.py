"""Scripted 'LLM' for demos and integration tests: no key, no network, same answer for every image."""

import json
from pathlib import Path

from .llm import ScriptedProvider

_CASE = Path(__file__).resolve().parent.parent / "eval" / "cases" / "dashboard.expected.json"


def demo_provider():
    """1st reply breaks two registry rules (shows the repair step), 2nd is valid, 3rd serves a fidelity repair."""
    good = json.loads(_CASE.read_text())
    bad = json.loads(json.dumps(good))
    bad["root"]["children"][0]["variant"] = "neon"  # not an allowed variant
    bad["root"]["children"][1]["props"][0]["value"] = "diagonal"  # not an allowed direction
    return ScriptedProvider([json.dumps(bad), json.dumps(good), json.dumps(good)], model="demo")
