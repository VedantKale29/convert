// React hook: submit a screenshot to the Node BFF, follow live progress, fetch the result.
// Talks ONLY to your own backend (same origin, your session cookie) - never to the Python service.
import { useCallback, useEffect, useRef, useState } from "react";

export function useUiGeneration(apiBase = "/api/uigen") {
  const [state, setState] = useState({ phase: "idle", jobId: null, events: [], result: null, error: null });
  const sourceRef = useRef(null);

  const close = () => {
    if (sourceRef.current) sourceRef.current.close();
    sourceRef.current = null;
  };
  useEffect(() => close, []); // stop streaming when the component unmounts

  const start = useCallback(
    async (file, { requirement = "", framework = "react", config = {} } = {}) => {
      close();
      setState({ phase: "uploading", jobId: null, events: [], result: null, error: null });
      const form = new FormData();
      form.append("image", file);
      form.append("requirement", requirement);
      form.append("framework", framework);
      for (const [k, v] of Object.entries(config)) form.append(k, typeof v === "string" ? v : JSON.stringify(v));

      let job;
      try {
        const res = await fetch(`${apiBase}/generations`, { method: "POST", body: form, credentials: "same-origin" });
        job = await res.json();
        if (res.status !== 202) throw new Error(job.error || `request failed (${res.status})`);
      } catch (err) {
        setState((s) => ({ ...s, phase: "error", error: err.message }));
        return;
      }
      setState((s) => ({ ...s, phase: "running", jobId: job.id }));

      const finish = async () => {
        close();
        try {
          const res = await fetch(`${apiBase}/generations/${job.id}`, { credentials: "same-origin" });
          const body = await res.json();
          if (body.status === "failed") throw new Error(body.error || "generation failed");
          setState((s) => ({ ...s, phase: "done", result: body.result }));
        } catch (err) {
          setState((s) => ({ ...s, phase: "error", error: err.message }));
        }
      };
      const source = new EventSource(`${apiBase}/generations/${job.id}/events`, { withCredentials: true });
      sourceRef.current = source;
      source.onmessage = (msg) => setState((s) => ({ ...s, events: [...s.events, JSON.parse(msg.data)] }));
      source.addEventListener("end", finish);
      source.onerror = () => {
        // The stream can drop (proxy timeout, network). The job keeps running server-side, so fall back
        // to polling the status until it finishes.
        close();
        const poll = async () => {
          const res = await fetch(`${apiBase}/generations/${job.id}`, { credentials: "same-origin" });
          const body = await res.json();
          if (body.status === "done" || body.status === "failed") finish();
          else setTimeout(poll, 2000);
        };
        poll();
      };
    },
    [apiBase],
  );

  return { ...state, start, previewUrl: state.jobId ? `${apiBase}/generations/${state.jobId}/preview` : null,
           downloadUrl: state.jobId ? `${apiBase}/generations/${state.jobId}/download` : null };
}
