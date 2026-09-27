// Drop-in component: upload/paste a screenshot, watch progress, see the preview, code and quality.
import React, { useEffect, useState } from "react";
import { useUiGeneration } from "./useUiGeneration.js";

const pct = (v) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`);

export default function UiGenerator({ apiBase = "/api/uigen" }) {
  const gen = useUiGeneration(apiBase);
  const [file, setFile] = useState(null);
  const [requirement, setRequirement] = useState("");
  const [fidelityRepair, setFidelityRepair] = useState(false);
  const [tab, setTab] = useState("preview");
  const busy = gen.phase === "uploading" || gen.phase === "running";

  useEffect(() => {
    const onPaste = (e) => {
      const item = [...(e.clipboardData?.items ?? [])].find((i) => i.type.startsWith("image/"));
      if (item) setFile(item.getAsFile());
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);

  const submit = (e) => {
    e.preventDefault();
    if (file) gen.start(file, { requirement, config: { fidelity_repair: fidelityRepair } });
  };
  const r = gen.result;

  return (
    <div className="uigen">
      <form onSubmit={submit} className="uigen-form">
        <label htmlFor="uigen-file">Screenshot (or paste with Ctrl+V)</label>
        <input id="uigen-file" type="file" accept="image/png,image/jpeg,image/webp"
               onChange={(e) => setFile(e.target.files[0] ?? null)} />
        <label htmlFor="uigen-req">Requirement (optional)</label>
        <textarea id="uigen-req" value={requirement} onChange={(e) => setRequirement(e.target.value)} />
        <label><input type="checkbox" checked={fidelityRepair} onChange={(e) => setFidelityRepair(e.target.checked)} />
          {" "}Fidelity repair</label>
        <button type="submit" disabled={!file || busy}>{busy ? "Generating..." : "Generate"}</button>
      </form>

      {gen.error && <p role="alert" className="uigen-error">{gen.error}</p>}
      {gen.events.length > 0 && (
        <ol className="uigen-progress" aria-live="polite">
          {gen.events.map((ev, i) => <li key={i}><b>{ev.stage}</b>: {ev.message}</li>)}
        </ol>
      )}

      {r && (
        <section className="uigen-result">
          <p className="uigen-summary">
            Status <b>{r.status}</b> · fidelity <b>{pct(r.quality?.fidelity)}</b> · coverage {pct(r.coverage)} ·
            accessibility issues {r.quality?.a11y_violations ?? "n/a"} · tokens {r.usage.input_tokens}/{r.usage.output_tokens}
            {" "}· <a href={gen.downloadUrl}>Download files</a>
          </p>
          <div role="tablist">
            {["preview", "code", "ir", "quality"].map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>
            ))}
          </div>
          {tab === "preview" && (
            // Preview at the screenshot's width: a phone screenshot is shown at phone width, not stretched.
            <iframe title="Generated UI" src={gen.previewUrl} sandbox="allow-scripts allow-forms"
                    style={{ width: r.viewport && r.viewport[0] < 900 ? r.viewport[0] : "100%", maxWidth: "100%",
                             height: r.viewport ? Math.min(Math.max(r.viewport[1], 480), 900) : 600,
                             display: "block", margin: "0 auto", border: "1px solid #ddd" }} />
          )}
          {tab === "code" && <pre className="uigen-code">{r.files["App.jsx"]}</pre>}
          {tab === "ir" && <pre className="uigen-code">{JSON.stringify(r.ir, null, 2)}</pre>}
          {tab === "quality" && (
            <ul>
              {(r.quality?.missing_text ?? []).map((t, i) => <li key={`m${i}`}>Missing: {t}</li>)}
              {(r.quality?.extra_text ?? []).map((t, i) => <li key={`e${i}`}>Not in image: {t}</li>)}
              {r.warnings.map((w, i) => <li key={`w${i}`}>{w}</li>)}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
