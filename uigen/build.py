"""Sandbox build of generated code + self-contained preview.

Runs the pinned toolchain in build_workspace/ in a per-job directory with a timeout and an
import allowlist. The preview inlines the bundle (React included): no CDN, no network.
NOTE: this is process-level isolation. Production should run builds in a container with
CPU/memory/network limits (see README, Phase 4).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent / "build_workspace"
BUILD_TIMEOUT_S = 60

# Defence in depth: the compiler never emits these, so finding one means a compiler bug.
FORBIDDEN_OUTPUT = {
    r"dangerouslySetInnerHTML": "dangerouslySetInnerHTML in generated code",
    r"\beval\s*\(": "eval() in generated code",
    r"https?://": "external URL in generated code",
    r"\bfetch\s*\(|XMLHttpRequest": "network call in generated code",
}


def toolchain_ready():
    return shutil.which("node") is not None and (WORKSPACE / "node_modules" / "esbuild").exists()


def security_scan(files):
    return [
        f"{name}: {msg}"
        for name, text in files.items()
        for pattern, msg in FORBIDDEN_OUTPUT.items()
        if re.search(pattern, text)
    ]


def build(files, job_dir):
    """Write files into job_dir and bundle them. Returns dict(ok, errors, js, css)."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (job_dir / name).write_text(text, encoding="utf-8")
    if not toolchain_ready():
        return {"ok": False, "errors": ["build toolchain missing: run 'npm install' in build_workspace/"]}
    scan = security_scan(files)
    if scan:
        return {"ok": False, "errors": scan}
    try:
        proc = subprocess.run(
            ["node", str(WORKSPACE / "build.js"), str(job_dir)],
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
            cwd=WORKSPACE,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": [f"build timed out after {BUILD_TIMEOUT_S}s"]}
    try:
        result = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        result = {"ok": False, "errors": [proc.stderr.strip()[:1000] or "build produced no result"]}
    if result.get("ok"):
        dist = job_dir / "dist"
        result["js"] = (dist / "entry.js").read_text(encoding="utf-8")
        result["css"] = (dist / "entry.css").read_text(encoding="utf-8") if (dist / "entry.css").exists() else ""
    result.setdefault("errors", [])
    return result


def preview_html(bundle_js, bundle_css, title="Preview"):
    """Standalone page with the built app inlined."""
    safe_js = bundle_js.replace("</script", "<\\/script")
    safe_css = bundle_css.replace("</style", "<\\/style")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>'
        f'<style>body{{margin:0}}{safe_css}</style></head><body><div id="root"></div>'
        f"<script>{safe_js}</script></body></html>"
    )
