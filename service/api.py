"""HTTP API for UI generation. Called by the team's Node backend (BFF), never directly by browsers.

  uv run uvicorn service.api:create_app_from_env --factory --port 8000

Endpoints (all under /v1, all except /v1/health need the X-API-Key header):
  POST /v1/generations                 multipart: image, requirement, framework, config(JSON)  -> 202 + job id
  GET  /v1/generations/{id}            status + result (IR, code, quality, warnings)
  GET  /v1/generations/{id}/events     Server-Sent Events: live progress, then `event: end`
  GET  /v1/generations/{id}/preview    the built page, served inside a CSP sandbox
  GET  /v1/generations/{id}/render.png what the quality check saw
  GET  /v1/generations/{id}/download   zip: IR, React code, preview, quality report
  GET  /v1/meta                        versions, frameworks, components, tunable settings
  GET  /v1/health, /v1/ready           liveness / readiness for load balancers
"""

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
import time
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from uigen import governance, quality
from uigen.build import toolchain_ready
from uigen.config import registry
from uigen.jobs import InMemoryJobStore, QueueFull, ThreadJobRunner
from uigen.pipeline import CACHE_DIR, RUNS_DIR, SUPPORTED_FRAMEWORKS, generate, versions
from uigen.retention import purge_old_runs
from uigen.settings import GenerationConfig

API_VERSION = "v1"
log = logging.getLogger("uigen.api")
JOB_ID = re.compile(r"^job_[0-9a-f]{16}$")
# Callers may tune these; prompts and contracts stay under the service owner's control.
ALLOWED_CONFIG_KEYS = {"image_detail", "max_ir_repairs", "fidelity_repair", "fidelity_threshold"}
# The generated page runs in a sandbox even when opened directly: no same-origin access, no network.
PREVIEW_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-forms; default-src 'none'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, max-age=300",
}
ARTIFACTS = ["ir.json", "react/App.jsx", "react/styles.css", "preview.html", "quality.json", "render.png"]


def _public(res):
    """What the API returns: everything useful, nothing heavy or internal."""
    q = dict(res["quality"] or {})
    q.pop("render_png", None)
    tr = res["trace"]
    return {
        "generation_id": res["generation_id"],
        "status": res["status"],
        "errors": res["errors"],
        "warnings": res["warnings"],
        "ir": res["ir"],
        "files": res["files"],
        "coverage": res["coverage"],
        "unsupported": res["unsupported"],
        "quality": q or None,
        "fidelity_repair": res["fidelity_repair"],
        "config": res["config"],
        "viewport": res["viewport"],
        "usage": {
            "model": tr["model"],
            "input_tokens": tr["input_tokens"],
            "output_tokens": tr["output_tokens"],
            "ir_repairs": tr["repair_count"],
            "total_ms": tr["total_ms"],
            "cache_hit": tr.get("cache_hit"),
        },
        "versions": {k: tr[k] for k in versions()},
    }


