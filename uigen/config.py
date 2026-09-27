"""Load the versioned contracts: component registry and design tokens."""

import json
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name):
    data = json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))
    data.pop("_note", None)
    return data


@lru_cache(maxsize=1)
def registry():
    return _load("registry.json")


@lru_cache(maxsize=1)
def tokens():
    return _load("design_tokens.json")
