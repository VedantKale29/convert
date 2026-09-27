import json

import pytest
from conftest import CASES, load_case

from uigen import pipeline
from uigen.llm import ScriptedProvider
from uigen.settings import GenerationConfig

IMAGE = (CASES / "login.png").read_bytes()
GOOD = load_case("login").model_dump_json()


def _bad():
    d = json.loads(GOOD)
    d["root"]["children"][0]["variant"] = "fancy"
    return json.dumps(d)


def _run(provider, tmp_path, **config):
    return pipeline.generate(
        IMAGE,
        provider,
        runs_dir=tmp_path / "r",
        cache_dir=tmp_path / "c",
        trace_dir=tmp_path / "t",
        config=GenerationConfig(run_quality=False, **config),
    )


def test_config_validation():
    with pytest.raises(ValueError, match="bounded"):
        GenerationConfig(max_ir_repairs=5)
    with pytest.raises(ValueError):
        GenerationConfig(image_detail="ultra")
    with pytest.raises(ValueError):
        GenerationConfig(fidelity_threshold=1.5)


def test_fingerprint_tracks_only_output_affecting_settings():
    base = GenerationConfig()
    assert base.fingerprint() == base.with_(name="renamed", tags={"note": "x"}, run_quality=False).fingerprint()
    assert base.fingerprint() != base.with_(image_detail="low").fingerprint()
    assert base.fingerprint() != base.with_(soft_rules=("Only use Stack",)).fingerprint()


def test_settings_reach_the_provider_and_prompt(tmp_path):
    p = ScriptedProvider([GOOD])
    res = _run(
        p,
        tmp_path,
        image_detail="low",
        temperature=0.2,
        extra_instructions="Prefer Grid for cards.",
        soft_rules=("Rule A",),
    )
    call = p.calls[0]
    assert call["detail"] == "low" and call["temperature"] == 0.2
    assert "- Rule A" in call["system"] and "Prefer Grid for cards." in call["system"]
    assert res["trace"]["config"]["image_detail"] == "low"


def test_repair_budget_is_respected(tmp_path):
    p0 = ScriptedProvider([_bad(), GOOD])
    assert _run(p0, tmp_path, max_ir_repairs=0)["status"] == "invalid_ir" and len(p0.calls) == 1
    p2 = ScriptedProvider([_bad(), _bad(), GOOD])
    res = _run(p2, tmp_path, max_ir_repairs=2)
    assert res["status"] == "success" and len(p2.calls) == 3 and res["trace"]["repair_count"] == 2


def test_different_config_does_not_reuse_cache(tmp_path):
    _run(ScriptedProvider([GOOD]), tmp_path)
    p = ScriptedProvider([GOOD])
    res = _run(p, tmp_path, image_detail="low")
    assert res["trace"]["cache_hit"] is False and len(p.calls) == 1
    p_same = ScriptedProvider([])
    assert _run(p_same, tmp_path, name="same settings, new name")["trace"]["cache_hit"] is True


def test_max_side_px_is_applied(tmp_path):
    res = _run(ScriptedProvider([GOOD]), tmp_path, max_side_px=512)
    governance_span = res["trace"]["spans"][0]
    assert max(governance_span["sent_size"]) == 512
