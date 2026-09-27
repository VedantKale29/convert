"""Gradio UI: upload a wireframe/screenshot, get validated UI IR + React code + live preview.

python app.py            # real OpenAI calls (needs .env)
python app.py --demo     # scripted replies, no key, no network
"""

import argparse
import html
import io
import json
import queue
import threading
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv
from PIL import Image

from uigen.demo import demo_provider
from uigen.llm import OpenAIProvider
from uigen.pipeline import generate, versions
from uigen.settings import GenerationConfig

ROOT = Path(__file__).resolve().parent
STEP_LABELS = {
    "governance": "Input checks",
    "llm": "LLM",
    "repair": "Repair",
    "validate": "Validation",
    "compile": "Compile",
    "build": "Sandbox build",
    "quality": "Quality check",
    "fidelity_repair": "Fidelity repair",
    "result": "Result",
    "done": "Finished",
}


def status_markdown(res):
    tr = res["trace"]
    icon = {"success": "✅", "invalid_ir": "❌", "compiler_error": "🛠️", "rejected_input": "⛔"}.get(res["status"], "•")
    lines = [
        f"### {icon} {res['status'].replace('_', ' ')}",
        f"`{res['generation_id']}` · model `{tr['model']}` · {tr['total_ms']} ms · "
        f"tokens in/out {tr['input_tokens']}/{tr['output_tokens']} · repairs {tr['repair_count']}"
        + (" · cache hit" if tr.get("cache_hit") else ""),
    ]
    if res["coverage"] is not None:
        lines.append(f"**Coverage:** {res['coverage']:.0%} of elements expressed with real components")
    fr = res.get("fidelity_repair")
    if fr:
        if fr["after"] is None:
            lines.append(
                f"**Fidelity repair:** attempted at {fr['before']}, the repaired answer was invalid; "
                "kept the original result"
            )
        else:
            lines.append(f"**Fidelity repair:** {fr['before']} -> {fr['after']} · kept the {fr['kept']} result")
    q = res.get("quality")
    if q and q["fidelity"] is not None:
        lines.append(
            f"**Fidelity vs your image:** {q['fidelity']:.0%} (details in the Quality tab) · "
            f"accessibility issues: {q['a11y_violations']}"
        )
    if res.get("first_attempt_errors"):
        lines.append(
            "**First attempt rejected, repaired once:**\n"
            + "\n".join(f"- {e}" for e in res["first_attempt_errors"][:8])
        )
    if res["errors"]:
        lines.append("**Errors:**\n" + "\n".join(f"- {e}" for e in res["errors"][:12]))
    if res["unsupported"]:
        lines.append(
            "**Not expressible with the component registry:**\n" + "\n".join(f"- {u}" for u in res["unsupported"])
        )
    if res["warnings"]:
        lines.append("**Warnings:**\n" + "\n".join(f"- {w}" for w in res["warnings"]))
    return "\n\n".join(lines)


def quality_markdown(res):
    q = res.get("quality")
    if not q:
        skipped = [w for w in res.get("warnings", []) if w.startswith("quality check")]
        return skipped[0] if skipped else "No quality report for this run."

    def pct(v):
        return "n/a" if v is None else f"{v:.0%}"

    rows = [
        ("**Fidelity (overall)**", pct(q["fidelity"]), "weighted: text 50%, position 25%, visual 25%"),
        ("Text found (recall)", pct(q["text_recall"]), f"{q['ocr_blocks']} text blocks read from your image"),
        ("Text not invented (precision)", pct(q["text_precision"]), f"{q['dom_blocks']} text blocks in the result"),
        ("Position", pct(q["position"]), "matched text in roughly the same place"),
        ("Visual similarity (SSIM)", pct(q["ssim"]), "layout, density, spacing"),
        ("Accessibility issues", str(q["a11y_violations"]), "axe-core, WCAG 2 A/AA"),
    ]
    out = ["| Check | Score | Meaning |", "|---|---|---|"] + [f"| {a} | {b} | {c} |" for a, b, c in rows]
    if q["missing_text"]:
        out.append("\n**In your image but not in the result:**\n" + "\n".join(f"- {t}" for t in q["missing_text"]))
    if q["extra_text"]:
        out.append("\n**In the result but not in your image:**\n" + "\n".join(f"- {t}" for t in q["extra_text"]))
    for v in q["accessibility"]:
        out.append(f"- ♿ **{v['impact']}** {v['help']} (`{v['id']}`, {v['nodes']} element(s))")
    out.append(
        f"\n<small>{q['version']} · rendered at {q['viewport'][0]}×{q['viewport'][1]} px · "
        "scores are reported, not blocking; thresholds are calibrated on the eval set</small>"
    )
    return "\n".join(out)


def _png(data):
    return Image.open(io.BytesIO(data)) if data else None


def preview_frame(page):
    if not page:
        return "<p style='padding:16px;color:#64748b'>No preview (see status).</p>"
    return (
        f'<iframe title="Generated UI preview" sandbox="allow-scripts allow-forms" '
        f'style="width:100%;height:720px;border:1px solid #e2e8f0;border-radius:8px;background:#fff" '
        f'srcdoc="{html.escape(page, quote=True)}"></iframe>'
    )


