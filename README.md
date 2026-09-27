# Wireframe → UI IR → React  (Phases 0, 1, 2)

See **ARCHITECTURE.md** (tiers, scaling path, contracts) and **INTEGRATION.md** (React + Node).

A screenshot/wireframe (plus an optional requirement) becomes a validated **UI IR**, which a
**deterministic compiler** turns into React. The LLM decides *what* is on screen; code decides
*how* it looks and proves that it builds.

```
image + requirement
  → governance (flag, re-encode, strip metadata, size limits)
  → cache lookup (image + requirement + model + all versions)
  → OpenAI: ONE structured-output call (strict JSON Schema from the component registry)
  → validators (registry, tokens, structure, accessibility)
      └ fail → ONE repair call with the exact errors → still failing = invalid_ir (stop)
  → deterministic React compiler (all CSS from design tokens)
  → sandbox build (pinned esbuild + React, import allowlist, timeout)
      └ fail = compiler_error (our bug; never sent to the LLM)
  → self-contained preview (no CDN, no network) + trace
  → quality check (Phase 2): render in headless Chromium at the input size and compare with the image
```

## Setup (Windows, PowerShell)
Needs [uv](https://docs.astral.sh/uv/) and Node.js 18+ (`build_workspace/.nvmrc` pins 20).
```powershell
.\tasks.ps1 setup      # uv sync (creates .venv, installs from uv.lock) + npm ci + creates .env
# edit .env: add OPENAI_API_KEY; set ALLOW_EXTERNAL_LLM=true only for mock screens / approved data
.\tasks.ps1 check      # lint + 41 tests
```
If PowerShell blocks the script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Without uv: `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -r requirements.txt -r requirements-dev.txt`
(then `cd build_workspace; npm ci`).

## Run
```powershell
.\tasks.ps1 demo       # scripted replies, no key, nothing sent anywhere
.\tasks.ps1 run        # real calls, model from .env (default gpt-4.1-mini)
.\tasks.ps1 eval       # score the eval set -> eval/report.json
.\tasks.ps1            # list all tasks (test, lint, format, audit, reqs, snapshots, clean)
```
Open http://127.0.0.1:7860, upload or paste a screenshot, press **Generate**.
Tabs: Preview · UI IR (JSON) · App.jsx · styles.css · Trace.

## Dependencies
| File | Role |
|---|---|
| `pyproject.toml` | Declared dependencies (runtime + `dev` group), pytest and ruff config |
| `uv.lock` | Exact versions + hashes for every platform. **Source of truth; commit it.** |
| `requirements.txt` / `-dev` / `-notebook` | Exported from `uv.lock` for pip users. Regenerate with `.\tasks.ps1 reqs`, never edit by hand |
| `.python-version` | 3.12 (tested on 3.10 and 3.12; minimum 3.10) |
| `build_workspace/package.json` + `package-lock.json` | Pinned build toolchain (React 18.3.1, esbuild 0.28.2) |

Adding a package: `uv add <pkg>` (or `uv add --group dev <pkg>`), then `.\tasks.ps1 reqs`.

## Contracts (Phase 0) — owned by the team, versioned, traced
| File | Owner | What it controls |
|---|---|---|
| `config/registry.json` | team | The component vocabulary: kinds, variants, props, text and a11y rules. **Draft - review it.** |
| `config/design_tokens.json` | UI teammate | Every size, colour, radius. Hard rules: the LLM cannot set these. **Placeholder values.** |
| `uigen/ir.py` | us | UI IR models + strict JSON Schema generated from the registry |
| `uigen/prompts.py` | us | Versioned prompt (`PROMPT_VERSION`); soft rules live here |
| `eval/cases/` | team | Wireframe + hand-checked expected IR. 3 seed cases; grow to 30-50 real ones |

Changing any contract changes its version, which is recorded on every trace and part of the cache key.

## Statuses
`success` · `invalid_ir` (LLM could not produce a valid IR after one repair) ·
`compiler_error` (our compiler emitted code that does not build: fix the compiler) ·
`rejected_input` (governance: flag off, not an image, too large...).

## Governance built in
- `ALLOW_EXTERNAL_LLM` must be `true` for any external call (default off).
- Images are verified, re-encoded to PNG (drops EXIF/GPS/device metadata) and capped at 2048 px.
- Text in the image and the requirement are treated as data; the requirement is quoted in the prompt.
- The LLM never writes code. All IR strings are emitted as escaped JS expressions (`<`, `>`, `&`
  escaped), so no IR text can become markup or script. Output is also scanned for URLs/network/eval.
- Builds run with a pinned toolchain, an import allowlist and a timeout (process-level isolation;
  containerised builds come in Phase 4).
- Every generation writes a trace: input hash, all versions, per-step timings, tokens, repairs, status.

## Experiments notebook (tuning)
`notebooks/experiments.ipynb` runs the same images with different **`GenerationConfig`s** and compares
the numbers. No product code changes needed.

```powershell
.\tasks.ps1 notebook        # installs Jupyter extras and opens JupyterLab
```
**VS Code:** `uv sync --group notebook`, open the notebook, pick the `.venv` Python as the kernel.

Sections: one inspected run · prompt variants · image detail/size (cost vs quality) · fidelity repair
on/off · model comparison · charts · calibrate the fidelity score with your own ratings · edit and
reload the registry/tokens · export a fine-tuning dataset · save results as CSV.
Start with `MODE = "scripted"` (offline, replays the expected answers) to learn it, then switch to
`MODE = "openai"`. Real runs have a call budget (`max_calls`) so an experiment cannot run away with cost.

Everything tunable lives in `uigen/settings.py`:

| Setting | Default | Effect |
|---|---|---|
| `image_detail` | `high` | `low` sends fewer image tokens |
| `max_side_px` | 2048 | images are downscaled before sending |
| `temperature` | provider default | sampling randomness |
| `soft_rules`, `extra_instructions` | 3 rules, none | prompt guidance (hard rules stay in code) |
| `max_ir_repairs` | 1 | validation repairs, 0-2 |
| `fidelity_repair`, `fidelity_threshold` | off, 0.6 | one extra call when the render does not match; kept only if better |

The config is stored on every trace and in the cache key (settings that do not change the model's
output, like the name, are excluded). The app exposes the main ones under **Advanced settings**.

## Quality check (Phase 2)
After a successful build the result is rendered in headless Chromium **at the size of your image** and compared:

| Metric | How | Why |
|---|---|---|
| Text recall | OCR your image (offline, RapidOCR); each text block must exist in the rendered page | Catches missing content |
| Text precision | Rendered text must exist in your image | Catches invented content |
| Position | Matched text in roughly the same place (normalised centre distance) | Catches wrong layout |
| SSIM | Structural similarity of the two images | Coarse layout/density check |
| Accessibility | axe-core, WCAG 2 A/AA rules, on the rendered page | Labels, contrast, alt text... |
| **Fidelity** | 50% text F1 + 25% position + 25% SSIM | One number to track |

Scores are **reported, not blocking**, and saved as `runs/<id>/quality.json` + `render.png`.
Calibration so far: a *perfect* IR scores ~0.85 on the seed cases (hand-drawn images never match a
compiled page pixel-for-pixel); a deliberately wrong IR scores ~0.15. Set blocking thresholds or a
fidelity-driven repair only after measuring real runs on the eval set.
Needs Chromium once: `uv run playwright install chromium` (done by `.\tasks.ps1 setup`). If it is
missing, generation still succeeds and the status shows "quality check skipped".

## Evaluation metrics (eval/run_eval.py)
success rate, first-call valid rate, repair rate, structure F1 (types + nesting + order),
type F1, text recall, coverage (share of non-Placeholder elements), fidelity, OCR text recall,
SSIM, accessibility violations, latency, tokens.

## Phase 0 sign-offs to get (not code)
1. Approved LLM endpoint + data classification for real screenshots (until then: mock screens only).
2. Registry v1 reviewed by the team (which components, which variants).
3. Real design tokens from the UI teammate.
4. 30-50 real wireframes with expected IR, agreed with a domain expert.

## Not built yet (next)
- Angular/Vue compilers (same IR), export to the team's rule JSON.
- Langfuse/OpenTelemetry exporters (the trace already has the right shape); queue/workers/S3/Postgres.