def create_app(
    provider_factory,
    api_keys,
    *,
    allow_no_auth=False,
    workers=2,
    max_pending=20,
    runs_dir=RUNS_DIR,
    cache_dir=CACHE_DIR,
    trace_dir=None,
    store=None,
    runner=None,
    external_llm=True,
    expose_docs=True,
):
    if not api_keys and not allow_no_auth:
        raise RuntimeError("no API keys configured (UIGEN_API_KEYS); refusing to start an open service")
    store = store or InMemoryJobStore()
    runner = runner or ThreadJobRunner(workers=workers, max_pending=max_pending)
    runs_dir = Path(runs_dir)
    docs = {} if expose_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(
        title="uigen",
        version=API_VERSION,
        description="Wireframe/screenshot -> UI IR -> framework code, with quality checks.",
        **docs,
    )
    purged = purge_old_runs(runs_dir)
    if purged:
        log.info("retention: deleted %d old generation folders", len(purged))

    def owner(x_api_key: str | None = Header(default=None)):
        for key in api_keys:
            if x_api_key and hmac.compare_digest(key.encode(), x_api_key.encode()):
                return "key_" + hashlib.sha256(key.encode()).hexdigest()[:10]  # never store the key itself
        if not api_keys and allow_no_auth:
            return "anonymous"
        raise HTTPException(401, "missing or invalid X-API-Key")

    def owned(job_id, who):
        job = store.get(job_id) if JOB_ID.match(job_id) else None
        if job is None or job.owner != who:  # same answer for "not yours" and "does not exist"
            raise HTTPException(404, "generation not found")
        return job

    def run_job(job_id, data, requirement, framework, config):
        store.update(job_id, status="running")
        try:
            res = generate(
                data,
                provider_factory(),
                requirement,
                framework,
                on_progress=lambda s, m: store.add_event(job_id, {"stage": s, "message": m}),
                runs_dir=runs_dir,
                cache_dir=cache_dir,
                trace_dir=trace_dir,
                config=config,
            )
            store.update(
                job_id,
                result=_public(res),
                run_dir=str(runs_dir / res["generation_id"]),
                status="done",
                finished_at=time.time(),
            )
        except Exception:  # never leak internals to the caller; full detail goes to the server log
            log.exception("job %s failed", job_id)
            store.update(job_id, status="failed", error="internal error", finished_at=time.time())

    def links(job_id):
        base = f"/{API_VERSION}/generations/{job_id}"
        return {
            "self": base,
            "events": f"{base}/events",
            "preview": f"{base}/preview",
            "render": f"{base}/render.png",
            "download": f"{base}/download",
        }

    @app.post(f"/{API_VERSION}/generations", status_code=202)
    async def create_generation(
        image: UploadFile = File(...),
        requirement: str = Form(""),
        framework: str = Form("react"),
        config: str = Form("{}"),
        who=Depends(owner),
    ):
        data = await image.read(governance.MAX_UPLOAD_BYTES + 1)
        if len(data) > governance.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "image larger than 10 MB")
        if framework not in SUPPORTED_FRAMEWORKS:
            raise HTTPException(400, f"framework must be one of {sorted(SUPPORTED_FRAMEWORKS)}")
        try:
            options = json.loads(config or "{}")
            if not isinstance(options, dict) or set(options) - ALLOWED_CONFIG_KEYS:
                raise ValueError(f"config keys allowed: {sorted(ALLOWED_CONFIG_KEYS)}")
            cfg = GenerationConfig(name="api", **options)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"invalid config: {exc}") from exc
        if external_llm and not governance.external_llm_allowed():
            raise HTTPException(503, "external LLM calls are disabled on this server (ALLOW_EXTERNAL_LLM)")
        try:  # reject bad images now (fast 400) instead of inside the job
            await run_in_threadpool(governance.sanitize_image, data, cfg.max_side_px)
            requirement = governance.check_requirement(requirement)
        except governance.GovernanceError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = store.create(who)
        try:
            runner.submit(run_job, job.id, data, requirement, framework, cfg)
        except QueueFull:
            store.update(job.id, status="failed", error="server busy", finished_at=time.time())
            raise HTTPException(
                429, "too many generations in progress; retry shortly", headers={"Retry-After": "10"}
            ) from None
        return {"id": job.id, "status": job.status, "links": links(job.id)}

    @app.get(f"/{API_VERSION}/generations/{{job_id}}")
    def get_generation(job_id: str, who=Depends(owner)):
        job = owned(job_id, who)
        return {
            "id": job.id,
            "status": job.status,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "result": job.result,
            "links": links(job.id),
        }

    @app.get(f"/{API_VERSION}/generations/{{job_id}}/events")
    async def events(job_id: str, who=Depends(owner)):
        owned(job_id, who)

        async def stream():
            sent, last_ping = 0, time.monotonic()
            while True:
                new, finished = store.events_since(job_id, sent)
                for event in new:
                    yield f"data: {json.dumps(event)}\n\n"
                sent += len(new)
                if finished:
                    yield f"event: end\ndata: {json.dumps({'status': store.get(job_id).status})}\n\n"
                    return
                if time.monotonic() - last_ping > 15:  # keeps proxies from closing an idle stream
                    yield ": ping\n\n"
                    last_ping = time.monotonic()
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    def artifact(job_id, who, name):
        job = owned(job_id, who)
        path = Path(job.run_dir or "") / name
        if job.status != "done" or not job.run_dir or not path.is_file():
            raise HTTPException(404, f"{name} not available")
        return path

    @app.get(f"/{API_VERSION}/generations/{{job_id}}/preview")
    def preview(job_id: str, who=Depends(owner)):
        return FileResponse(artifact(job_id, who, "preview.html"), media_type="text/html", headers=PREVIEW_HEADERS)

    @app.get(f"/{API_VERSION}/generations/{{job_id}}/render.png")
    def render_png(job_id: str, who=Depends(owner)):
        return FileResponse(artifact(job_id, who, "render.png"), media_type="image/png")

    @app.get(f"/{API_VERSION}/generations/{{job_id}}/download")
    def download(job_id: str, who=Depends(owner)):
        job = owned(job_id, who)
        if job.status != "done" or not job.run_dir:
            raise HTTPException(404, "not available")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in ARTIFACTS:
                path = Path(job.run_dir) / name
                if path.is_file():
                    zf.write(path, name)
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{job.id}.zip"'},
        )

    @app.get(f"/{API_VERSION}/meta")
    def meta(who=Depends(owner)):
        return {
            "api_version": API_VERSION,
            "versions": versions(),
            "frameworks": sorted(SUPPORTED_FRAMEWORKS),
            "components": sorted(registry()["components"]),
            "config": {k: GenerationConfig().as_dict()[k] for k in sorted(ALLOWED_CONFIG_KEYS)},
        }

    @app.get(f"/{API_VERSION}/health")
    def health():
        return {"status": "ok"}

    @app.get(f"/{API_VERSION}/ready")
    def ready():
        checks = {
            "build_toolchain": toolchain_ready(),
            "quality_stage": quality.available()[0],
            "external_llm_allowed": (not external_llm) or governance.external_llm_allowed(),
        }
        ok = checks["build_toolchain"] and checks["external_llm_allowed"]
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    app.state.store, app.state.runner = store, runner
    return app


