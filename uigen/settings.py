"""Every tunable knob of a generation, in one place. Experiments change these, never the code.

The config is recorded on every trace and is part of the cache key, so results are always
attributable to the exact settings that produced them.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

DEFAULT_SOFT_RULES = (
    "Prefer the most specific component (Metric for a number with a label, Table for tabular data).",
    "Use Stack for rows/columns and Grid for evenly repeated items such as card rows.",
    "Use only as much nesting as the layout needs.",
    "Elements on the same line at opposite ends (a title with a badge or button on the right) are a row "
    "Stack with justify 'between' and align 'center'.",
    "A navigation bar pinned to the bottom of a phone screen is a List with variant 'tabbar'; set 'active' to "
    "the 0-based index of the highlighted item.",
    "Pill-shaped filter buttons where one is selected are Tabs with variant 'pills' and 'active' set.",
    "A search box or field without a visible label still needs a 'label'; set 'labelHidden' to true.",
)


@dataclass(frozen=True)
class GenerationConfig:
    name: str = "default"
    # model call
    image_detail: str = "high"  # "high" | "low" | "auto"  (low = fewer tokens, less detail)
    temperature: float | None = None  # None = provider default
    max_side_px: int = 2048  # images are downscaled to this before sending
    # prompt experiments
    soft_rules: tuple[str, ...] = DEFAULT_SOFT_RULES
    extra_instructions: str = ""  # appended to the system prompt
    # bounded repairs
    max_ir_repairs: int = 1  # validation-error repairs (0..2)
    fidelity_repair: bool = False  # one extra call when the render does not match the image
    fidelity_threshold: float = 0.6  # repair only below this fidelity
    # quality stage
    run_quality: bool = True
    tags: dict = field(default_factory=dict)  # free-form labels for your experiment notes

    def __post_init__(self):
        if self.image_detail not in {"high", "low", "auto"}:
            raise ValueError("image_detail must be 'high', 'low' or 'auto'")
        if not 0 <= self.max_ir_repairs <= 2:
            raise ValueError("max_ir_repairs must be 0, 1 or 2 (repairs stay bounded)")
        if not 0.0 <= self.fidelity_threshold <= 1.0:
            raise ValueError("fidelity_threshold must be between 0 and 1")
        if not 256 <= self.max_side_px <= 4096:
            raise ValueError("max_side_px must be between 256 and 4096")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")

    def with_(self, **changes):
        """Copy with some settings changed: base.with_(name="low-detail", image_detail="low")."""
        return replace(self, **changes)

    def as_dict(self):
        d = asdict(self)
        d["soft_rules"] = list(self.soft_rules)
        return d

    def fingerprint(self):
        """Settings that change the LLM output (for the cache key). Name, tags and quality flags excluded."""
        d = self.as_dict()
        for k in ("name", "tags", "run_quality", "fidelity_repair", "fidelity_threshold"):
            d.pop(k)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]
