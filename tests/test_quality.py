"""Phase 2 quality checks: pure logic always runs; browser tests skip if Chromium is unavailable."""

import functools

import pytest
from conftest import CASES, load_case
from PIL import Image, ImageDraw

from uigen import pipeline, quality
from uigen.llm import ScriptedProvider


@functools.lru_cache(maxsize=1)
def _browser_ok():
    if not quality.available()[0]:
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(
    not _browser_ok(), reason="Chromium not installed: uv run playwright install chromium"
)


# ---------- pure logic ----------
def test_normalize_survives_ocr_spacing_errors():
    assert quality.normalize("Work email *") == quality.normalize("Workemail*") == "workemail"


def test_fuzzy_same():
    assert quality._same("forgotpassword", "forgotpasword")  # one OCR typo
    assert quality._same("signin", "signinnow")  # containment, similar length
    assert not quality._same("signin", "signintotheportal")  # short word inside a long sentence
    assert not quality._same("orders", "suppliers")


def test_match_texts_recall_precision_position():
    image = [
        {"text": "Sign in", "cx": 0.5, "cy": 0.1},
        {"text": "Email", "cx": 0.3, "cy": 0.3},
        {"text": "Remember me", "cx": 0.3, "cy": 0.6},
    ]
    dom = [
        {"text": "Sign in", "x": 450, "y": 90, "w": 100, "h": 20},  # same place
        {"text": "Email", "x": 800, "y": 700, "w": 60, "h": 20},  # far away
        {"text": "Totally invented", "x": 10, "y": 10, "w": 90, "h": 20},
    ]  # hallucinated
    r = quality.match_texts(image, dom, 1000, 1000)
    assert r["missing_text"] == ["Remember me"] and r["extra_text"] == ["Totally invented"]
    assert r["text_recall"] == 0.667 and r["text_precision"] == 0.667
    assert 0.4 < r["position"] < 0.6  # one perfect, one far off


def test_ssim_identical_vs_different():
    a = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(a).rectangle([50, 50, 200, 150], fill="black")
    b = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(b).rectangle([220, 160, 380, 280], fill="black")
    assert quality.ssim(a, a) == 1.0
    assert quality.ssim(a, b) < 0.9


# ---------- browser ----------
def _generate(image_name, case, tmp_path):
    raw = (CASES / f"{image_name}.png").read_bytes()
    res = pipeline.generate(
        raw,
        ScriptedProvider([load_case(case).model_dump_json()]),
        runs_dir=tmp_path / "runs",
        cache_dir=tmp_path / "cache",
        trace_dir=tmp_path / "traces",
        use_cache=False,
    )
    return res


@needs_browser
def test_correct_result_scores_far_above_wrong_result(tmp_path):
    good = _generate("login", "login", tmp_path)["quality"]
    wrong = _generate("login", "dashboard", tmp_path)["quality"]
    assert good["text_recall"] == 1.0 and good["fidelity"] > 0.7
    assert wrong["text_recall"] == 0.0 and wrong["fidelity"] < 0.3
    assert "Remember me" in wrong["missing_text"] and "SupplyView" in wrong["extra_text"]


@needs_browser
def test_quality_is_saved_traced_and_warned(tmp_path):
    res = _generate("login", "dashboard", tmp_path)
    run_dir = tmp_path / "runs" / res["generation_id"]
    assert (run_dir / "render.png").exists() and (run_dir / "quality.json").exists()
    assert res["trace"]["quality"]["fidelity"] == res["quality"]["fidelity"]
    assert any(w.startswith("low fidelity") for w in res["warnings"])
    assert any("'Remember me'" in w for w in res["warnings"])
    assert res["status"] == "success"  # reported, not blocking


@needs_browser
def test_axe_finds_real_accessibility_errors(tmp_path):
    bad = tmp_path / "bad.html"
    bad.write_text(
        '<html><body><main><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="><input type="text">'
        '<p style="color:#eee;background:#fff">low contrast text</p></main></body></html>'
    )
    _, _, axe = quality.render(bad, 800, 600)
    ids = {v["id"] for v in axe}
    assert {"image-alt", "label", "color-contrast"} <= ids


