"""Builds notebooks/experiments.ipynb. Edit the cells here, then: uv run python tools/make_notebook.py"""

from pathlib import Path

import nbformat as nbf

md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
cells = []

cells += [
    md("""# UI generation experiments

Use this notebook to **experiment and tune** the wireframe → UI IR → React pipeline without editing product code.
Everything you can change lives in a `GenerationConfig` (prompt rules, image size and detail, repairs, fidelity repair).
Each experiment runs the same images with different configs and compares the numbers.

**Two modes**
- `MODE = "scripted"`: no API key, no network. The "LLM" replays the expected answer, so every config scores the same.
  Use it to learn the notebook and check your setup.
- `MODE = "openai"`: real calls. **Mock screens with dummy data only** until an endpoint is approved for client data.

**How to read the numbers**
| Column | Meaning | Good |
|---|---|---|
| `fidelity` | rendered result vs your image (text 50%, position 25%, SSIM 25%) | ≈0.85 is the ceiling on our seed images |
| `ir_structure_f1` | component tree vs the hand-checked expected IR (labeled cases only) | 1.0 = identical |
| `coverage` | share of elements expressed with real components (not Placeholder) | high |
| `ir_repairs` | validation-repair calls needed | 0 |
| `input_tokens` / `output_tokens` | cost drivers | low |
""")
]

cells += [
    md("## 0. Setup"),
    code("""import os
import sys
from pathlib import Path

ROOT = Path.cwd() if (Path.cwd() / "uigen").exists() else Path.cwd().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)
openai_api_key = os.getenv("OPENAI_API_KEY")
if openai_api_key and openai_api_key != "sk-...":
    print(f"OpenAI API Key exists and begins {openai_api_key[:8]}")
else:
    print("OpenAI API Key not set (fine for scripted mode)")

MODE = "scripted"  # "scripted" (offline) or "openai" (real calls)
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
if MODE == "openai":
    os.environ["ALLOW_EXTERNAL_LLM"] = "true"  # mock screens only!
print("MODE:", MODE, "| MODEL:", MODEL if MODE == "openai" else "scripted")"""),
    code("""import json

import matplotlib.pyplot as plt
import pandas as pd

from uigen import experiments as ex
from uigen.prompts import system_prompt
from uigen.settings import GenerationConfig

pd.set_option("display.max_colwidth", 60)
cases = ex.list_cases(labeled=True, unlabeled=True)
pd.DataFrame([{"case": n, "labeled (has expected IR)": e is not None} for n, _, e in cases])"""),
]

cases_md = """## 1. One run, inspected

`baseline` is the default config. `ex.show()` displays the scores, your image next to the rendered result,
the live preview, and the IR."""
cells += [
    md(cases_md),
    code("""baseline = GenerationConfig(name="baseline")
baseline.as_dict()"""),
    code("""name, image, expected = ex.list_cases(labeled=True)[1]  # try another index, or an unlabeled image in openai mode
res = ex.run_one(image, MODE, baseline, model=MODEL if MODE == "openai" else None, expected=expected)
ex.show(res)"""),
    md("What the model is told (the system prompt is generated from the component registry and your config):"),
    code("""print(system_prompt(baseline.soft_rules, baseline.extra_instructions)[:2500])"""),
]

cells += [
    md("""## 2. Experiment: prompt variants

Change **soft rules** (guidance) or add **extra instructions**. Hard rules (sizes, colours, allowed components) stay
enforced by code whatever the prompt says. Keep one change per variant so you know what caused a difference."""),
    code("""prompt_variants = [
    baseline,
    baseline.with_(name="grid-first", extra_instructions="When 2 or more similar cards are in a row, always use a Grid."),
    baseline.with_(name="no-soft-rules", soft_rules=()),
]
per_case, summary = ex.compare(prompt_variants, MODE, model=MODEL if MODE == "openai" else None)
summary"""),
    code("""per_case[["config", "case", "status", "fidelity", "ir_structure_f1", "coverage", "input_tokens"]]"""),
]

cells += [
    md("""## 3. Experiment: image detail and size (cost vs quality)

`image_detail="low"` and a smaller `max_side_px` cut input tokens. The question is how much fidelity you lose."""),
    code("""size_variants = [
    baseline,
    baseline.with_(name="detail-low", image_detail="low"),
    baseline.with_(name="max-1024px", max_side_px=1024),
]
per_case_size, summary_size = ex.compare(size_variants, MODE, model=MODEL if MODE == "openai" else None)
summary_size[["success_rate", "fidelity", "input_tokens", "output_tokens", "seconds"]]"""),
    code("""# Current prices per 1M tokens for your model (look them up; they change). Leave None to skip.
PRICE_INPUT_PER_1M = None
PRICE_OUTPUT_PER_1M = None
if PRICE_INPUT_PER_1M is not None and PRICE_OUTPUT_PER_1M is not None:
    for cfg in summary_size.index:
        print(cfg, ex.estimate_cost(per_case_size[per_case_size.config == cfg], PRICE_INPUT_PER_1M, PRICE_OUTPUT_PER_1M))
else:
    print("Set the prices above to estimate cost per generation.")"""),
]

