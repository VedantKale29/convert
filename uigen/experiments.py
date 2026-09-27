"""Helpers for notebooks/experiments.ipynb: run, inspect, compare, calibrate, export.

Nothing here changes the product code. Experiments vary a GenerationConfig and measure the effect.
"""

import base64
import html
import io
import json
import time
from pathlib import Path

import numpy as np

from .config import registry, tokens
from .evaluate import score
from .ir import UIDocument
from .llm import OpenAIProvider, ScriptedProvider
from .pipeline import generate
from .prompts import system_prompt, user_prompt
from .settings import GenerationConfig

ROOT = Path(__file__).resolve().parent.parent
LABELED = ROOT / "eval" / "cases"  # image + hand-checked expected IR
UNLABELED = ROOT / "eval" / "unlabeled"  # images only (fidelity metrics, no structure score)
EXPERIMENT_DIR = ROOT / "experiments"


# ---------------------------------------------------------------- cases & providers
def list_cases(labeled=True, unlabeled=False):
    """[(name, image_path, expected_path_or_None)]"""
    cases = []
    if labeled:
        cases += [(p.stem, p, LABELED / f"{p.stem}.expected.json") for p in sorted(LABELED.glob("*.png"))]
    if unlabeled:
        cases += [(p.stem, p, None) for p in sorted(UNLABELED.glob("*.png"))]
    return cases


def provider_for(mode, model=None, case_expected=None):
    """'openai' -> real calls. 'scripted' -> replies with the expected IR (dry run of the harness, no network)."""
    if mode == "openai":
        return OpenAIProvider(model)
    if mode == "scripted":
        if case_expected is None:
            raise ValueError("scripted mode needs a labeled case (it replays the expected IR)")
        return ScriptedProvider([Path(case_expected).read_text()] * 3, model="scripted")
    raise ValueError("mode must be 'openai' or 'scripted'")


def reload_contracts():
    """Call after editing config/registry.json or config/design_tokens.json in the notebook."""
    registry.cache_clear()
    tokens.cache_clear()
    return {"registry": registry()["version"], "design_system": tokens()["version"]}


# ---------------------------------------------------------------- single run
def run_one(image_path, mode="scripted", config=None, requirement="", model=None, expected=None):
    config = config or GenerationConfig()
    provider = provider_for(mode, model, expected)
    out = EXPERIMENT_DIR / "runs"
    res = generate(
        Path(image_path).read_bytes(),
        provider,
        requirement,
        runs_dir=out,
        cache_dir=EXPERIMENT_DIR / "cache",
        trace_dir=EXPERIMENT_DIR,
        config=config,
    )
    res["image_path"] = str(image_path)
    if expected and res["ir"]:
        res["score"] = score(
            UIDocument.model_validate(res["ir"]), UIDocument.model_validate_json(Path(expected).read_text())
        )
    return res


def summary_row(res, case=None, config_name=None):
    tr, q = res["trace"], res.get("quality") or {}
    row = {
        "case": case or Path(res.get("image_path", "?")).stem,
        "config": config_name or res["config"]["name"],
        "status": res["status"],
        "ir_repairs": tr["repair_count"],
        "fidelity_repair": (res.get("fidelity_repair") or {}).get("kept"),
        "input_tokens": tr["input_tokens"],
        "output_tokens": tr["output_tokens"],
        "seconds": round(tr["total_ms"] / 1000, 2),
        "coverage": res["coverage"],
        "fidelity": q.get("fidelity"),
        "text_f1": q.get("text_f1"),
        "position": q.get("position"),
        "ssim": q.get("ssim"),
        "a11y": q.get("a11y_violations"),
    }
    row.update({f"ir_{k}": v for k, v in (res.get("score") or {}).items() if k in ("structure_f1", "text_recall")})
    return row


