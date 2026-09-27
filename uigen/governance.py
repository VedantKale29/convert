"""Input governance: what may be sent to the external LLM, and in what form."""

import hashlib
import io
import os
import warnings

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE_PX = 2048  # bigger images cost more tokens without adding detail
MAX_REQUIREMENT_CHARS = 2000
BIDI_CONTROLS = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f")
MAX_PIXELS = 40_000_000  # ~8K x 5K; above this decoding costs too much memory (decompression bombs)
MIN_SIDE_PX = 64  # smaller cannot contain a readable UI
MAX_ASPECT = 12  # long full-page screenshots are fine; thin strips are not
ACCEPTED_FORMATS = {"PNG", "JPEG", "MPO", "WEBP"}  # MPO = multi-picture JPEG saved by many phones


class GovernanceError(Exception):
    pass


def external_llm_allowed():
    return os.getenv("ALLOW_EXTERNAL_LLM", "false").strip().lower() == "true"


def sanitize_image(raw, max_side_px=MAX_SIDE_PX):
    """Verify it is really an image, drop all metadata (EXIF, GPS, device), cap the size, re-encode as PNG.
    Returns (png_bytes, info)."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise GovernanceError(f"image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        with warnings.catch_warnings():  # we enforce our own, stricter pixel limit below
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            img.verify()  # structural check
            img = Image.open(io.BytesIO(raw))  # verify() invalidates the object
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise GovernanceError("file is not a valid image") from exc
    if img.format not in ACCEPTED_FORMATS:
        raise GovernanceError(f"image format {img.format} not accepted (PNG, JPEG, WEBP only)")
    w, h = img.size  # read from the header: nothing decoded yet
    if w * h > MAX_PIXELS:
        raise GovernanceError(f"image is {w}x{h} ({w * h / 1e6:.0f} MP); the limit is {MAX_PIXELS / 1e6:.0f} MP")
    if min(w, h) < MIN_SIDE_PX:
        raise GovernanceError(f"image is {w}x{h}; it must be at least {MIN_SIDE_PX} px on each side")
    if max(w, h) / min(w, h) > MAX_ASPECT:
        raise GovernanceError(f"image is {w}x{h}; aspect ratio above {MAX_ASPECT}:1 cannot be read reliably")
    img.seek(0)  # animated / multi-picture files: first frame only
    if img.mode in ("RGBA", "LA", "P", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, "white")  # transparent areas become white, not black
        img.paste(rgba, mask=rgba.getchannel("A"))
    else:
        img = img.convert("RGB")  # new pixel data only: metadata is not copied
    original_size = img.size
    img.thumbnail((max_side_px, max_side_px))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    png = out.getvalue()
    return png, {
        "original_sha256": hashlib.sha256(raw).hexdigest(),
        "sent_sha256": hashlib.sha256(png).hexdigest(),
        "original_size": list(original_size),
        "sent_size": list(img.size),
    }


def check_requirement(text):
    # drop invisible control and bidi-override characters (keep newlines and tabs)
    text = "".join(ch for ch in (text or "") if ch in "\n\t" or (ord(ch) >= 32 and ch not in BIDI_CONTROLS))
    if text and len(text) > MAX_REQUIREMENT_CHARS:
        raise GovernanceError(f"requirement text is longer than {MAX_REQUIREMENT_CHARS} characters")
    return (text or "").strip()
