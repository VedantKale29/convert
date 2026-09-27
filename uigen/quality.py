"""Phase 2: check the RENDERED result against the uploaded image.

1. Render the built preview in headless Chromium at the input image's size.
2. Text fidelity: OCR the input image; every text block must appear in the rendered DOM
   (recall), rendered text should exist in the image (precision), and at roughly the same place.
3. Visual similarity: SSIM between the input and the render (layout / density / spacing).
4. Accessibility: axe-core (WCAG 2 A/AA rules) on the rendered page.

Everything runs locally and offline (OCR models ship inside the pip package).
Scores are REPORTED, not used to block: calibrate thresholds on the eval set first.
"""

import io
import re
import threading
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

AXE_JS = Path(__file__).resolve().parent.parent / "build_workspace" / "node_modules" / "axe-core" / "axe.min.js"
QUALITY_VERSION = "quality-v1"
_OCR_LOCK = threading.Lock()  # one OCR engine shared by all threads
MIN_OCR_CONFIDENCE = 0.5
FUZZY_MATCH = 0.8  # SequenceMatcher ratio needed to call two texts the same
# Initial weights for the single fidelity number. Calibrate against human ratings on the eval set.
WEIGHTS = {"text_f1": 0.5, "position": 0.25, "ssim": 0.25}

# Collect every piece of visible text in the page with its box: text nodes, input placeholders
# and values, and the selected option of each select.
DOM_TEXT_JS = """() => {
  const out = [];
  const add = (text, r) => { if (text && text.trim() && r.width > 0 && r.height > 0)
      out.push({text: text.trim(), x: r.left, y: r.top, w: r.width, h: r.height}); };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, parent = node.parentElement;
    if (!parent || ['SCRIPT', 'STYLE', 'OPTION'].includes(parent.tagName)) continue;
    const style = getComputedStyle(parent);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const range = document.createRange(); range.selectNodeContents(node);
    add(node.textContent, range.getBoundingClientRect());
  }
  document.querySelectorAll('input, textarea').forEach(el => {
    if (['checkbox', 'radio', 'button', 'submit', 'hidden'].includes(el.type)) return;   // value is not visible text
    add(el.value || el.placeholder, el.getBoundingClientRect()); });
  document.querySelectorAll('select').forEach(el => {
    const opt = el.options[el.selectedIndex]; if (opt) add(opt.text, el.getBoundingClientRect()); });
  return out;
}"""


def available():
    """(True, '') if the quality stage can run, else (False, reason)."""
    try:
        import playwright.sync_api  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError as exc:
        return False, f"quality check skipped: {exc.name} not installed (uv sync)"
    if not AXE_JS.exists():
        return False, "quality check skipped: axe-core missing (npm ci in build_workspace)"
    return True, ""


def normalize(text):
    """Lowercase letters and digits only: OCR often drops or adds spaces and punctuation."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _same(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 4 and shorter in longer and len(shorter) / len(longer) >= 0.5:
        return True
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_MATCH


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def ocr_blocks(png_bytes):
    """Text blocks in the image: text + box as fractions of the image size."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = img.size
    with _OCR_LOCK:
        result, _ = _ocr_engine()(np.array(img)[:, :, ::-1])  # engine expects BGR like OpenCV
    blocks = []
    for box, text, conf in result or []:
        if float(conf) < MIN_OCR_CONFIDENCE or len(normalize(text)) < 2:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        blocks.append({"text": text, "cx": (min(xs) + max(xs)) / 2 / w, "cy": (min(ys) + max(ys)) / 2 / h})
    return blocks


MISPLACED_DISTANCE = 0.15  # fraction of the page; farther than this counts as "wrong place"


def region(cx, cy):
    """Human-readable page region for a normalised centre point, e.g. 'at the top-left'."""
    v = "top" if cy < 1 / 3 else ("middle" if cy < 2 / 3 else "bottom")
    h = "left" if cx < 1 / 3 else ("center" if cx < 2 / 3 else "right")
    return "in the center" if (v, h) == ("middle", "center") else f"at the {v}-{h}"


