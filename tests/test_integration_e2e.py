"""Full stack: React (Chromium) -> Node BFF -> Python API. Skipped if Node, the BFF packages or Chromium are missing."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
BFF = ROOT / "integrations" / "node-bff"
DEMO = ROOT / "integrations" / "react" / "demo"
IMAGE = ROOT / "eval" / "cases" / "login.png"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise TimeoutError(url)


pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (BFF / "node_modules" / "express").exists(),
    reason="Node BFF not installed: cd integrations/node-bff && npm ci",
)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    api_port, bff_port = _free_port(), _free_port()
    runs = tmp_path_factory.mktemp("runs")
    env = {
        **os.environ,
        "UIGEN_PROVIDER": "demo",
        "UIGEN_API_KEYS": "e2e-key",
        "UIGEN_RUNS_DIR": str(runs / "runs"),
        "UIGEN_CACHE_DIR": str(runs / "cache"),
        "UIGEN_TRACE_DIR": str(runs / "traces"),
    }
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "service.api:create_app_from_env",
            "--factory",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    subprocess.run(["node", str(DEMO / "build.mjs")], check=True, capture_output=True)
    bff = subprocess.Popen(
        ["node", "server.js"],
        cwd=BFF,
        stdout=subprocess.DEVNULL,
        env={
            **os.environ,
            "UIGEN_URL": f"http://127.0.0.1:{api_port}",
            "UIGEN_API_KEY": "e2e-key",
            "PORT": str(bff_port),
            "STATIC_DIR": str(DEMO),
            "RATE_LIMIT_PER_MINUTE": "4",
        },
    )
    try:
        _wait(f"http://127.0.0.1:{api_port}/v1/health")
        _wait(f"http://127.0.0.1:{bff_port}/index.html")
        yield f"http://127.0.0.1:{bff_port}"
    finally:
        for proc in (bff, api):
            proc.terminate()
            proc.wait(10)
    assert any((runs / "runs").iterdir()), "API did not write to UIGEN_RUNS_DIR"


def _submit(base, user, **extra):
    files = {"image": ("login.png", IMAGE.read_bytes(), "image/png")}
    return httpx.post(
        f"{base}/api/uigen/generations", headers={"x-demo-user": user}, files=files, data=extra, timeout=30
    )


def _wait_done(base, user, job_id):
    for _ in range(200):
        body = httpx.get(f"{base}/api/uigen/generations/{job_id}", headers={"x-demo-user": user}).json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.2)
    raise TimeoutError


def test_bff_enforces_per_user_ownership(stack):
    job = _submit(stack, "alice").json()
    assert job["links"]["preview"].startswith("/api/uigen/")  # service URLs never reach the browser
    assert _wait_done(stack, "alice", job["id"])["status"] == "done"
    for path in ("", "/events", "/preview", "/download"):
        r = httpx.get(f"{stack}/api/uigen/generations/{job['id']}{path}", headers={"x-demo-user": "bob"})
        assert r.status_code == 404, path
    preview = httpx.get(f"{stack}/api/uigen/generations/{job['id']}/preview", headers={"x-demo-user": "alice"})
    assert preview.status_code == 200 and preview.headers["content-security-policy"].startswith("sandbox")


def test_bff_input_validation_and_rate_limit(stack):
    no_file = httpx.post(f"{stack}/api/uigen/generations", headers={"x-demo-user": "carol"}, data={"requirement": "x"})
    assert no_file.status_code == 400
    big = httpx.post(
        f"{stack}/api/uigen/generations",
        headers={"x-demo-user": "carol"},
        files={"image": ("big.png", b"0" * (11 * 1024 * 1024), "image/png")},
    )
    assert big.status_code == 413
    assert _submit(stack, "dave", fidelity_repair="{bad").status_code == 400
    codes = [_submit(stack, "erin").status_code for _ in range(5)]
    assert codes == [202, 202, 202, 202, 429]


def test_react_component_full_flow(stack):
    pw = pytest.importorskip("playwright.sync_api")
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{stack}/index.html")
            page.set_input_files("#uigen-file", str(ROOT / "eval" / "cases" / "dashboard.png"))
            page.get_by_role("button", name="Generate").click()
            page.wait_for_selector(".uigen-summary", timeout=60000)
            assert "Status success" in page.locator(".uigen-summary").text_content()
            assert page.locator(".uigen-progress li").last.text_content() == "done: success"
            frame = page.frame_locator("iframe[title='Generated UI']")
            assert frame.locator("h1").text_content(timeout=10000) == "Overview"
            assert errors == []
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium not installed")
        raise
