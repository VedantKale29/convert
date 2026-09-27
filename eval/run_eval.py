"""Run the pipeline on every eval case and report quality + cost numbers.

python eval/run_eval.py              # real OpenAI calls (needs .env)
python eval/run_eval.py --scripted   # dry run of the harness itself (replies = expected IR)
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from uigen.evaluate import score  # noqa: E402
from uigen.ir import UIDocument  # noqa: E402
from uigen.llm import OpenAIProvider, ScriptedProvider  # noqa: E402
from uigen.pipeline import generate  # noqa: E402

CASES = ROOT / "eval" / "cases"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripted", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "eval" / "report.json"))
    args = ap.parse_args()
    load_dotenv(ROOT / ".env", override=True)

    rows = []
    for image in sorted(CASES.glob("*.png")):
        gold = UIDocument.model_validate_json((CASES / f"{image.stem}.expected.json").read_text())
        provider = ScriptedProvider([gold.model_dump_json()]) if args.scripted else OpenAIProvider()
        res = generate(image.read_bytes(), provider, use_cache=False)
        tr = res["trace"]
        row = {
            "case": image.stem,
            "status": res["status"],
            "repairs": tr["repair_count"],
            "input_tokens": tr["input_tokens"],
            "output_tokens": tr["output_tokens"],
            "total_ms": tr["total_ms"],
            "coverage": res["coverage"],
        }
        if res["ir"]:
            row.update(score(UIDocument.model_validate(res["ir"]), gold))
        q = res.get("quality")
        if q:
            row.update(
                fidelity=q["fidelity"],
                ocr_text_recall=q["text_recall"],
                ocr_text_precision=q["text_precision"],
                position=q["position"],
                ssim=q["ssim"],
                a11y_violations=q["a11y_violations"],
            )
        rows.append(row)
        print(json.dumps(row))

    ok = [r for r in rows if r["status"] == "success"]

    def mean(key):
        values = [r[key] for r in ok if r.get(key) is not None]
        return round(statistics.mean(values), 3) if values else None

    summary = {
        "cases": len(rows),
        "success_rate": round(len(ok) / len(rows), 3),
        "first_call_valid_rate": round(
            sum(1 for r in rows if r["repairs"] == 0 and r["status"] == "success") / len(rows), 3
        ),
        "repair_rate": round(sum(1 for r in rows if r["repairs"]) / len(rows), 3),
        "mean_structure_f1": mean("structure_f1"),
        "mean_type_f1": mean("type_f1"),
        "mean_text_recall": mean("text_recall"),
        "mean_coverage": mean("coverage"),
        "mean_fidelity": mean("fidelity"),
        "mean_ocr_text_recall": mean("ocr_text_recall"),
        "mean_ssim": mean("ssim"),
        "total_a11y_violations": sum(r.get("a11y_violations") or 0 for r in rows),
        "mean_total_ms": mean("total_ms"),
        "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows),
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "cases": rows}, indent=2))
    print("\nSUMMARY", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
