import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import CASES, load_case

from uigen.llm import ScriptedProvider
from uigen.pipeline import generate
from uigen.retention import purge_old_runs


def test_purge_only_old_generation_folders(tmp_path):
    old, new, other = tmp_path / "gen_aaaaaaaaaaaa", tmp_path / "gen_bbbbbbbbbbbb", tmp_path / "keep_me"
    for d in (old, new, other):
        d.mkdir()
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old, (ten_days_ago, ten_days_ago))
    os.utime(other, (ten_days_ago, ten_days_ago))
    assert purge_old_runs(tmp_path, days=7) == ["gen_aaaaaaaaaaaa"]
    assert not old.exists() and new.exists() and other.exists()  # never touches unrelated folders
    assert purge_old_runs(tmp_path, days=0) == []  # 0 = retention disabled


def test_parallel_generations_do_not_interfere(tmp_path):
    jobs = ["login", "dashboard", "settings"] * 2

    def one(case):
        return generate(
            (CASES / f"{case}.png").read_bytes(),
            ScriptedProvider([load_case(case).model_dump_json()]),
            runs_dir=tmp_path / "r",
            cache_dir=tmp_path / "c",
            trace_dir=tmp_path / "t",
            use_cache=False,
            run_quality=False,
        )

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(one, jobs))
    assert all(r["status"] == "success" for r in results)
    assert [r["ir"]["title"] for r in results] == [load_case(c).title for c in jobs]
    lines = (tmp_path / "t" / "traces.jsonl").read_text().splitlines()
    assert len(lines) == 6 and all(json.loads(line)["status"] == "success" for line in lines)
