"""LLM gateway. The rest of the code talks to LLMProvider, never to OpenAI directly."""

import base64
import os
import time


class LLMError(Exception):
    """A model call failed in a way the pipeline reports as status 'llm_error' (never contains secrets)."""


class LLMProvider:
    name = "base"
    model = "none"

    def generate_json(self, system, user_text, image_png, schema, schema_name, detail="high", temperature=None):
        """Return (raw_json_text, usage_dict)."""
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model=None):
        from openai import OpenAI

        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set (put it in .env)")
        # Explicit limits: the SDK default would wait up to 10 minutes. Retries use exponential backoff
        # and cover timeouts, connection errors, 429 rate limits and 5xx responses.
        self.client = OpenAI(
            timeout=float(os.getenv("OPENAI_TIMEOUT_S", "90")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
        )
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    def generate_json(self, system, user_text, image_png, schema, schema_name, detail="high", temperature=None):
        data_url = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        started = time.perf_counter()
        extra = {} if temperature is None else {"temperature": temperature}
        import openai

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                **extra,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": detail}},
                        ],
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                },
            )
        except openai.AuthenticationError as exc:
            raise LLMError("authentication failed: check OPENAI_API_KEY") from exc
        except openai.RateLimitError as exc:
            raise LLMError("rate limited or out of quota after retries") from exc
        except openai.APITimeoutError as exc:
            raise LLMError("the model call timed out after retries") from exc
        except openai.BadRequestError as exc:
            raise LLMError(f"request rejected by the API: {getattr(exc, 'message', str(exc))[:300]}") from exc
        except openai.OpenAIError as exc:
            raise LLMError(f"model call failed: {type(exc).__name__}") from exc
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"model refused: {choice.message.refusal[:300]}")
        if choice.finish_reason == "length":
            raise LLMError("reply was cut off at the token limit (screen too complex for one reply)")
        usage = response.usage
        return choice.message.content or "", {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "finish_reason": choice.finish_reason,
        }


class ScriptedProvider(LLMProvider):
    """Returns canned replies in order. For tests and the offline demo; no network."""

    name = "scripted"

    def __init__(self, replies, model="scripted"):
        self.replies = list(replies)
        self.model = model
        self.calls = []

    def generate_json(self, system, user_text, image_png, schema, schema_name, detail="high", temperature=None):
        self.calls.append({"system": system, "user": user_text, "detail": detail, "temperature": temperature})
        return self.replies.pop(0), {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "finish_reason": "stop"}