cells += [
    md("""## 4. Experiment: fidelity repair

With `fidelity_repair=True`, a result below `fidelity_threshold` gets **one** more call listing the located
differences (missing text, invented text, wrong position). The repaired result is kept **only if it scores higher**.
Measure: how often it triggers, how much it helps, and what it costs."""),
    code("""repair_variants = [
    baseline,
    baseline.with_(name="fidelity-repair-0.6", fidelity_repair=True, fidelity_threshold=0.6),
    baseline.with_(name="fidelity-repair-0.75", fidelity_repair=True, fidelity_threshold=0.75),
]
per_case_rep, summary_rep = ex.compare(repair_variants, MODE, model=MODEL if MODE == "openai" else None)
display(summary_rep[["fidelity", "input_tokens", "seconds"]])
per_case_rep.groupby("config")["fidelity_repair"].value_counts(dropna=False)"""),
]

cells += [
    md("""## 5. Experiment: compare models (openai mode only)

Same config, different models. Add any vision model your key can use."""),
    code("""MODELS = ["gpt-4.1-mini", "gpt-4.1"]
if MODE == "openai":
    frames = [ex.run_suite(baseline.with_(name=m), MODE, model=m) for m in MODELS]
    per_case_models = pd.concat(frames, ignore_index=True)
    display(ex.summarize(per_case_models))
else:
    print("Switch MODE to 'openai' to compare models.")"""),
]

cells += [
    md("## 6. Chart the results"),
    code("""all_runs = pd.concat([per_case, per_case_size, per_case_rep], ignore_index=True)
overview = ex.summarize(all_runs)
fig, axes = plt.subplots(1, 2, figsize=(12, 4))
overview["fidelity"].plot.barh(ax=axes[0], title="Mean fidelity (higher is better)", xlim=(0, 1))
overview["input_tokens"].plot.barh(ax=axes[1], title="Mean input tokens (lower is cheaper)")
plt.tight_layout()
plt.show()"""),
]

cells += [
    md("""## 7. Calibrate the fidelity score with your own judgement

The fidelity weights (50/25/25) are a starting guess. Rate results yourself (0 = useless, 1 = perfect) and mark
whether each is **acceptable**. With 10+ ratings the notebook fits better weights and recommends a threshold.
Look at the image vs render in `ex.show()` before rating."""),
    code("""# Fill in after looking at results, keyed by (config, case). Example format:
ratings = {
    # ("baseline", "login"): (0.9, True),
    # ("baseline", "dashboard"): (0.7, True),
}
rated = all_runs.copy()
rated["human"] = [ratings.get((c, k), (None, None))[0] for c, k in zip(rated.config, rated.case, strict=True)]
rated["acceptable"] = [ratings.get((c, k), (None, None))[1] for c, k in zip(rated.config, rated.case, strict=True)]
if rated["human"].notna().sum() >= 5:
    print("weights:", ex.calibrate_weights(rated))
    print("threshold:", ex.recommend_threshold(rated))
else:
    print(f"{rated['human'].notna().sum()} ratings so far: add at least 5 (10+ recommended) to calibrate.")"""),
]

cells += [
    md("""## 8. Edit the contracts (registry and design tokens)

The component registry and design tokens are JSON files. After editing them, call `ex.reload_contracts()`.
Changing them changes the versions recorded on every trace and invalidates the cache automatically."""),
    code("""from uigen.config import registry, tokens

reg = registry()
pd.DataFrame([{"component": n, "kind": s["kind"], "variants": ", ".join(s["variants"]),
               "props": ", ".join(s["props"]) or "-"} for n, s in reg["components"].items()])"""),
    code("""# Example edit (uncomment to try): add a 'compact' variant to Card, then reload.
# path = ROOT / "config" / "registry.json"
# data = json.loads(path.read_text())
# data["components"]["Card"]["variants"].append("compact")
# data["version"] = "registry-v1-exp1"          # always bump the version when you change a contract
# path.write_text(json.dumps(data, indent=2))
print(ex.reload_contracts())
print("design tokens:", json.dumps(tokens()["components"], indent=1)[:400])"""),
]

cells += [
    md("""## 9. Fine-tuning dataset (only when prompting plateaus)

Fine-tuning is the **last** tuning step, not the first:
1. Tune prompts and configs first (sections 2-4). They are cheaper and reversible.
2. Collect **50-100+ labeled cases** (image + checked expected IR), with approval for the data.
3. Export them as chat fine-tuning examples (below), train, then compare the tuned model in section 5
   against the same eval set. Keep it only if the numbers improve.

Check the provider's current documentation for which models accept image fine-tuning examples."""),
    code("""ft = ex.export_finetune_jsonl(ex.EXPERIMENT_DIR / "finetune_train.jsonl", config=baseline)
print(ft)
first = json.loads(Path(ft["path"]).read_text().splitlines()[0])
print("roles:", [m["role"] for m in first["messages"]])
print("assistant target starts:", first["messages"][2]["content"][:120], "...")"""),
]

cells += [
    md("## 10. Save everything for your report"),
    code("""path = ex.save(all_runs, "notebook_session")
print("saved:", path)
overview"""),
]

nb = nbf.v4.new_notebook(
    cells=cells,
    metadata={
        "kernelspec": {"display_name": "Python 3 (uigen)", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    },
)
out = Path(__file__).resolve().parent.parent / "notebooks" / "experiments.ipynb"
nbf.write(nb, out)
print("wrote", out, len(cells), "cells")
