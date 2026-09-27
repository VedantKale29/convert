from conftest import CASES, load_case

from uigen.config import registry, tokens
from uigen.ir import strict_json_schema
from uigen.validators import validate


def _walk_objects(schema, found):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for v in schema.values():
            _walk_objects(v, found)
    elif isinstance(schema, list):
        for v in schema:
            _walk_objects(v, found)


def test_schema_follows_openai_strict_rules():
    objects = []
    _walk_objects(strict_json_schema(), objects)
    assert len(objects) == 3  # document, prop, node
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_schema_enum_matches_registry():
    enum = strict_json_schema()["$defs"]["node"]["properties"]["type"]["enum"]
    assert set(enum) == set(registry()["components"])


def test_every_token_reference_in_registry_exists():
    for name, spec in registry()["components"].items():
        for prop, pspec in spec["props"].items():
            if pspec["type"] == "token":
                assert pspec["group"] in tokens(), (name, prop)


def test_eval_expected_irs_are_valid():
    for path in sorted(CASES.glob("*.expected.json")):
        errors, _ = validate(load_case(path.name.split(".")[0]))
        assert errors == [], (path.name, errors)
