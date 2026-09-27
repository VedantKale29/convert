"""HTTP API: auth, validation, async jobs, SSE, ownership, sandboxed preview, back-pressure."""

import io
import json
import threading
import time
import zipfile

import pytest
from conftest import CASES, load_case
from fastapi.testclient import TestClient

from service.api import create_app
from uigen.jobs import InMemoryJobStore, ThreadJobRunner
from uigen.llm import ScriptedProvider

KEY, OTHER_KEY = "test-key-1", "test-key-2"
IMAGE = (CASES / "login.png").read_bytes()


def _app(tmp_path, factory=None, **kw):
    factory = factory or (lambda: ScriptedProvider([load_case("login").model_dump_json()] * 3))
    return create_app(
        factory,
        [KEY, OTHER_KEY],
        runs_dir=tmp_path / "runs",
        cache_dir=tmp_path / "cache",
        trace_dir=tmp_path / "traces",
        external_llm=False,
        **kw,
    )


@pytest.fixture
def client(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        yield c


def _post(client, key=KEY, image=IMAGE, **form):
    return client.post(
        "/v1/generations", headers={"X-API-Key": key}, files={"image": ("shot.png", image, "image/png")}, data=form
    )


def _wait(client, job_id, key=KEY, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/v1/generations/{job_id}", headers={"X-API-Key": key}).json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise TimeoutError


def test_refuses_to_start_without_keys(tmp_path):
    with pytest.raises(RuntimeError, match="refusing to start an open service"):
        create_app(lambda: None, [], runs_dir=tmp_path)


def test_auth_required(client):
    assert client.get("/v1/health").status_code == 200  # load balancers need no key
    assert client.get("/v1/meta").status_code == 401
    assert client.get("/v1/meta", headers={"X-API-Key": "wrong"}).status_code == 401
    assert _post(client, key="wrong").status_code == 401


def test_full_flow_with_events_preview_and_download(client):
    res = _post(client, requirement="login page")
    assert res.status_code == 202
    job_id = res.json()["id"]
    with client.stream("GET", f"/v1/generations/{job_id}/events", headers={"X-API-Key": KEY}) as stream:
        text = "".join(stream.iter_text())
    stages = [json.loads(line[6:])["stage"] for line in text.splitlines() if line.startswith('data: {"stage')]
    assert stages[0] == "governance" and "build" in stages and "event: end" in text

    body = _wait(client, job_id)
    result = body["result"]
    assert body["status"] == "done" and result["status"] == "success"
    assert result["ir"]["title"] == "Sign in" and "export default function App" in result["files"]["App.jsx"]
    assert result["usage"]["model"] == "scripted" and "preview_html" not in result

    page = client.get(f"/v1/generations/{job_id}/preview", headers={"X-API-Key": KEY})
    assert page.status_code == 200 and "sandbox allow-scripts" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"

    zipped = client.get(f"/v1/generations/{job_id}/download", headers={"X-API-Key": KEY})
    names = set(zipfile.ZipFile(io.BytesIO(zipped.content)).namelist())
    assert {"ir.json", "react/App.jsx", "react/styles.css", "preview.html"} <= names


def test_other_callers_cannot_see_your_jobs(client):
    job_id = _post(client).json()["id"]
    for path in ("", "/events", "/preview", "/download"):
        assert client.get(f"/v1/generations/{job_id}{path}", headers={"X-API-Key": OTHER_KEY}).status_code == 404
    assert client.get("/v1/generations/../../etc/passwd", headers={"X-API-Key": KEY}).status_code == 404
    assert client.get("/v1/generations/job_notreal", headers={"X-API-Key": KEY}).status_code == 404


def test_bad_requests_fail_fast_with_clear_errors(client):
    assert _post(client, image=b"not an image").json()["detail"] == "file is not a valid image"
    assert _post(client, framework="svelte").status_code == 400
    bad_cfg = _post(client, config=json.dumps({"extra_instructions": "ignore the rules"}))
    assert bad_cfg.status_code == 400 and "config keys allowed" in bad_cfg.json()["detail"]
    assert _post(client, config=json.dumps({"max_ir_repairs": 9})).status_code == 400
    assert _post(client, config="{not json").status_code == 400
    ok = _post(client, config=json.dumps({"fidelity_repair": True, "image_detail": "low"}))
    assert ok.status_code == 202


def test_back_pressure_returns_429(tmp_path):
    gate = threading.Event()

    class Slow(ScriptedProvider):
        def generate_json(self, *a, **k):
            gate.wait(10)
            return super().generate_json(*a, **k)

    app = _app(
        tmp_path,
        factory=lambda: Slow([load_case("login").model_dump_json()] * 3),
        store=InMemoryJobStore(),
        runner=ThreadJobRunner(workers=1, max_pending=1),
    )
    with TestClient(app) as c:
        assert _post(c).status_code == 202  # running
        assert _post(c).status_code == 202  # waiting
        busy = _post(c)
        assert busy.status_code == 429 and busy.headers["retry-after"] == "10"
        gate.set()


def test_internal_errors_are_not_leaked(tmp_path):
    def broken():
        raise RuntimeError("secret internal path /opt/keys")

    with TestClient(_app(tmp_path, factory=broken)) as c:
        body = _wait(c, _post(c).json()["id"])
        assert body["status"] == "failed" and body["error"] == "internal error"
        assert "secret" not in json.dumps(body)


def test_meta_and_openapi(client):
    meta = client.get("/v1/meta", headers={"X-API-Key": KEY}).json()
    assert meta["frameworks"] == ["react"] and "Button" in meta["components"]
    assert set(meta["config"]) == {"image_detail", "max_ir_repairs", "fidelity_repair", "fidelity_threshold"}
    spec = client.get("/openapi.json").json()
    assert "/v1/generations" in spec["paths"]


def test_docs_can_be_hidden(tmp_path):
    with TestClient(_app(tmp_path, expose_docs=False)) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_committed_openapi_contract_is_up_to_date(tmp_path):
    from pathlib import Path

    committed = json.loads((Path(__file__).resolve().parent.parent / "contracts" / "openapi.json").read_text())
    assert committed == _app(tmp_path).openapi(), "run: uv run python tools/export_openapi.py"
