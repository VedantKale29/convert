# Task runner for Windows PowerShell.   Usage:  .\tasks.ps1 <task>
# If scripts are blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
param([Parameter(Position = 0)][string]$Task = "help")
$ErrorActionPreference = "Stop"

function Invoke-Step([string]$Command) {
    Write-Host "> $Command" -ForegroundColor Cyan
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) { throw "failed: $Command" }
}

switch ($Task) {
    "setup" {
        Invoke-Step "uv sync"
        Invoke-Step "uv run playwright install chromium"
        Push-Location build_workspace
        try { Invoke-Step "npm.cmd ci" } finally { Pop-Location }
        Push-Location integrations/node-bff
        try { Invoke-Step "npm.cmd ci" } finally { Pop-Location }
        if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - add your key." }
    }
    "test"     { Invoke-Step "uv run pytest" }
    "lint"     { Invoke-Step "uv run ruff check ."; Invoke-Step "uv run ruff format --check ." }
    "format"   { Invoke-Step "uv run ruff format ."; Invoke-Step "uv run ruff check . --fix" }
    "demo"     { Invoke-Step "uv run python app.py --demo" }
    "run"      { Invoke-Step "uv run python app.py" }
    "eval"     { Invoke-Step "uv run python eval/run_eval.py" }
    "api"      { Invoke-Step "uv run uvicorn service.api:create_app_from_env --factory --port 8000" }
    "bff" {
        Push-Location integrations/node-bff
        try { Invoke-Step "node server.js" } finally { Pop-Location }
    }
    "openapi"  { Invoke-Step "uv run python tools/export_openapi.py" }
    "notebook" {
        Invoke-Step "uv sync --group notebook"
        Invoke-Step "uv run jupyter lab notebooks/experiments.ipynb"
    }
    "snapshots"{ Invoke-Step "uv run python tests/update_snapshots.py" }
    "reqs" {
        Invoke-Step "uv lock"
        Invoke-Step "uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt"
        Invoke-Step "uv export --format requirements-txt --no-hashes --only-group dev --no-emit-project -o requirements-dev.txt"
        Invoke-Step "uv export --format requirements-txt --no-hashes --only-group notebook --no-emit-project -o requirements-notebook.txt"
    }
    "audit" {
        Invoke-Step "uvx pip-audit -r requirements.txt --disable-pip --no-deps --progress-spinner off"
        Push-Location build_workspace
        try { Invoke-Step "npm.cmd audit" } finally { Pop-Location }
        Push-Location integrations/node-bff
        try { Invoke-Step "npm.cmd audit" } finally { Pop-Location }
    }
    "check"    { & $PSCommandPath lint; & $PSCommandPath test }
    "clean" {
        foreach ($p in @("runs", "cache", "traces", ".pytest_cache", ".ruff_cache", "eval/report.json")) {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Host "removed $p" }
        }
    }
    default {
        Write-Host @"
Tasks:
  setup      uv sync + Chromium + npm ci (build sandbox, Node BFF) + create .env
  test       run the test suite
  lint       ruff lint + format check
  format     auto-format and fix lint
  demo       Gradio app with scripted replies (no key, no network)
  run        Gradio app with real OpenAI calls
  eval       score the eval set -> eval/report.json
  api        HTTP API service on :8000 (for the Node BFF)
  bff        Node BFF on :3001 (reads integrations/node-bff env vars)
  openapi    re-export contracts/openapi.json after API changes
  notebook   install notebook extras and open notebooks/experiments.ipynb in JupyterLab
  snapshots  regenerate compiler snapshots (review the diff!)
  reqs       re-lock and re-export requirements*.txt
  audit      known-vulnerability scan (Python + npm)
  check      lint + test
  clean      delete runs, cache, traces, tool caches
"@
    }
}