def show(res):
    """Notebook view: status, scores, image vs render, live preview, IR."""
    from IPython.display import HTML, display

    q = res.get("quality") or {}
    img = base64.b64encode(Path(res["image_path"]).read_bytes()).decode() if res.get("image_path") else ""
    render = base64.b64encode(q["render_png"]).decode() if q.get("render_png") else ""
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td><b>{html.escape(str(v))}</b></td></tr>"
        for k, v in summary_row(res).items()
    )
    issues = "".join(f"<li>{html.escape(w)}</li>" for w in (res["errors"] + res["warnings"])[:20]) or "<li>none</li>"
    frame = (
        (
            f'<iframe sandbox="allow-scripts allow-forms" style="width:100%;height:520px;border:1px solid #ccc" '
            f'srcdoc="{html.escape(res["preview_html"], quote=True)}"></iframe>'
        )
        if res.get("preview_html")
        else ""
    )
    display(
        HTML(f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <table style="font-size:13px">{rows}</table>
      <div><b>Issues</b><ul style="font-size:13px">{issues}</ul></div>
    </div>
    <div style="display:flex;gap:8px;margin:8px 0">
      <figure style="flex:1;margin:0"><figcaption>Your image</figcaption>
        <img src="data:image/png;base64,{img}" style="width:100%;border:1px solid #ccc"></figure>
      <figure style="flex:1;margin:0"><figcaption>Rendered result</figcaption>
        {f'<img src="data:image/png;base64,{render}" style="width:100%;border:1px solid #ccc">' if render else "n/a"}</figure>
    </div>
    <details><summary>Live preview</summary>{frame}</details>
    <details><summary>UI IR</summary><pre style="font-size:11px">{html.escape(json.dumps(res["ir"], indent=2)) if res["ir"] else ""}</pre></details>
    """)
    )


# ---------------------------------------------------------------- suites & comparisons
def planned_calls(n_cases, config):
    """Worst-case number of LLM calls, for the budget guard."""
    return n_cases * (1 + config.max_ir_repairs + (1 if config.fidelity_repair else 0))


def run_suite(config, mode="scripted", cases=None, model=None, max_calls=60, verbose=True):
    """Run every case with one config. Returns a pandas DataFrame (one row per case)."""
    import pandas as pd

    cases = cases if cases is not None else list_cases(labeled=True, unlabeled=(mode == "openai"))
    if mode == "scripted":
        cases = [c for c in cases if c[2] is not None]
    worst = planned_calls(len(cases), config)
    if mode == "openai" and worst > max_calls:
        raise RuntimeError(f"up to {worst} LLM calls planned, budget is {max_calls}: raise max_calls deliberately")
    rows = []
    for name, image, expected in cases:
        t = time.time()
        res = run_one(image, mode, config, model=model, expected=expected)
        rows.append(summary_row(res, name, config.name))
        if verbose:
            print(
                f"  {config.name:>18} | {name:<32} {res['status']:<15} fidelity={rows[-1]['fidelity']}  "
                f"({time.time() - t:.1f}s)"
            )
            if rows[-1]["fidelity"] is None:
                reasons = [w for w in res["warnings"] if w.startswith("quality")] or ["quality stage did not run"]
                print(f"      ! no fidelity: {reasons[0]}")
    return pd.DataFrame(rows)


def compare(configs, mode="scripted", cases=None, model=None, max_calls=120):
    """Run several configs on the same cases. Returns (per_case_df, per_config_summary_df)."""
    import pandas as pd

    frames = [run_suite(c, mode, cases, model, max_calls) for c in configs]
    df = pd.concat(frames, ignore_index=True)
    return df, summarize(df)


def summarize(df):
    agg = {
        "status": lambda s: round((s == "success").mean(), 3),
        "ir_repairs": "mean",
        "input_tokens": "mean",
        "output_tokens": "mean",
        "seconds": "mean",
        "coverage": "mean",
        "fidelity": "mean",
        "text_f1": "mean",
        "position": "mean",
        "ssim": "mean",
        "a11y": "sum",
    }
    for col in ("ir_structure_f1", "ir_text_recall"):
        if col in df:
            agg[col] = "mean"
    out = df.groupby("config").agg({k: v for k, v in agg.items() if k in df}).rename(columns={"status": "success_rate"})
    return out.round(3)


def estimate_cost(df, usd_per_1m_input, usd_per_1m_output):
    """Cost per case and total, from the token counts. Prices change: pass current ones in."""
    per_case = df["input_tokens"] / 1e6 * usd_per_1m_input + df["output_tokens"] / 1e6 * usd_per_1m_output
    return {"mean_usd_per_generation": round(float(per_case.mean()), 5), "total_usd": round(float(per_case.sum()), 4)}


def save(df, name):
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPERIMENT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}.csv"
    df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------- calibration
def calibrate_weights(df, human_col="human"):
    """Fit fidelity weights to YOUR ratings (0..1). Returns weights for text_f1/position/ssim and the fit quality.

    Uses non-negative least squares by clipping + renormalising: simple, and enough for 3 features.
    Needs at least ~10 rated results to mean anything."""
    feats = ["text_f1", "position", "ssim"]
    data = df.dropna(subset=feats + [human_col])
    if len(data) < 5:
        raise ValueError(f"only {len(data)} rated rows: rate at least 5 (10+ recommended)")
    X, y = data[feats].to_numpy(float), data[human_col].to_numpy(float)
    w, *_ = np.linalg.lstsq(X, y, rcond=None)
    w = np.clip(w, 0, None)
    w = w / w.sum() if w.sum() > 0 else np.full(3, 1 / 3)
    pred = X @ w
    corr = float(np.corrcoef(pred, y)[0, 1]) if len(set(y)) > 1 else float("nan")
    current = X @ np.array([0.5, 0.25, 0.25])
    corr_now = float(np.corrcoef(current, y)[0, 1]) if len(set(y)) > 1 else float("nan")
    return {
        "weights": dict(zip(feats, np.round(w, 3).tolist(), strict=True)),
        "correlation_with_ratings": round(corr, 3),
        "current_weights_correlation": round(corr_now, 3),
        "rated_rows": len(data),
    }


def recommend_threshold(df, acceptable_col="acceptable", score_col="fidelity"):
    """Best fidelity cut-off to separate results you marked acceptable (True/False)."""
    data = df.dropna(subset=[score_col, acceptable_col])
    best = (0.0, None)
    for t in np.round(np.arange(0.3, 0.96, 0.01), 2):
        accuracy = float(((data[score_col] >= t) == data[acceptable_col].astype(bool)).mean())
        if accuracy > best[0]:
            best = (accuracy, float(t))
    return {"threshold": best[1], "accuracy": round(best[0], 3), "rated_rows": len(data)}


# ---------------------------------------------------------------- fine-tuning data
def export_finetune_jsonl(path, config=None, cases=None, max_side_px=None):
    """Write labeled cases as OpenAI chat fine-tuning examples (system + user[text, image] -> assistant JSON).

    Only prepare this after prompt experiments plateau, with approved data, and ideally 50-100+ cases.
    Check the provider's current fine-tuning docs for which models accept image examples."""
    from .governance import sanitize_image

    config = config or GenerationConfig()
    cases = cases if cases is not None else list_cases(labeled=True)
    system = system_prompt(config.soft_rules, config.extra_instructions)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for _name, image, expected in cases:
            if expected is None:
                continue
            png, _ = sanitize_image(Path(image).read_bytes(), max_side_px or config.max_side_px)
            target = UIDocument.model_validate_json(Path(expected).read_text()).model_dump_json()
            url = "data:image/png;base64," + base64.b64encode(png).decode()
            f.write(
                json.dumps(
                    {
                        "messages": [
                            {"role": "system", "content": system},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": user_prompt("")},
                                    {"type": "image_url", "image_url": {"url": url, "detail": config.image_detail}},
                                ],
                            },
                            {"role": "assistant", "content": target},
                        ]
                    }
                )
                + "\n"
            )
            n += 1
    return {"path": str(path), "examples": n}


def image_from_png(data):
    from PIL import Image

    return Image.open(io.BytesIO(data))
