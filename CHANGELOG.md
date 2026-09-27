# Changelog

## registry-v2 · ds-v2 · ui-generator-v3 — from the first real run (phone screenshot)
The model read every card, PO number, supplier, ETA and badge correctly; the vocabulary could not express
the layout. Measured on the phone screen: fidelity 0.81 → 0.86, position 0.56 → 0.76, misplaced items 6 → 0.

**Vocabulary (registry-v2)**
- `Stack`: `justify` (start | center | end | between) and `wrap` — e.g. a title left, a badge right.
- `List`: variants `horizontal` and `tabbar` (bottom navigation, pinned to the bottom), `active` item.
- `Tabs`: variant `pills` (filter chips), `active` item.
- `Input` / `Select` / `Textarea`: `labelHidden` — label kept for screen readers, hidden visually.
- Validator: `active` must point at an existing item.

**Design system (ds-v2)**
- Success and warning badge colours failed WCAG AA with white text (3.30:1 and 3.19:1). Now 5.0:1+.
- New guard: `uigen/contrast.py` + test — every text/background pair the compiler emits must pass AA.

**Rendering**
- Previews (Gradio app, API result `viewport`, React component) render at the screenshot's width.
- Card content has vertical spacing (a CSS specificity bug cancelled it; now covered by a browser test).

**Quality check**
- Phone status bars, OS taskbars and browser URLs at the image edges no longer count as "missing".
- OCR look-alikes (I/l/1, O/0) match: "AlI" = "All", while "Late" ≠ "Lake".
- Placeholder and select text positions are measured where the text is, not the box centre.

**Prompt (ui-generator-v3)**: soft rules for space-between rows, bottom tab bars, pill filters, hidden labels.

**Eval**: `mobile_deliveries` added as a labeled case (4 labeled + 12 unlabeled images).

## Earlier
Phase 0-2, experiments notebook, fidelity repair, HTTP API + Node BFF + React integration: see README.
