// Node BFF for the uigen service.
//   Browser (React) --cookie/session--> this BFF --X-API-Key--> Python service (service/api.py)
// Responsibilities: authenticate the user, remember which user owns which job, rate-limit per user,
// keep the service key secret, and pass progress events (SSE) straight through.
import express from "express";
import multer from "multer";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UIGEN_URL = process.env.UIGEN_URL ?? "http://127.0.0.1:8000";
const UIGEN_API_KEY = process.env.UIGEN_API_KEY;
const PORT = Number(process.env.PORT ?? 3001);
const RATE_LIMIT = Number(process.env.RATE_LIMIT_PER_MINUTE ?? 10);
const JOB_ID = /^job_[0-9a-f]{16}$/;
const ALLOWED_CONFIG = ["image_detail", "max_ir_repairs", "fidelity_repair", "fidelity_threshold"];

if (!UIGEN_API_KEY) {
  console.error("UIGEN_API_KEY is not set");
  process.exit(1);
}

// ---------------------------------------------------------------- auth (replace with your SSO)
// In your app this is the existing session/SSO middleware. The demo trusts an x-demo-user header
// so the tests can act as two different users. NEVER ship this demo version.
export function requireUser(req, res, next) {
  const user = req.get("x-demo-user") ?? "demo-user";
  req.user = { id: user };
  next();
}

// ---------------------------------------------------------------- ownership + rate limit
// In-memory for the demo. With more than one Node instance, keep these in Redis or your DB.
const owners = new Map(); // jobId -> userId
const recent = new Map(); // userId -> timestamps of recent submissions

function rateLimited(userId) {
  const now = Date.now();
  const stamps = (recent.get(userId) ?? []).filter((t) => now - t < 60_000);
  if (stamps.length >= RATE_LIMIT) return true;
  stamps.push(now);
  recent.set(userId, stamps);
  return false;
}

function ownJob(req, res) {
  const { id } = req.params;
  if (!JOB_ID.test(id) || owners.get(id) !== req.user.id) {
    res.status(404).json({ error: "generation not found" }); // same answer for "not yours"
    return null;
  }
  return id;
}

function toBff(links) {
  // Rewrite service URLs (/v1/generations/..) to BFF URLs (/api/uigen/generations/..)
  return Object.fromEntries(Object.entries(links ?? {}).map(([k, v]) => [k, v.replace(/^\/v1\//, "/api/uigen/")]));
}

async function upstream(pathname, init = {}) {
  return fetch(`${UIGEN_URL}${pathname}`, {
    ...init,
    headers: { ...(init.headers ?? {}), "X-API-Key": UIGEN_API_KEY },
  });
}

// ---------------------------------------------------------------- routes
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024, files: 1 },
  fileFilter: (_req, file, cb) => cb(null, ["image/png", "image/jpeg", "image/webp"].includes(file.mimetype)),
});

export const router = express.Router();
router.use(requireUser);

router.post("/generations", upload.single("image"), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "attach a PNG, JPEG or WEBP image as 'image'" });
  if (rateLimited(req.user.id)) {
    return res.set("Retry-After", "60").status(429).json({ error: "too many generations, wait a minute" });
  }
  const config = {};
  for (const key of ALLOWED_CONFIG) {
    if (req.body[key] === undefined || req.body[key] === "") continue;
    config[key] = key === "image_detail" ? req.body[key] : JSON.parse(req.body[key]);
  }
  const form = new FormData();
  form.append("image", new Blob([req.file.buffer], { type: req.file.mimetype }), req.file.originalname || "screen.png");
  form.append("requirement", String(req.body.requirement ?? "").slice(0, 2000));
  form.append("framework", String(req.body.framework ?? "react"));
  form.append("config", JSON.stringify(config));
  try {
    const r = await upstream("/v1/generations", { method: "POST", body: form });
    const body = await r.json();
    if (r.status === 202) owners.set(body.id, req.user.id);
    if (r.headers.get("retry-after")) res.set("Retry-After", r.headers.get("retry-after"));
    return res.status(r.status).json(r.status === 202 ? { ...body, links: toBff(body.links) } : { error: body.detail });
  } catch {
    return res.status(502).json({ error: "generation service unavailable" });
  }
});

router.get("/generations/:id", async (req, res) => {
  const id = ownJob(req, res);
  if (!id) return;
  try {
    const r = await upstream(`/v1/generations/${id}`);
    const body = await r.json();
    return res.status(r.status).json({ ...body, links: toBff(body.links) });
  } catch {
    return res.status(502).json({ error: "generation service unavailable" });
  }
});

router.get("/generations/:id/events", async (req, res) => {
  const id = ownJob(req, res);
  if (!id) return;
  const abort = new AbortController();
  req.on("close", () => abort.abort()); // browser left: stop reading upstream
  try {
    const r = await upstream(`/v1/generations/${id}/events`, { signal: abort.signal });
    res.status(r.status).set({ "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" });
    res.flushHeaders();
    const reader = r.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(value);
    }
    res.end();
  } catch {
    if (!res.headersSent) res.status(502).json({ error: "generation service unavailable" });
    else res.end();
  }
});

// Binary/HTML artifacts: pass through status, body and the security headers (CSP sandbox for the preview).
for (const [route, name] of [["preview", "preview"], ["render.png", "render.png"], ["download", "download"]]) {
  router.get(`/generations/:id/${route}`, async (req, res) => {
    const id = ownJob(req, res);
    if (!id) return;
    try {
      const r = await upstream(`/v1/generations/${id}/${name}`);
      for (const h of ["content-type", "content-security-policy", "x-content-type-options", "referrer-policy",
                       "content-disposition", "cache-control"]) {
        if (r.headers.get(h)) res.set(h, r.headers.get(h));
      }
      return res.status(r.status).send(Buffer.from(await r.arrayBuffer()));
    } catch {
      return res.status(502).json({ error: "generation service unavailable" });
    }
  });
}

// ---------------------------------------------------------------- app
export function createServer({ staticDir } = {}) {
  const app = express();
  app.disable("x-powered-by");
  app.use("/api/uigen", router);
  app.use((err, _req, res, _next) => {
    // multer size limit, bad JSON in config, etc.
    const status = err.code === "LIMIT_FILE_SIZE" ? 413 : 400;
    res.status(status).json({ error: status === 413 ? "image larger than 10 MB" : "invalid request" });
  });
  if (staticDir) app.use(express.static(staticDir));
  return app;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const staticDir = process.env.STATIC_DIR ? path.resolve(process.env.STATIC_DIR) : undefined;
  createServer({ staticDir }).listen(PORT, "127.0.0.1", () => console.log(`BFF on http://127.0.0.1:${PORT}`));
}
