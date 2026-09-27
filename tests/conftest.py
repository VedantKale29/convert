import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CASES = ROOT / "eval" / "cases"


@pytest.fixture(autouse=True)
def allow_llm(monkeypatch):
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "true")


def load_case(name):
    from uigen.ir import UIDocument

    return UIDocument.model_validate(json.loads((CASES / f"{name}.expected.json").read_text()))
