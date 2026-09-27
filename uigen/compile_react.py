"""Deterministic compiler: validated UI IR -> React (App.jsx + styles.css).

Same IR in -> byte-identical code out. The LLM never writes code: every string from the IR
is emitted as an escaped JS string literal, and every size/colour comes from design tokens.
"""

import json

from .config import registry, tokens

COMPILER_VERSION = "react-compiler-v1"


def js(value):
    """Escaped JavaScript literal for any IR value. <, > and & are escaped too, so no IR text can
    ever close a tag or a <script> element, whatever context the code ends up in."""
    return json.dumps(value, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def A(value):
    """JSX attribute value as an expression: attr={"..."}.
    JSX attribute *strings* (attr="...") do not process escapes, so IR text never goes there."""
    return "{" + js(value) + "}"


class _Emitter:
    def __init__(self):
        self.lines = []
        self.field_counter = 0
        self.uses_tabs = False

    def emit(self, depth, text):
        self.lines.append("  " * depth + text)

    def next_id(self):
        self.field_counter += 1
        return f"field-{self.field_counter}"


def _variant(node):
    return node.variant or registry()["components"][node.type]["variants"][0]


def _classes(*names):
    return A(" ".join(n for n in names if n))


def _children(em, node, depth, in_form):
    for child in node.children:
        _node(em, child, depth, in_form)


def _field(em, depth, label, required, control):
    fid = em.next_id()
    star = " *" if required else ""
    em.emit(depth, '<div className="ui-field">')
    em.emit(depth + 1, f"<label htmlFor={A(fid)}>{{{js(label + star)}}}</label>")
    em.emit(depth + 1, control(fid))
    em.emit(depth, "</div>")


def _node(em, node, depth, in_form=False):
    t, v = node.type, _variant(node)
    text = node.text

    if t == "Stack":
        cls = _classes(
            "ui-stack",
            f"ui-stack--{node.prop('direction')}",
            node.prop("gap") and f"ui-gap--{node.prop('gap')}",
            node.prop("align") and f"ui-align--{node.prop('align')}",
        )
        em.emit(depth, f"<div className={cls}>")
        _children(em, node, depth + 1, in_form)
        em.emit(depth, "</div>")
    elif t == "Grid":
        cls = _classes(
            "ui-grid", f"ui-grid--cols-{int(node.prop('columns'))}", node.prop("gap") and f"ui-gap--{node.prop('gap')}"
        )
        em.emit(depth, f"<div className={cls}>")
        _children(em, node, depth + 1, in_form)
        em.emit(depth, "</div>")
    elif t in ("Navbar", "Sidebar", "Card"):
        tag = {"Navbar": "nav", "Sidebar": "aside", "Card": "section"}[t]
        base = f"ui-{t.lower()}"
        em.emit(depth, f"<{tag} className={_classes(base, f'{base}--{v}')}>")
        if text:
            title_cls = "ui-navbar__brand" if t == "Navbar" else "ui-card__title"
            em.emit(depth + 1, f"<div className={A(title_cls)}>{{{js(text)}}}</div>")
        _children(em, node, depth + 1, in_form)
        em.emit(depth, f"</{tag}>")
    elif t == "Form":
        em.emit(depth, '<form className="ui-form" onSubmit={handleSubmit}>')
        if text:
            em.emit(depth + 1, f'<div className="ui-form__title">{{{js(text)}}}</div>')
        _children(em, node, depth + 1, True)
        em.emit(depth, "</form>")
    elif t == "Heading":
        level = int(node.prop("level"))
        em.emit(depth, f'<h{level} className="ui-heading">{{{js(text)}}}</h{level}>')
    elif t == "Text":
        em.emit(depth, f"<p className={_classes('ui-text', f'ui-text--{v}')}>{{{js(text)}}}</p>")
    elif t == "Link":
        em.emit(depth, f'<a className="ui-link" href="#" onClick={{preventNav}}>{{{js(text)}}}</a>')
    elif t == "Button":
        btype = "submit" if (in_form and v == "primary") else "button"
        em.emit(
            depth,
            f"<button type={A(btype)} className={_classes('ui-button', f'ui-button--{v}')}>{{{js(text)}}}</button>",
        )
    elif t == "Input":
        req = bool(node.prop("required", False))
        attrs = f"type={A(node.prop('inputType'))}"
        if node.prop("placeholder"):
            attrs += f" placeholder={A(node.prop('placeholder'))}"
        if req:
            attrs += " required"
        _field(
            em,
            depth,
            node.prop("label"),
            req,
            lambda fid: f'<input id={A(fid)} name={A(fid)} className="ui-input" {attrs} />',
        )
    elif t == "Textarea":
        ph = f" placeholder={A(node.prop('placeholder'))}" if node.prop("placeholder") else ""
        _field(
            em,
            depth,
            node.prop("label"),
            False,
            lambda fid: f'<textarea id={A(fid)} name={A(fid)} className="ui-input ui-textarea"{ph} />',
        )
    elif t == "Select":
        opts = "".join(f"<option key={A(i)}>{{{js(o)}}}</option>" for i, o in enumerate(node.prop("options")))
        _field(
            em,
            depth,
            node.prop("label"),
            False,
            lambda fid: f'<select id={A(fid)} name={A(fid)} className="ui-input">{opts}</select>',
        )
    elif t == "Checkbox":
        fid = em.next_id()
        em.emit(depth, '<div className="ui-check">')
        em.emit(depth + 1, f'<input id={A(fid)} name={A(fid)} type="checkbox" />')
        em.emit(depth + 1, f"<label htmlFor={A(fid)}>{{{js(node.prop('label'))}}}</label>")
        em.emit(depth, "</div>")
    elif t == "Image":
        alt = node.prop("alt")
        em.emit(depth, f'<div className="ui-image" role="img" aria-label={A(alt)}>{{{js(alt)}}}</div>')
    elif t == "Metric":
        em.emit(depth, '<div className="ui-metric">')
        em.emit(depth + 1, f'<div className="ui-metric__label">{{{js(node.prop("label"))}}}</div>')
        em.emit(depth + 1, f'<div className="ui-metric__value">{{{js(node.prop("value"))}}}</div>')
        em.emit(depth, "</div>")
    elif t == "Table":
        cols, rows = node.prop("columns"), node.prop("rows") or []
        em.emit(depth, '<div className="ui-table-wrap">')
        em.emit(depth + 1, '<table className="ui-table">')
        if text:
            em.emit(depth + 2, f"<caption>{{{js(text)}}}</caption>")
        head = "".join(f'<th scope="col">{{{js(c)}}}</th>' for c in cols)
        em.emit(depth + 2, f"<thead><tr>{head}</tr></thead>")
        em.emit(depth + 2, "<tbody>")
        for r in rows:
            cells = "".join(f"<td>{{{js(c)}}}</td>" for c in r)
            em.emit(depth + 3, f"<tr>{cells}</tr>")
        em.emit(depth + 2, "</tbody>")
        em.emit(depth + 1, "</table>")
        em.emit(depth, "</div>")
    elif t == "List":
        em.emit(depth, f"<ul className={_classes('ui-list', f'ui-list--{v}')}>")
        for item in node.prop("items"):
            em.emit(depth + 1, f"<li>{{{js(item)}}}</li>")
        em.emit(depth, "</ul>")
    elif t == "Tabs":
        em.uses_tabs = True
        em.emit(depth, f"<TabBar items={{{js(node.prop('items'))}}} />")
    elif t == "Badge":
        em.emit(depth, f"<span className={_classes('ui-badge', f'ui-badge--{v}')}>{{{js(text)}}}</span>")
    elif t == "Divider":
        em.emit(depth, '<hr className="ui-divider" />')
    elif t == "Chart":
        label = node.prop("label") or text or f"{v} chart"
        em.emit(
            depth,
            f'<div className={_classes("ui-chart", f"ui-chart--{v}")} role="img" aria-label={A(label)}>'
            f"{{{js(label + ' (' + v + ' chart placeholder)')}}}</div>",
        )
    elif t == "Placeholder":
        em.emit(depth, f'<div className="ui-placeholder" data-unsupported="true">{{{js(text)}}}</div>')
    else:  # unreachable after validation
        raise ValueError(f"compiler has no mapping for component '{t}'")


TABS_HELPER = """function TabBar({ items }) {
  const [active, setActive] = useState(0);
  return (
    <div className="ui-tabs" role="tablist">
      {items.map((item, i) => (
        <button key={i} type="button" role="tab" aria-selected={i === active}
          className={i === active ? "ui-tab ui-tab--active" : "ui-tab"} onClick={() => setActive(i)}>
          {item}
        </button>
      ))}
    </div>
  );
}
"""


def compile_jsx(doc):
    em = _Emitter()
    _node(em, doc.root, 3)
    body = "\n".join(em.lines)
    imports = "import React, { useState } from 'react';" if em.uses_tabs else "import React from 'react';"
    helper = ("\n" + TABS_HELPER) if em.uses_tabs else ""
    return f"""// Generated by {COMPILER_VERSION} from ui-ir-v1. Do not edit by hand.
{imports}
import './styles.css';
{helper}
const handleSubmit = (event) => {{ event.preventDefault(); }};
const preventNav = (event) => {{ event.preventDefault(); }};

export default function App() {{
  return (
    <main className="ui-app" aria-label={A(doc.title)}>
{body}
    </main>
  );
}}
"""


def compile_css():
    """All hard design rules live here, generated from design_tokens.json."""
    t = tokens()
    c, s, r, comp = t["colors"], t["spacing"], t["radius"], t["components"]
    lines = [f"/* Generated by {COMPILER_VERSION} from {t['version']}. Do not edit by hand. */", ":root {"]
    lines += [f"  --color-{k}: {v};" for k, v in c.items()]
    lines += [f"  --space-{k}: {v};" for k, v in s.items()]
    lines += [f"  --radius-{k}: {v};" for k, v in r.items()]
    lines.append("}")
    lines += [f".ui-gap--{k} {{ gap: var(--space-{k}); }}" for k in s]
    lines += [f".ui-grid--cols-{n} {{ grid-template-columns: repeat({n}, minmax(0, 1fr)); }}" for n in range(1, 7)]
    lines.append(f"""
* {{ box-sizing: border-box; }}
.ui-app {{ font-family: {t["typography"]["fontFamily"]}; font-size: {t["typography"]["baseSize"]};
  color: var(--color-text); background: var(--color-background); min-height: 100vh; padding: var(--space-md); }}
.ui-stack {{ display: flex; gap: var(--space-md); }}
.ui-stack--row {{ flex-direction: row; }}
.ui-stack--row > .ui-stack, .ui-stack--row > .ui-grid, .ui-stack--row > .ui-form,
.ui-stack--row > .ui-table-wrap, .ui-stack--row > .ui-chart {{ flex: 1 1 0; min-width: 0; }}
.ui-stack--column {{ flex-direction: column; }}
.ui-align--start {{ align-items: flex-start; }} .ui-align--center {{ align-items: center; }}
.ui-align--end {{ align-items: flex-end; }} .ui-align--stretch {{ align-items: stretch; }}
.ui-grid {{ display: grid; gap: var(--space-md); }}
.ui-navbar {{ display: flex; align-items: center; gap: var(--space-md); min-height: {comp["navbar"]["height"]};
  padding: 0 var(--space-md); background: var(--color-surface); border-bottom: 1px solid var(--color-border); }}
.ui-navbar--dark, .ui-sidebar--dark {{ background: var(--color-navDark); color: #fff; }}
.ui-navbar__brand {{ font-weight: 700; }}
.ui-sidebar {{ width: {comp["sidebar"]["width"]}; flex-shrink: 0; padding: var(--space-md);
  background: var(--color-surface); border-right: 1px solid var(--color-border); }}
.ui-card {{ background: var(--color-surface); border-radius: {comp["card"]["radius"]}; padding: {comp["card"]["padding"]};
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08); flex: 1 1 0; min-width: 0; }}
.ui-card--outlined {{ box-shadow: none; border: 1px solid var(--color-border); }}
.ui-card__title, .ui-form__title {{ font-weight: 600; margin-bottom: var(--space-sm); }}
.ui-form {{ display: flex; flex-direction: column; gap: var(--space-md); }}
.ui-heading {{ margin: 0; }}
.ui-text {{ margin: 0; }} .ui-text--muted {{ color: var(--color-muted); }} .ui-text--error {{ color: var(--color-danger); }}
.ui-link {{ color: var(--color-primary); }}
.ui-button {{ height: {comp["button"]["height"]}; border-radius: {comp["button"]["radius"]};
  padding: 0 {comp["button"]["paddingX"]}; border: 1px solid transparent; font: inherit; cursor: pointer; }}
.ui-button--primary {{ background: var(--color-primary); color: #fff; }}
.ui-button--secondary {{ background: var(--color-surface); color: var(--color-text); border-color: var(--color-border); }}
.ui-button--danger {{ background: var(--color-danger); color: #fff; }}
.ui-button--ghost {{ background: transparent; color: var(--color-primary); }}
.ui-field {{ display: flex; flex-direction: column; gap: var(--space-xs); }}
.ui-input {{ height: {comp["input"]["height"]}; border-radius: {comp["input"]["radius"]}; border: 1px solid var(--color-border);
  padding: 0 var(--space-sm); font: inherit; background: var(--color-surface); }}
.ui-textarea {{ height: auto; min-height: 96px; padding: var(--space-sm); }}
.ui-check {{ display: flex; align-items: center; gap: var(--space-sm); }}
.ui-image {{ aspect-ratio: {comp["image"]["aspectRatio"]}; width: 100%; border-radius: var(--radius-md);
  background: var(--color-border); color: var(--color-muted); display: grid; place-items: center; text-align: center; }}
.ui-metric__label {{ color: var(--color-muted); font-size: 0.9em; }}
.ui-metric__value {{ font-size: 1.6em; font-weight: 600; }}
.ui-table-wrap {{ overflow-x: auto; }}
.ui-table {{ width: 100%; border-collapse: collapse; }}
.ui-table th, .ui-table td {{ text-align: left; padding: var(--space-sm); border-bottom: 1px solid var(--color-border);
  white-space: nowrap; }}
.ui-table caption {{ text-align: left; font-weight: 600; padding-bottom: var(--space-sm); }}
.ui-list {{ margin: 0; padding-left: var(--space-lg); }}
.ui-list--nav {{ list-style: none; padding: 0; display: flex; flex-direction: column; gap: var(--space-xs); }}
.ui-tabs {{ display: flex; gap: var(--space-xs); border-bottom: 1px solid var(--color-border); }}
.ui-tab {{ border: 0; background: none; padding: var(--space-sm) var(--space-md); cursor: pointer; font: inherit; }}
.ui-tab--active {{ border-bottom: 2px solid var(--color-primary); color: var(--color-primary); }}
.ui-badge {{ display: inline-block; padding: 2px var(--space-sm); border-radius: 999px; font-size: 0.85em; background: var(--color-border); }}
.ui-badge--success {{ background: var(--color-success); color: #fff; }}
.ui-badge--warning {{ background: var(--color-warning); color: #fff; }}
.ui-badge--error {{ background: var(--color-danger); color: #fff; }}
.ui-divider {{ border: 0; border-top: 1px solid var(--color-border); width: 100%; }}
.ui-chart {{ min-height: 180px; border-radius: var(--radius-md); background: repeating-linear-gradient(45deg,
  var(--color-background), var(--color-background) 10px, var(--color-border) 10px, var(--color-border) 20px);
  display: grid; place-items: center; color: var(--color-muted); }}
.ui-placeholder {{ border: 2px dashed var(--color-warning); border-radius: var(--radius-md); padding: var(--space-md);
  color: var(--color-warning); }}

/* Narrow screens: layout adapts, design-token values stay fixed */
@media (max-width: 900px) {{
  .ui-stack--row:has(> .ui-sidebar) {{ flex-direction: column; }}
  .ui-sidebar {{ width: 100%; border-right: 0; border-bottom: 1px solid var(--color-border); }}
  .ui-grid--cols-4, .ui-grid--cols-5, .ui-grid--cols-6 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 600px) {{
  .ui-stack--row {{ flex-wrap: wrap; }}
  .ui-grid {{ grid-template-columns: minmax(0, 1fr); }}
  .ui-app {{ padding: var(--space-sm); }}
}}""")
    return "\n".join(lines) + "\n"


def compile_react(doc):
    return {"App.jsx": compile_jsx(doc), "styles.css": compile_css()}