@needs_browser
def test_generated_screens_have_no_axe_violations(tmp_path):
    for case in ("login", "dashboard", "settings"):
        q = _generate(case, case, tmp_path)["quality"]
        assert q["accessibility"] == [], (case, q["accessibility"])


def test_missing_dependencies_is_a_warning_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "available", lambda: (False, "quality check skipped: test"))
    res = _generate("login", "login", tmp_path)
    assert res["status"] == "success" and res["quality"] is None
    assert "quality check skipped: test" in res["warnings"]


def test_browser_crash_is_a_warning_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, "available", lambda: (True, ""))

    def boom(png, preview):
        raise RuntimeError("Executable doesn't exist at /ms-playwright/chromium\nmore detail")

    monkeypatch.setattr(quality, "check", boom)
    res = _generate("login", "login", tmp_path)
    assert res["status"] == "success"
    assert "quality check failed: Executable doesn't exist at /ms-playwright/chromium" in res["warnings"]


# ---------- fidelity repair ----------
from uigen.settings import GenerationConfig  # noqa: E402


def _fidelity_run(replies, tmp_path, **config):
    p = ScriptedProvider(replies)
    res = pipeline.generate(
        (CASES / "login.png").read_bytes(),
        p,
        runs_dir=tmp_path / "r",
        cache_dir=tmp_path / "c",
        trace_dir=tmp_path / "t",
        config=GenerationConfig(fidelity_repair=True, **config),
    )
    return res, p


@needs_browser
def test_fidelity_repair_fixes_a_wrong_reading(tmp_path):
    wrong, right = load_case("dashboard").model_dump_json(), load_case("login").model_dump_json()
    res, p = _fidelity_run([wrong, right], tmp_path)
    fr = res["fidelity_repair"]
    assert len(p.calls) == 2 and fr["kept"] == "repaired" and fr["after"] > fr["before"]
    prompt = p.calls[1]["user"]
    assert '"Remember me"' in prompt and "MISSING" in prompt and '"SupplyView"' in prompt
    assert res["quality"]["fidelity"] == fr["after"] and res["ir"]["title"] == "Sign in"
    assert res["trace"]["repair_count"] == 0  # fidelity repairs are counted separately
    assert [c["purpose"] for c in res["trace"]["llm_calls"]] == ["generate", "fidelity_repair"]


@needs_browser
def test_fidelity_repair_never_makes_things_worse(tmp_path):
    right, wrong = load_case("login").model_dump_json(), load_case("dashboard").model_dump_json()
    res, p = _fidelity_run([right, wrong], tmp_path, fidelity_threshold=0.99)  # force a repair attempt
    fr = res["fidelity_repair"]
    assert fr["kept"] == "original" and fr["after"] < fr["before"]
    assert res["ir"]["title"] == "Sign in"


@needs_browser
def test_invalid_fidelity_repair_is_discarded(tmp_path):
    wrong = load_case("dashboard").model_dump_json()
    res, p = _fidelity_run([wrong, '{"title": "broken"}'], tmp_path)
    assert res["status"] == "success" and res["fidelity_repair"]["kept"] == "original"
    assert res["fidelity_repair"]["rejected"]


@needs_browser
def test_good_result_needs_no_fidelity_repair(tmp_path):
    res, p = _fidelity_run([load_case("login").model_dump_json()], tmp_path)
    assert len(p.calls) == 1 and res["fidelity_repair"] is None


@needs_browser
def test_quality_works_inside_a_running_event_loop(tmp_path):
    """Jupyter runs an asyncio loop; Playwright's sync API must not break there."""
    import asyncio

    async def in_loop():
        return _generate("login", "login", tmp_path)["quality"]

    q = asyncio.run(in_loop())
    assert q is not None and q["fidelity"] > 0.7
