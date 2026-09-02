import crypto from "node:crypto";

const SESSION_TTL = 8 * 60 * 60;
const ALLOWED_GET = new Set(["/api/health", "/api/groups"]);

function secret(name) {
  return (process.env[name] || "").trim();
}

function sign(value) {
  return crypto.createHmac("sha256", secret("GHOSTEA_SESSION_SECRET"))
    .update(value)
    .digest("base64url");
}

function makeSession() {
  const payload = `${Date.now()}:${crypto.randomBytes(24).toString("base64url")}`;
  return `${Buffer.from(payload).toString("base64url")}.${sign(payload)}`;
}

function validSession(req) {
  const cookie = req.headers.cookie || "";
  const match = cookie.match(/(?:^|;\s*)ghostea_session=([^;]+)/);
  if (!match || !secret("GHOSTEA_SESSION_SECRET")) return false;

  const raw = match[1];
  const parts = raw.split(".");
  if (parts.length !== 2) return false;

  let payload;
  try {
    payload = Buffer.from(parts[0], "base64url").toString("utf8");
  } catch {
    return false;
  }

  const [issued] = payload.split(":");
  const issuedAt = Number(issued);
  if (!Number.isFinite(issuedAt) || Date.now() - issuedAt > SESSION_TTL * 1000) {
    return false;
  }

  const expected = sign(payload);
  const actual = parts[1];
  if (expected.length !== actual.length) return false;

  return crypto.timingSafeEqual(
    Buffer.from(expected),
    Buffer.from(actual)
  );
}

function json(res, status, body, extra = {}) {
  res.status(status).setHeader("Content-Type", "application/json");
  Object.entries(extra).forEach(([k, v]) => res.setHeader(k, v));
  return res.end(JSON.stringify(body));
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    let size = 0;
    req.on("data", chunk => {
      size += chunk.length;
      if (size > 16 * 1024) {
        reject(new Error("payload_too_large"));
        req.destroy();
        return;
      }
      raw += chunk;
    });
    req.on("end", () => {
      try { resolve(raw ? JSON.parse(raw) : {}); }
      catch { reject(new Error("invalid_json")); }
    });
    req.on("error", reject);
  });
}

export default async function handler(req, res) {
  const target = secret("GHOSTEA_API_URL").replace(/\/+$/, "");
  if (!target || !secret("GHOSTEA_API_KEY")) {
    return json(res, 500, { error: "server_not_configured" });
  }

  if (req.method === "POST" && req.query.action === "login") {
    const body = await parseBody(req).catch(() => null);
    if (!body || typeof body.password !== "string") {
      return json(res, 400, { error: "password_required" });
    }

    const expected = secret("GHOSTEA_ADMIN_PASSWORD");
    if (!expected) return json(res, 500, { error: "server_not_configured" });

    const a = Buffer.from(body.password);
    const b = Buffer.from(expected);
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
      return json(res, 401, { error: "invalid_credentials" });
    }

    const session = makeSession();
    res.setHeader(
      "Set-Cookie",
      `ghostea_session=${session}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_TTL}`
    );
    return json(res, 200, { ok: true });
  }

  if (req.method === "POST" && req.query.action === "logout") {
    res.setHeader(
      "Set-Cookie",
      "ghostea_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"
    );
    return json(res, 200, { ok: true });
  }

  if (!validSession(req)) {
    return json(res, 401, { error: "login_required" });
  }

  const requested = typeof req.query.path === "string" ? req.query.path : "";
  if (!requested.startsWith("/api/")) {
    return json(res, 400, { error: "invalid_path" });
  }

  // Parse the requested path separately from its query string so analytics
  // requests such as /api/groups/<id>/analytics?days=30 remain allowlisted.
  let parsedPath;
  try {
    parsedPath = new URL(requested, target);
  } catch {
    return json(res, 400, { error: "invalid_path" });
  }

  const pathname = parsedPath.pathname;
  const match = pathname.match(/^\/api\/groups\/(-?\d+)\/(settings|analytics|logs|filters|risk)$/);
  const filterDelete = pathname.match(/^\/api\/groups\/(-?\d+)\/filters\/(\d+)$/);
  const userProfile = pathname.match(/^\/api\/groups\/(-?\d+)\/users\/(-?\d+)\/profile$/);
  const userList = pathname.match(/^\/api\/groups\/(-?\d+)\/users$/);
  const userAction = pathname.match(/^\/api\/groups\/(-?\d+)\/users$/);
  const allowed = ALLOWED_GET.has(pathname) || Boolean(match) || Boolean(filterDelete) || Boolean(userProfile) || Boolean(userList) || Boolean(userAction);
  if (!allowed) return json(res, 404, { error: "not_found" });

  if (!["GET", "PATCH", "POST", "DELETE"].includes(req.method)) {
    return json(res, 405, { error: "method_not_allowed" });
  }

  if (req.method === "PATCH" && !(match && pathname.endsWith("/settings"))) {
    return json(res, 405, { error: "method_not_allowed" });
  }
  if (req.method === "POST" && !((match && pathname.endsWith("/filters")) || userAction)) {
    return json(res, 405, { error: "method_not_allowed" });
  }
  if (req.method === "DELETE" && !filterDelete) {
    return json(res, 405, { error: "method_not_allowed" });
  }

  const url = new URL(target + pathname);
  for (const [key, value] of parsedPath.searchParams.entries()) {
    url.searchParams.set(key, value);
  }
  for (const [key, value] of Object.entries(req.query)) {
    if (key !== "path" && typeof value === "string") {
      url.searchParams.set(key, value);
    }
  }

  const headers = {
    Authorization: `Bearer ${secret("GHOSTEA_API_KEY")}`,
    Accept: "application/json",
  };

  let body;
  if (req.method === "PATCH" || req.method === "POST") {
    const parsed = await parseBody(req).catch(() => null);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return json(res, 400, { error: "object_required" });
    }
    body = JSON.stringify(parsed);
    headers["Content-Type"] = "application/json";
  }

  try {
    const upstream = await fetch(url, {
      method: req.method,
      headers,
      body,
      redirect: "error",
    });

    const text = await upstream.text();
    let payload;
    try { payload = JSON.parse(text); }
    catch { payload = { error: "upstream_invalid_response" }; }

    return json(res, upstream.status, payload, {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
  } catch {
    return json(res, 502, { error: "ghostea_unreachable" });
  }
}