def create_app_from_env():
    """Entry point for uvicorn (--factory). All settings come from the environment / .env."""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    provider = os.getenv("UIGEN_PROVIDER", "openai")
    if provider == "demo":
        from uigen.demo import demo_provider as factory

        external = False
    else:
        from uigen.llm import OpenAIProvider

        OpenAIProvider()  # fail fast on a missing key

        def factory():
            return OpenAIProvider()

        external = True
    keys = [k.strip() for k in os.getenv("UIGEN_API_KEYS", "").split(",") if k.strip()]
    data_dirs = {  # where generations, cache and traces live (e.g. a mounted volume in production)
        "runs_dir": Path(os.getenv("UIGEN_RUNS_DIR", str(RUNS_DIR))),
        "cache_dir": Path(os.getenv("UIGEN_CACHE_DIR", str(CACHE_DIR))),
        "trace_dir": Path(os.environ["UIGEN_TRACE_DIR"]) if os.getenv("UIGEN_TRACE_DIR") else None,
    }
    return create_app(
        factory,
        keys,
        allow_no_auth=os.getenv("UIGEN_ALLOW_NO_AUTH", "false").lower() == "true",
        workers=int(os.getenv("UIGEN_WORKERS", "2")),
        max_pending=int(os.getenv("UIGEN_MAX_PENDING", "20")),
        external_llm=external,
        expose_docs=os.getenv("UIGEN_EXPOSE_DOCS", "false").lower() == "true",
        **data_dirs,
    )
