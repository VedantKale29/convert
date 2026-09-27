"""Canonical UI IR (intermediate representation), version ui-ir-v1.

The LLM produces this and nothing else. It says WHAT is on screen (components, variants,
text, props), never HOW it looks: no CSS, sizes or colours. Those come from design tokens.
"""

from pydantic import BaseModel, ConfigDict

from .config import registry

IR_VERSION = "ui-ir-v1"
PropValue = bool | float | str | list[str] | list[list[str]]


class Prop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: PropValue


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    variant: str | None = None
    text: str | None = None
    props: list[Prop] = []
    children: list["Node"] = []

    def prop(self, name, default=None):
        for p in self.props:
            if p.name == name:
                return p.value
        return default


class UIDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    root: Node
    unsupported: list[str] = []


def strict_json_schema():
    """JSON Schema for OpenAI structured outputs (strict mode): every object closes with
    additionalProperties=false and lists all properties as required; optional values are
    expressed as nullable instead. Component types are limited to the registry."""
    component_names = sorted(registry()["components"])
    prop_value = {
        "anyOf": [
            {"type": "string"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "array", "items": {"type": "string"}},
            {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        ]
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "root", "unsupported"],
        "properties": {
            "title": {"type": "string", "description": "Short name of the screen"},
            "root": {"$ref": "#/$defs/node"},
            "unsupported": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Things in the image the component vocabulary cannot express",
            },
        },
        "$defs": {
            "prop": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "value"],
                "properties": {"name": {"type": "string"}, "value": prop_value},
            },
            "node": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "variant", "text", "props", "children"],
                "properties": {
                    "type": {"type": "string", "enum": component_names},
                    "variant": {"type": ["string", "null"]},
                    "text": {"type": ["string", "null"]},
                    "props": {"type": "array", "items": {"$ref": "#/$defs/prop"}},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                },
            },
        },
    }


def iter_nodes(node, path="root"):
    """Depth-first walk yielding (path, node)."""
    yield path, node
    for i, child in enumerate(node.children):
        yield from iter_nodes(child, f"{path}.children[{i}]")
