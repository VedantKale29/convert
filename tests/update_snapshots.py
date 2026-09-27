"""Regenerate compiler snapshots. Review the git diff before committing!"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from conftest import load_case  # noqa: E402

from uigen.compile_react import compile_react  # noqa: E402

for case in ["login", "dashboard", "settings"]:
    (ROOT / "tests" / "snapshots" / f"{case}.App.jsx").write_text(compile_react(load_case(case))["App.jsx"])
    print("updated", case)
