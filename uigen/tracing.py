"""One trace per generation (generation_id), with a span per step.

Writes JSONL locally (traces/traces.jsonl). The span/attribute shape matches what Langfuse and
OpenTelemetry expect, so an exporter can be added without touching the pipeline.
"""

import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TRACE_DIR = Path(__file__).resolve().parent.parent / "traces"
_WRITE_LOCK = threading.Lock()  # concurrent generations append to the same file


class Trace:
    def __init__(self, **attributes):
        self.generation_id = "gen_" + uuid.uuid4().hex[:12]
        self.started = time.perf_counter()
        self.data = {
            "generation_id": self.generation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **attributes,
            "spans": [],
            "llm_calls": [],
        }

    @contextmanager
    def span(self, name, **attrs):
        record = {"name": name, **attrs}
        t0 = time.perf_counter()
        try:
            yield record
            record.setdefault("status", "ok")
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - t0) * 1000)
            self.data["spans"].append(record)

    def llm_call(self, purpose, provider, usage):
        self.data["llm_calls"].append({"purpose": purpose, "provider": provider.name, "model": provider.model, **usage})

    def finish(self, status, trace_dir=None, **attrs):
        calls = self.data["llm_calls"]
        self.data.update(
            status=status,
            **attrs,
            total_ms=round((time.perf_counter() - self.started) * 1000),
            input_tokens=sum(c.get("input_tokens", 0) for c in calls),
            output_tokens=sum(c.get("output_tokens", 0) for c in calls),
            repair_count=sum(1 for c in calls if c["purpose"] == "repair"),
        )
        directory = Path(trace_dir) if trace_dir else TRACE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(self.data) + "\n"
        with _WRITE_LOCK, open(directory / "traces.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
        return self.data
