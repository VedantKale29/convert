import tempfile
from pathlib import Path

import pytest
from conftest import load_case

from uigen.build import build, security_scan, toolchain_ready
from uigen.compile_react import compile_react
from uigen.config import registry
from uigen.ir import UIDocument

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
CASES = ["login", "dashboard", "settings", "mobile_deliveries"]
needs_node = pytest.mark.skipif(not toolchain_ready(), reason="run 'npm install' in build_workspace/")


@pytest.mark.parametrize("case", CASES)
def test_output_matches_snapshot(case):
    """The compiler is deterministic: output must equal the reviewed snapshot byte for byte.
    To accept an intentional change: python tests/update_snapshots.py"""
    files = compile_react(load_case(case))
    assert files["App.jsx"] == (SNAPSHOTS / f"{case}.App.jsx").read_text()
    assert files == compile_react(load_case(case))  # same input -> same output


def _all_components_doc():
    kids = []
    samples = {
        "string": "x",
        "boolean": True,
        "integer": 2.0,
        "string_list": ["a", "b"],
        "string_matrix": [["1", "2"]],
        "token": "md",
    }
    for name, spec in registry()["components"].items():
        if name in ("Stack", "Grid"):
            continue
        props = []
        for p, s in spec["props"].items():
            value = s["values"][0] if s["type"] == "enum" else samples[s["type"]]
            props.append({"name": p, "value": value})
        kids.append(
            {
                "type": name,
                "variant": None,
                "text": "Sample" if spec["text"] != "none" else None,
                "props": props,
                "children": [],
            }
        )
    return UIDocument.model_validate(
        {
            "title": "all",
            "unsupported": [],
            "root": {
                "type": "Grid",
                "variant": None,
                "text": None,
                "props": [{"name": "columns", "value": 2}],
                "children": [
                    {
                        "type": "Stack",
                        "variant": None,
                        "text": None,
                        "props": [{"name": "direction", "value": "column"}],
                        "children": kids,
                    }
                ],
            },
        }
    )


@needs_node
def test_every_registry_component_compiles_and_builds():
    result = build(compile_react(_all_components_doc()), tempfile.mkdtemp())
    assert result["ok"], result["errors"]


@needs_node
def test_llm_text_cannot_become_code():
    evil = 'Hi"}</h1><script>alert(1)</script>{alert(document.cookie)}`${x}`'
    d = UIDocument.model_validate(
        {
            "title": evil,
            "unsupported": [],
            "root": {
                "type": "Stack",
                "variant": None,
                "text": None,
                "props": [{"name": "direction", "value": "column"}],
                "children": [
                    {
                        "type": "Heading",
                        "variant": None,
                        "text": evil,
                        "props": [{"name": "level", "value": 1}],
                        "children": [],
                    }
                ],
            },
        }
    )
    files = compile_react(d)
    assert "<script>" not in files["App.jsx"]  # escaped as \u003c... inside a JS string
    result = build(files, tempfile.mkdtemp())
    assert result["ok"], result["errors"]


def test_security_scan_catches_forbidden_output():
    assert security_scan({"App.jsx": "fetch('https://x.io')"}) == [
        "App.jsx: external URL in generated code",
        "App.jsx: network call in generated code",
    ]


@needs_node
def test_quotes_in_attributes_build():
    d = UIDocument.model_validate(
        {
            "title": 'say "hi"',
            "unsupported": [],
            "root": {
                "type": "Stack",
                "variant": None,
                "text": None,
                "props": [{"name": "direction", "value": "column"}],
                "children": [
                    {
                        "type": "Input",
                        "variant": None,
                        "text": None,
                        "props": [
                            {"name": "label", "value": 'Name "full"'},
                            {"name": "inputType", "value": "text"},
                            {"name": "placeholder", "value": 'e.g. O\'Brien "Jr" <tag>'},
                        ],
                        "children": [],
                    }
                ],
            },
        }
    )
    result = build(compile_react(d), tempfile.mkdtemp())
    assert result["ok"], result["errors"]
