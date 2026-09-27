"""generate(): image (+ requirement) -> validated UI IR -> React -> sandbox build -> preview -> quality.

LLM calls are bounded by the GenerationConfig:
  1 generate call                      (always, unless the cache hits)
  + up to max_ir_repairs repair calls  (only for validation errors in the IR)
  + 1 fidelity repair call             (only if enabled AND the render does not match the image;
                                        the new result is kept ONLY if its fidelity is higher)
Build failures are never sent to the LLM: the compiler is deterministic, so a failed build is a
compiler bug in our code (status 'compiler_error').
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

from pydantic import ValidationError

from . import governance, quality
from .build import build, preview_html
from .compile_react import COMPILER_VERSION, compile_react
from .config import registry, tokens
from .ir import IR_VERSION, UIDocument, strict_json_schema
from .llm import LLMError
from .prompts import PROMPT_VERSION, fidelity_repair_prompt, repair_prompt, system_prompt, user_prompt
from .settings import GenerationConfig
from .tracing import Trace
from .validators import coverage, validate

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
CACHE_DIR = ROOT / "cache"
SUPPORTED_FRAMEWORKS = {"react"}  # angular / vue: same IR, new compiler (on demand)
LOW_FIDELITY_WARNING = 0.6


def versions():
    return {
        "ir_version": IR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "registry_version": registry()["version"],
        "design_system_version": tokens()["version"],
        "compiler_version": COMPILER_VERSION,
    }


def _parse(raw):
    """Raw LLM text -> (UIDocument or None, errors)."""
    try:
        return UIDocument.model_validate_json(raw), []
    except ValidationError as exc:
        return None, [f"schema: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]]


def _cache_key(png, requirement, model, config):
    h = hashlib.sha256(png)
    for part in (requirement, model, json.dumps(versions(), sort_keys=True), config.fingerprint()):
        h.update(part.encode())
    return h.hexdigest()


class _Run:
    """State of one generation. Keeps generate() readable."""

    def __init__(self, provider, config, trace, progress, run_dir, requirement):
        self.provider, self.config, self.trace, self.progress = provider, config, trace, progress
        self.run_dir, self.requirement = run_dir, requirement
        self.schema = strict_json_schema()
        self.system = system_prompt(config.soft_rules, config.extra_instructions)

    def call(self, purpose, user_text, png):
        with self.trace.span(f"llm_{purpose}"):
            raw, usage = self.provider.generate_json(
                self.system,
                user_text,
                png,
                self.schema,
                "ui_document",
                detail=self.config.image_detail,
                temperature=self.config.temperature,
            )
            self.trace.llm_call(purpose, self.provider, usage)
        return raw

    def validated(self, raw, attempt):
        doc, errors = _parse(raw)
        if doc:
            with self.trace.span("validate", attempt=attempt) as s:
                errors, _ = validate(doc)
                s["errors"] = len(errors)
        return doc, errors

    def compile_and_build(self, doc, label):
        with self.trace.span("compile", attempt=label):
            files = compile_react(doc)
        with self.trace.span("build", attempt=label) as s:
            built = build(files, self.run_dir / f"react_{label}")
            s["ok"] = built["ok"]
        return files, built

    def check_quality(self, png, page_html, label):
        """Returns the quality report, or (None, reason) when the stage cannot run."""
        ok, reason = quality.available()
        if not ok:
            return None, reason
        page = self.run_dir / f"preview_{label}.html"
        page.write_text(page_html, encoding="utf-8")
        with self.trace.span("quality", attempt=label) as s:
            try:
                report = quality.check(png, page)
            except Exception as exc:  # e.g. browser not installed: never lose the generation over it
                s["status"] = "skipped"
                return None, f"quality check failed: {str(exc).splitlines()[0][:200]}"
            s.update({k: report[k] for k in ("fidelity", "text_recall", "text_precision", "position", "ssim")})
        return report, None


def generate(
    image_bytes,
    provider,
    requirement="",
    framework="react",
    on_progress=None,
    runs_dir=RUNS_DIR,
    cache_dir=CACHE_DIR,
    trace_dir=None,
    use_cache=True,
    config=None,
    run_quality=None,
):
    config = config or GenerationConfig()
    if run_quality is not None:  # kept for backwards compatibility
        config = config.with_(run_quality=run_quality)
    progress = on_progress or (lambda step, msg: None)
    if framework not in SUPPORTED_FRAMEWORKS:
        raise NotImplementedError(f"'{framework}' is not built yet; available: {sorted(SUPPORTED_FRAMEWORKS)}")
    trace = Trace(framework=framework, provider=provider.name, model=provider.model, **versions())
    trace.data["config"] = config.as_dict()
    result = {
        "generation_id": trace.generation_id,
        "status": None,
        "ir": None,
        "errors": [],
        "warnings": [],
        "files": {},
        "preview_html": None,
        "unsupported": [],
        "coverage": None,
        "quality": None,
        "fidelity_repair": None,
        "config": config.as_dict(),
    }
    run_dir = Path(runs_dir) / trace.generation_id

    def finish(status):
        result["status"] = status
        result["trace"] = trace.finish(
            status, trace_dir=trace_dir, coverage=result["coverage"], error_count=len(result["errors"])
        )
        progress("done", status)
        return result

    # 1. Governance on the input
    with trace.span("governance") as s:
        try:
            if not governance.external_llm_allowed() and provider.name != "scripted":
                raise governance.GovernanceError(
                    "external LLM calls are disabled (ALLOW_EXTERNAL_LLM is not 'true'); "
                    "confirm the data classification first"
                )
            png, info = governance.sanitize_image(image_bytes, config.max_side_px)
            requirement = governance.check_requirement(requirement)
            s.update(info)
            trace.data["input"] = {"image_sha256": info["original_sha256"], "requirement_chars": len(requirement)}
        except governance.GovernanceError as exc:
            result["errors"] = [str(exc)]
            s["status"] = "rejected"
            return finish("rejected_input")
    progress("governance", "input accepted")
    run_dir.mkdir(parents=True, exist_ok=True)
    run = _Run(provider, config, trace, progress, run_dir, requirement)

    # 2. Cache: same image + requirement + model + versions + config -> same IR
    cache_file = Path(cache_dir) / f"{_cache_key(png, requirement, provider.model, config)}.json"
    trace.data["cache_hit"] = bool(use_cache and cache_file.exists())
    if trace.data["cache_hit"]:
        doc = UIDocument.model_validate_json(cache_file.read_text(encoding="utf-8"))
        progress("llm", "cache hit: reusing a validated IR")
    else:
        # 3. One generate call + bounded IR repairs
        progress("llm", "reading the screenshot")
        try:
            raw = run.call("generate", user_prompt(requirement), png)
        except LLMError as exc:
            result["errors"] = [str(exc)]
            return finish("llm_error")
        doc, errors = run.validated(raw, 1)
        for attempt in range(2, config.max_ir_repairs + 2):
            if not errors:
                break
            progress("repair", f"{len(errors)} validation error(s), repair {attempt - 1} of {config.max_ir_repairs}")
            result.setdefault("first_attempt_errors", errors)
            try:
                raw = run.call("repair", repair_prompt(requirement, raw, errors), png)
            except LLMError as exc:
                result["errors"] = [f"repair call failed: {exc}", *errors]
                return finish("llm_error")
            doc, errors = run.validated(raw, attempt)
        if errors:
            result["errors"] = errors
            (run_dir / "rejected_ir.json").write_text(raw, encoding="utf-8")
            return finish("invalid_ir")

    # 4. Compile + build
    files, built = run.compile_and_build(doc, "1")
    if not built["ok"]:
        result["errors"] = built["errors"]
        return finish("compiler_error")
    page = preview_html(built["js"], built["css"], doc.title)
    progress("build", "build passed")

    # 5. Quality: compare the render with the image
    report = None
    if config.run_quality:
        progress("quality", "rendering and comparing with your image")
        report, skipped = run.check_quality(png, page, "1")
        if skipped:
            result["warnings"].append(skipped)

    # 6. Optional fidelity repair: one call with the located differences; keep the better result
    if (
        config.fidelity_repair
        and report
        and report["fidelity"] is not None
        and report["fidelity"] < config.fidelity_threshold
    ):
        progress("fidelity_repair", f"fidelity {report['fidelity']} < {config.fidelity_threshold}, one repair call")
        outcome = {"before": report["fidelity"], "after": None, "kept": "original"}
        try:
            raw2 = run.call("fidelity_repair", fidelity_repair_prompt(requirement, doc.model_dump_json(), report), png)
            doc2, errors2 = run.validated(raw2, "fidelity")
        except LLMError as exc:  # a failed optional call never costs us the original result
            doc2, errors2 = None, [f"fidelity repair call failed: {exc}"]
        if errors2:
            outcome["rejected"] = errors2[:10]
        else:
            files2, built2 = run.compile_and_build(doc2, "2")
            if built2["ok"]:
                page2 = preview_html(built2["js"], built2["css"], doc2.title)
                report2, _ = run.check_quality(png, page2, "2")
                if report2 and report2["fidelity"] is not None:
                    outcome["after"] = report2["fidelity"]
                    if report2["fidelity"] > report["fidelity"]:
                        doc, files, page, report = doc2, files2, page2, report2
                        outcome["kept"] = "repaired"
        result["fidelity_repair"] = outcome
        trace.data["fidelity_repair"] = outcome

    # 7. Final artifacts for the kept result
    if use_cache:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(f".{trace.generation_id}.tmp")  # atomic: readers never see half a file
        tmp.write_text(doc.model_dump_json(), encoding="utf-8")
        os.replace(tmp, cache_file)
    result["ir"] = json.loads(doc.model_dump_json())
    result["unsupported"] = doc.unsupported
    result["coverage"], placeholders = coverage(doc)
    result["warnings"] = validate(doc)[1] + result["warnings"]
    result["files"] = files
    result["preview_html"] = page
    (run_dir / "ir.json").write_text(json.dumps(result["ir"], indent=2), encoding="utf-8")
    (run_dir / "preview.html").write_text(page, encoding="utf-8")
    shutil.copytree(
        run_dir / f"react_{'2' if (result['fidelity_repair'] or {}).get('kept') == 'repaired' else '1'}",
        run_dir / "react",
        dirs_exist_ok=True,
    )
    progress("result", f"coverage {result['coverage']:.0%} ({placeholders} placeholder(s))")
    if report:
        shot = report.pop("render_png")
        (run_dir / "render.png").write_bytes(shot)
        (run_dir / "quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        result["quality"] = {**report, "render_png": shot}
        trace.data["quality"] = {
            k: report[k] for k in ("fidelity", "text_recall", "text_precision", "position", "ssim", "a11y_violations")
        }
        _quality_warnings(result, report)
        progress("quality", f"fidelity {report['fidelity']}")
    return finish("success")


def _quality_warnings(result, report):
    if report["fidelity"] is not None and report["fidelity"] < LOW_FIDELITY_WARNING:
        result["warnings"].append(f"low fidelity ({report['fidelity']}): the result may not match your image")
    for text in report["missing_text"][:10]:
        result["warnings"].append(f"text in the image not found in the result: '{text}'")
    for v in report["accessibility"]:
        result["warnings"].append(f"accessibility ({v['impact']}): {v['help']} [{v['id']}, {v['nodes']} element(s)]")
