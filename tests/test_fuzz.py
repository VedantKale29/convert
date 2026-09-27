"""Property test: EVERY valid IR must compile and build. A failure here is a compiler bug."""

import tempfile

import pytest
from fuzz_ir import random_doc

from uigen.build import build, toolchain_ready
from uigen.compile_react import compile_react
from uigen.validators import validate

SEEDS = range(60)  # tools/fuzz can run thousands; 60 keeps CI fast


@pytest.mark.skipif(not toolchain_ready(), reason="run 'npm ci' in build_workspace/")
@pytest.mark.parametrize("seed", SEEDS)
def test_random_valid_ir_always_builds(seed):
    doc = random_doc(seed)
    assert validate(doc)[0] == [], "fuzzer produced an invalid document"
    result = build(compile_react(doc), tempfile.mkdtemp())
    assert result["ok"], (seed, result["errors"])


def test_compiler_is_deterministic_on_random_docs():
    for seed in range(20):
        assert compile_react(random_doc(seed)) == compile_react(random_doc(seed))