DEMO_NOTE = (
    "### ⚠️ Demo mode: this is NOT generated from your image\n"
    "The same scripted SupplyView dashboard is shown for every upload, so you can try the interface "
    "without an API key. For real results, stop the app and run `.\\tasks.ps1 run` "
    "(needs `OPENAI_API_KEY` and `ALLOW_EXTERNAL_LLM=true` in `.env`).\n\n---\n"
)


def make_handler(provider_factory, demo=False):
    def run(image_path, requirement, framework, detail, max_repairs, fidelity_repair, threshold):
        empty = ("", "", "", {}, "", None)
        if not image_path:
            yield "Upload a wireframe or screenshot first.", preview_frame(None), *empty
            return
        events, box = queue.Queue(), {}

        def work():
            try:
                box["res"] = generate(
                    Path(image_path).read_bytes(),
                    provider_factory(),
                    requirement,
                    framework.lower(),
                    on_progress=lambda s, m: events.put((s, m)),
                    config=GenerationConfig(
                        name="app",
                        image_detail=detail,
                        max_ir_repairs=int(max_repairs),
                        fidelity_repair=bool(fidelity_repair),
                        fidelity_threshold=float(threshold),
                    ),
                )
            except Exception as exc:  # surface anything unexpected in the UI
                box["error"] = exc
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()
        log = []
        while True:
            item = events.get()
            if item is None:
                break
            step, msg = item
            log.append(f"- **{STEP_LABELS.get(step, step)}:** {msg}")
            yield "\n".join(log), preview_frame(None), *empty
        if "error" in box:
            yield f"### ❌ error\n{box['error']}", preview_frame(None), *empty
            return
        res = box["res"]
        yield (
            (DEMO_NOTE if demo else "") + status_markdown(res),
            preview_frame(res["preview_html"]),
            json.dumps(res["ir"], indent=2) if res["ir"] else "",
            res["files"].get("App.jsx", ""),
            res["files"].get("styles.css", ""),
            res["trace"],
            quality_markdown(res),
            _png((res.get("quality") or {}).get("render_png")),
        )

    return run


def build_ui(provider_factory, demo=False):
    v = versions()
    with gr.Blocks(title="Wireframe to UI") as ui:
        gr.Markdown(
            "## Wireframe → UI IR → React\n"
            + ("**DEMO MODE:** scripted replies, the upload is not sent anywhere.\n\n" if demo else "")
            + "Images go to the configured external LLM. **Use mock screens with dummy data only** "
            "until an endpoint is approved for client data.  \n"
            f"<small>{' · '.join(f'{k}: {val}' for k, val in v.items())}</small>"
        )
        with gr.Row():
            with gr.Column(scale=1):
                image = gr.Image(type="filepath", label="Wireframe or screenshot", sources=["upload", "clipboard"])
                requirement = gr.Textbox(
                    label="Requirement (optional)",
                    lines=3,
                    placeholder="e.g. This is the supplier overview page for planners",
                )
                framework = gr.Dropdown(
                    ["React"],
                    value="React",
                    label="Target framework",
                    info="Angular and Vue are added on demand (same IR, new compiler).",
                )
                with gr.Accordion("Advanced settings", open=False):
                    detail = gr.Radio(
                        ["high", "low", "auto"],
                        value="high",
                        label="Image detail sent to the model",
                        info="low = fewer tokens, less detail",
                    )
                    max_repairs = gr.Slider(0, 2, value=1, step=1, label="Max validation repairs")
                    fidelity_repair = gr.Checkbox(
                        value=False,
                        label="Fidelity repair",
                        info="One extra call if the render does not match the image; kept only if it scores higher",
                    )
                    threshold = gr.Slider(0.3, 0.95, value=0.6, step=0.05, label="Fidelity repair threshold")
                go = gr.Button("Generate", variant="primary")
                status = gr.Markdown("Upload an image and press Generate.")
            with gr.Column(scale=2):
                with gr.Tabs():
                    with gr.Tab("Preview"):
                        preview = gr.HTML(preview_frame(None))
                    with gr.Tab("UI IR (JSON)"):
                        ir = gr.Code(language="json", label="ui-ir-v1")
                    with gr.Tab("App.jsx"):
                        jsx = gr.Code(language="javascript", label="App.jsx")
                    with gr.Tab("styles.css"):
                        css = gr.Code(language="css", label="styles.css (from design tokens)")
                    with gr.Tab("Quality"):
                        gr.Markdown("Your image (left) is compared with the rendered result (right).")
                        with gr.Row():
                            quality_input = gr.Image(label="Your image", interactive=False)
                            quality_render = gr.Image(label="Rendered result", interactive=False)
                        quality_report = gr.Markdown("Generate something to see the quality report.")
                    with gr.Tab("Trace"):
                        trace = gr.JSON(label="Generation trace")
        go.click(
            make_handler(provider_factory, demo),
            [image, requirement, framework, detail, max_repairs, fidelity_repair, threshold],
            [status, preview, ir, jsx, css, trace, quality_report, quality_render],
        )
        image.change(lambda path: path, image, quality_input)
    return ui


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    if args.demo:
        factory = demo_provider
    else:
        OpenAIProvider()  # fail fast if the key is missing
        factory = OpenAIProvider
    build_ui(factory, demo=args.demo).launch(server_name="127.0.0.1", server_port=args.port)


if __name__ == "__main__":
    main()
