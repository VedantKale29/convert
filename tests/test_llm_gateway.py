"""The OpenAI request shape, without network."""

from types import SimpleNamespace

import openai

from uigen import llm
from uigen.ir import strict_json_schema


class _Completions:
    def create(self, **kwargs):
        self.kwargs = kwargs
        msg = SimpleNamespace(content='{"ok": 1}', refusal=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def test_structured_output_request(monkeypatch):
    comp = _Completions()
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=comp)))
    provider = llm.OpenAIProvider(model="gpt-4.1-mini")
    text, usage = provider.generate_json("SYS", "USER", b"\x89PNG", strict_json_schema(), "ui_document")
    assert text == '{"ok": 1}' and usage["input_tokens"] == 100 and usage["output_tokens"] == 20
    fmt = comp.kwargs["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "ui_document"
    content = comp.kwargs["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ---------- failure handling ----------
import httpx  # noqa: E402
import pytest  # noqa: E402
from conftest import CASES  # noqa: E402

from uigen import pipeline  # noqa: E402


def _provider_raising(monkeypatch, exc=None, finish_reason="stop", refusal=None):
    class Comp:
        def create(self, **kwargs):
            if exc is not None:
                raise exc
            msg = SimpleNamespace(content='{"x": 1}', refusal=refusal)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)], usage=None)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=Comp()), kw=kw))
    return llm.OpenAIProvider(model="m")


REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


@pytest.mark.parametrize(
    "exc, message",
    [
        (openai.APITimeoutError(request=REQ), "timed out"),
        (openai.RateLimitError("slow down", response=httpx.Response(429, request=REQ), body=None), "rate limited"),
        (
            openai.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None),
            "authentication failed",
        ),
        (openai.APIConnectionError(request=REQ), "model call failed"),
    ],
)
def test_api_failures_become_llm_errors(monkeypatch, exc, message):
    provider = _provider_raising(monkeypatch, exc)
    with pytest.raises(llm.LLMError, match=message) as info:
        provider.generate_json("s", "u", b"x", {}, "n")
    assert "sk-test-secret-value" not in str(info.value)


def test_truncated_and_refused_replies(monkeypatch):
    with pytest.raises(llm.LLMError, match="cut off at the token limit"):
        _provider_raising(monkeypatch, finish_reason="length").generate_json("s", "u", b"x", {}, "n")
    with pytest.raises(llm.LLMError, match="model refused"):
        _provider_raising(monkeypatch, refusal="I can't help").generate_json("s", "u", b"x", {}, "n")


def test_timeout_and_retries_are_explicit(monkeypatch):
    monkeypatch.setenv("OPENAI_TIMEOUT_S", "45")
    provider = _provider_raising(monkeypatch)
    assert provider.client.kw == {"timeout": 45.0, "max_retries": 2}


def test_pipeline_reports_llm_error_status(monkeypatch, tmp_path):
    provider = _provider_raising(monkeypatch, openai.APITimeoutError(request=REQ))
    res = pipeline.generate(
        (CASES / "login.png").read_bytes(),
        provider,
        runs_dir=tmp_path / "r",
        cache_dir=tmp_path / "c",
        trace_dir=tmp_path / "t",
        run_quality=False,
    )
    assert res["status"] == "llm_error" and "timed out" in res["errors"][0]
    assert res["trace"]["status"] == "llm_error"