def match_texts(image_blocks, dom_blocks, width, height):
    """Compare OCR blocks from the input with text found in the rendered DOM."""
    doms = [
        {**d, "n": normalize(d["text"]), "cx": (d["x"] + d["w"] / 2) / width, "cy": (d["y"] + d["h"] / 2) / height}
        for d in dom_blocks
    ]
    doms = [d for d in doms if len(d["n"]) >= 2]
    all_dom = "".join(d["n"] for d in doms)
    all_img = "".join(normalize(b["text"]) for b in image_blocks)

    missing, distances, misplaced = [], [], []
    for block in image_blocks:
        n = normalize(block["text"])
        candidates = [d for d in doms if _same(n, d["n"])]
        if candidates:
            best = min(candidates, key=lambda d: (d["cx"] - block["cx"]) ** 2 + (d["cy"] - block["cy"]) ** 2)
            dist = ((best["cx"] - block["cx"]) ** 2 + (best["cy"] - block["cy"]) ** 2) ** 0.5
            distances.append(dist)
            if dist > MISPLACED_DISTANCE:
                misplaced.append(
                    {
                        "text": block["text"],
                        "expected": region(block["cx"], block["cy"]),
                        "actual": region(best["cx"], best["cy"]),
                        "distance": _r(dist),
                    }
                )
        elif not (len(n) >= 4 and n in all_dom):  # text split over several DOM nodes
            missing.append(block["text"])

    extra = [
        d["text"]
        for d in doms
        if not any(_same(d["n"], normalize(b["text"])) for b in image_blocks)
        and not (len(d["n"]) >= 4 and d["n"] in all_img)
    ]

    found = len(image_blocks) - len(missing)
    recall = found / len(image_blocks) if image_blocks else None
    precision = (len(doms) - len(extra)) / len(doms) if doms else None
    f1 = (2 * recall * precision / (recall + precision)) if recall and precision else 0.0
    # 0 distance -> 1.0; a quarter of the page away or more -> 0
    if distances:
        position = float(np.mean([max(0.0, 1 - dist / 0.25) for dist in distances]))
    else:
        position = 0.0 if image_blocks else None  # nothing matched: nothing is in the right place
    return {
        "text_recall": _r(recall),
        "text_precision": _r(precision),
        "text_f1": _r(f1),
        "position": _r(position),
        "missing_text": missing,
        "misplaced": misplaced,
        "extra_text": extra,
        "ocr_blocks": len(image_blocks),
        "dom_blocks": len(doms),
    }


def ssim(img_a, img_b, width=256):
    """Mean structural similarity of two images (grayscale, resized to the same size), 0..1."""
    a = img_a.convert("L")
    size = (width, max(8, round(width * a.height / a.width)))
    x = np.asarray(a.resize(size), dtype=np.float64)
    y = np.asarray(img_b.convert("L").resize(size), dtype=np.float64)
    k = 7

    def box(m):  # mean over every k x k window
        c = np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)

    mx, my = box(x), box(y)
    vx, vy, cov = box(x * x) - mx * mx, box(y * y) - my * my, box(x * y) - mx * my
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    s = ((2 * mx * my + c1) * (2 * cov + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2))
    return _r(float(np.clip(s.mean(), 0, 1)))


def render(preview_file, width, height):
    """Open the built preview at the input size. Returns (screenshot PNG, DOM text boxes, axe result).

    Playwright's sync API cannot run inside a running asyncio loop (Jupyter, some web servers), so in
    that case the browser work runs in a separate worker thread."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _render(preview_file, width, height)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_render, preview_file, width, height).result()


def _render(preview_file, width, height):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if not route.request.url.startswith(("file:", "data:", "about:"))
                    else route.continue_()
                ),
            )
            page.goto(Path(preview_file).resolve().as_uri())
            page.wait_for_selector("main", timeout=10000)
            shot = page.screenshot()
            dom = page.evaluate(DOM_TEXT_JS)
            page.add_script_tag(path=str(AXE_JS))
            axe = page.evaluate("""async () => {
              const r = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa']}});
              return r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length}));
            }""")
        finally:
            browser.close()
    return shot, dom, axe


def check(input_png, preview_file):
    """Full Phase 2 report for one generation."""
    source = Image.open(io.BytesIO(input_png)).convert("RGB")
    width, height = source.size
    shot, dom, axe = render(preview_file, width, height)
    rendered = Image.open(io.BytesIO(shot)).convert("RGB")
    text = match_texts(ocr_blocks(input_png), dom, width, height)
    visual = ssim(source, rendered)
    parts = {"text_f1": text["text_f1"], "position": text["position"], "ssim": visual}
    used = {k: v for k, v in parts.items() if v is not None}
    fidelity = sum(WEIGHTS[k] * v for k, v in used.items()) / sum(WEIGHTS[k] for k in used) if used else None
    return {
        "version": QUALITY_VERSION,
        "fidelity": _r(fidelity),
        "ssim": visual,
        **text,
        "accessibility": axe,
        "a11y_violations": sum(v["nodes"] for v in axe),
        "render_png": shot,
        "viewport": [width, height],
    }


def _r(value):
    return None if value is None else round(float(value), 3)
