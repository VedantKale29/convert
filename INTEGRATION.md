# Integrating with your React + Node app

## 1. Run the generation API (Python)
```powershell
# .env: UIGEN_API_KEYS=<long random key for the BFF>, OPENAI_API_KEY=..., ALLOW_EXTERNAL_LLM=true (mock data only)
.\tasks.ps1 api                      # http://127.0.0.1:8000  (keep it private: not reachable from browsers)
```
Health for your load balancer: `GET /v1/health` (liveness), `GET /v1/ready` (toolchain, quality, LLM allowed).

## 2. Add the BFF routes to your Express backend
Copy `integrations/node-bff/server.js` into your backend (or mount its `router`):
```js
import { router as uigenRouter } from "./uigen-bff.js";
app.use("/api/uigen", yourAuthMiddleware, uigenRouter);   // replace the demo requireUser with your SSO
```
Environment: `UIGEN_URL`, `UIGEN_API_KEY`, `RATE_LIMIT_PER_MINUTE`.
**Must change before production:** the demo `requireUser` trusts an `x-demo-user` header. Replace it with your
existing session/SSO check, and move the `owners` / `recent` maps to Redis or your DB if you run more than one
Node instance.

Routes the React app uses (all under your origin):
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/uigen/generations` | multipart `image`, `requirement`, optional `fidelity_repair`, `image_detail`, ... → 202 `{id, links}` |
| GET | `/api/uigen/generations/:id` | status + result (IR, `files["App.jsx"]`, quality, warnings, usage) |
| GET | `/api/uigen/generations/:id/events` | Server-Sent Events progress (`event: end` when finished) |
| GET | `/api/uigen/generations/:id/preview` | generated page (CSP-sandboxed) for an `<iframe>` |
| GET | `/api/uigen/generations/:id/download` | zip with IR, React code, preview, quality report |

## 3. Use it in React
Copy `integrations/react/src/` into your app:
```jsx
import UiGenerator from "./uigen/UiGenerator.jsx";   // ready-made UI
<UiGenerator apiBase="/api/uigen" />

// or build your own UI on the hook:
import { useUiGeneration } from "./uigen/useUiGeneration.js";
const gen = useUiGeneration("/api/uigen");
gen.start(file, { requirement, config: { fidelity_repair: true } });
// gen.phase: idle | uploading | running | done | error;  gen.events;  gen.result;  gen.previewUrl
```
The hook streams progress with EventSource and falls back to polling if the stream drops (proxies, VPN).
Show the preview in an iframe with `sandbox="allow-scripts allow-forms"` (never `allow-same-origin`).

## 4. Typed client (optional)
`contracts/openapi.json` is the API contract. For TypeScript types in Node:
`npx openapi-typescript contracts/openapi.json -o src/uigen-api.d.ts`

## 5. Using the output in your codebase
`result.files["App.jsx"]` + `styles.css` are plain React (only `react` imported). `result.ir` is the
framework-neutral description; an exporter to your team's rule JSON (`models.ts`) is the next step so your
existing Angular/Vue generator can consume it too.

## Checked by tests
`tests/test_api.py` (auth, ownership, validation, 429, no leaks, docs hidden, contract drift) and
`tests/test_integration_e2e.py` (real Python API + Node BFF + React in Chromium: per-user ownership,
413/400/429/502 handling, full UI flow).
