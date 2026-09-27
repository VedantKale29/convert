import io

import pytest
from PIL import Image

from uigen.governance import GovernanceError, check_requirement, external_llm_allowed, sanitize_image


def _jpeg_with_exif():
    img = Image.new("RGB", (3000, 1500), "white")
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"  # Make
    exif[0x0110] = "Model-X"  # Model
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_metadata_is_stripped_and_image_downscaled():
    raw = _jpeg_with_exif()
    assert b"SecretCameraMaker" in raw
    png, info = sanitize_image(raw)
    assert b"SecretCameraMaker" not in png
    assert Image.open(io.BytesIO(png)).format == "PNG"
    assert info["original_size"] == [3000, 1500] and info["sent_size"] == [2048, 1024]
    assert info["original_sha256"] != info["sent_sha256"]


def test_rejects_non_images_and_huge_files():
    with pytest.raises(GovernanceError, match="not a valid image"):
        sanitize_image(b"%PDF-1.7 definitely not an image")
    with pytest.raises(GovernanceError, match="larger than 10 MB"):
        sanitize_image(b"0" * (10 * 1024 * 1024 + 1))
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    with pytest.raises(GovernanceError, match="GIF not accepted"):
        sanitize_image(gif.getvalue())


def test_requirement_length_limit():
    assert check_requirement("  a page  ") == "a page"
    with pytest.raises(GovernanceError):
        check_requirement("x" * 2001)


def test_external_llm_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "false")
    assert external_llm_allowed() is False
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "TRUE")
    assert external_llm_allowed() is True


def _png(size, mode="RGB", color="white"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_decompression_bomb_rejected_before_decoding():
    with pytest.raises(GovernanceError, match="limit is 40 MP"):
        sanitize_image(_png((12000, 12000), "L", 0))


def test_useless_images_rejected():
    with pytest.raises(GovernanceError, match="at least 64 px"):
        sanitize_image(_png((1, 1)))
    with pytest.raises(GovernanceError, match="aspect ratio"):
        sanitize_image(_png((1300, 100)))


def test_phone_mpo_jpeg_accepted():
    buf = io.BytesIO()
    im = Image.new("RGB", (800, 600), "white")
    im.save(buf, "MPO", save_all=True, append_images=[im.copy()])
    _, info = sanitize_image(buf.getvalue())
    assert info["sent_size"] == [800, 600]


def test_transparency_becomes_white_not_black():
    im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (0, 0, 100, 100))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = Image.open(io.BytesIO(sanitize_image(buf.getvalue())[0]))
    assert out.getpixel((50, 50)) == (255, 0, 0) and out.getpixel((150, 50)) == (255, 255, 255)


def test_requirement_control_characters_removed():
    assert check_requirement("a\u202eb\x07c\nd") == "abc\nd"
