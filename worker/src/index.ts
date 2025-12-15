type D1Database = any;
type D1PreparedStatement = any;

export interface Env {
  UPSTREAM_CLOUD_RUN_URL: string;
  CORS_ALLOW_ORIGIN?: string;
  INTERNAL_API_TOKEN?: string;
  DB?: D1Database;
}

function corsHeaders(env: Env): Record<string, string> {
  const origin = env.CORS_ALLOW_ORIGIN ?? "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Accept,Authorization",
    "Access-Control-Max-Age": "86400",
  };
}

function normalizeUpstream(base: string): string {
  const trimmed = (base ?? "").trim();
  if (!trimmed) return "";
  return trimmed.endsWith("/") ? trimmed.slice(0, -1) : trimmed;
}

function isHopByHopHeader(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n === "connection" ||
    n === "keep-alive" ||
    n === "proxy-authenticate" ||
    n === "proxy-authorization" ||
    n === "te" ||
    n === "trailer" ||
    n === "transfer-encoding" ||
    n === "upgrade"
  );
}

function copyRequestHeaders(req: Request): Headers {
  const headers = new Headers();
  req.headers.forEach((v, k) => {
    if (isHopByHopHeader(k)) return;
    // Cloudflare will set Host and other connection-specific headers.
    if (k.toLowerCase() === "host") return;
    headers.set(k, v);
  });
  return headers;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }

    const upstreamBase = normalizeUpstream(env.UPSTREAM_CLOUD_RUN_URL);
    if (!upstreamBase) {
      return new Response(
        "UPSTREAM_CLOUD_RUN_URL is not set. Configure it in worker/wrangler.toml or Cloudflare dashboard.",
        { status: 500, headers: { ...corsHeaders(env) } }
      );
    }

    async function ensureSchema(): Promise<void> {
      if (!env.DB) {
        throw new Error("D1 binding DB is not configured");
      }

      await env.DB.prepare(
        "CREATE TABLE IF NOT EXISTS dish_knowledge (dish_key TEXT NOT NULL, language TEXT NOT NULL, translated_name TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', romanji TEXT NOT NULL DEFAULT '', seen_count INTEGER NOT NULL DEFAULT 0, last_seen_at TEXT NOT NULL DEFAULT '', source_scan_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (dish_key, language))"
      ).run();
      await env.DB.prepare(
        "CREATE TABLE IF NOT EXISTS scan_records (scan_id TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT '', image_hash_sha256 TEXT NOT NULL, language TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]')"
      ).run();
      await env.DB.prepare("CREATE INDEX IF NOT EXISTS idx_scan_records_hash ON scan_records(image_hash_sha256)").run();
    }

    function requireInternalAuth(): Response | null {
      const expected = (env.INTERNAL_API_TOKEN ?? "").trim();
      const got = (request.headers.get("x-internal-token") ?? "").trim();
      if (!expected || got !== expected) {
        return new Response("Unauthorized", { status: 401, headers: corsHeaders(env) });
      }
      return null;
    }

    async function handleInternal(): Promise<Response> {
      const authErr = requireInternalAuth();
      if (authErr) return authErr;

      if (!env.DB) {
        return new Response("D1 binding DB is not configured", { status: 500, headers: corsHeaders(env) });
      }

      await ensureSchema();

      if (request.method !== "POST") {
        return new Response("Method Not Allowed", { status: 405, headers: corsHeaders(env) });
      }

      if (url.pathname === "/internal/dish_knowledge/fetch") {
        const body = (await request.json()) as { dish_keys?: unknown; language?: unknown };
        const language = typeof body.language === "string" ? body.language : "";
        const keys = Array.isArray(body.dish_keys)
          ? body.dish_keys
              .filter((k) => typeof k === "string")
              .map((k) => (k as string).trim())
              .filter((k) => k)
          : [];

        if (!language || keys.length === 0) {
          return Response.json({ items: {} }, { headers: corsHeaders(env) });
        }

        const placeholders = keys.map(() => "?").join(",");
        const stmt = env.DB.prepare(
          `SELECT dish_key, translated_name, description, tags, romanji, seen_count FROM dish_knowledge WHERE language = ? AND dish_key IN (${placeholders})`
        ).bind(language, ...keys);

        const rows = (await stmt.all()).results as Array<Record<string, unknown>>;
        const out: Record<string, unknown> = {};
        for (const r of rows) {
          const dish_key = typeof r.dish_key === "string" ? (r.dish_key as string) : "";
          if (!dish_key) continue;
          let tags: unknown[] = [];
          const rawTags = typeof r.tags === "string" ? (r.tags as string) : "[]";
          try {
            const parsed = JSON.parse(rawTags);
            if (Array.isArray(parsed)) tags = parsed;
          } catch {
            tags = [];
          }
          out[dish_key] = {
            dish_key,
            translated_name: typeof r.translated_name === "string" ? (r.translated_name as string) : "",
            description: typeof r.description === "string" ? (r.description as string) : "",
            tags,
            romanji: typeof r.romanji === "string" ? (r.romanji as string) : "",
            seen_count: typeof r.seen_count === "number" ? (r.seen_count as number) : Number(r.seen_count ?? 0),
          };
        }

        return Response.json({ items: out }, { headers: corsHeaders(env) });
      }

      if (url.pathname === "/internal/dish_knowledge/upsert_many") {
        const body = (await request.json()) as {
          rows?: unknown;
          language?: unknown;
          source_scan_id?: unknown;
        };
        const language = typeof body.language === "string" ? body.language : "";
        const source_scan_id = typeof body.source_scan_id === "string" ? body.source_scan_id : "";
        const rows = Array.isArray(body.rows) ? (body.rows as Array<Record<string, unknown>>) : [];

        if (!language || rows.length === 0) {
          return new Response(null, { status: 204, headers: corsHeaders(env) });
        }

        const sql =
          "INSERT INTO dish_knowledge (dish_key, language, translated_name, description, tags, romanji, seen_count, last_seen_at, source_scan_id) VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'), ?) ON CONFLICT(dish_key, language) DO UPDATE SET translated_name = CASE WHEN dish_knowledge.translated_name = '' THEN excluded.translated_name ELSE dish_knowledge.translated_name END, description = CASE WHEN dish_knowledge.description = '' THEN excluded.description ELSE dish_knowledge.description END, tags = CASE WHEN dish_knowledge.tags = '[]' THEN excluded.tags ELSE dish_knowledge.tags END, romanji = CASE WHEN dish_knowledge.romanji = '' THEN excluded.romanji ELSE dish_knowledge.romanji END, seen_count = dish_knowledge.seen_count + 1, last_seen_at = datetime('now'), source_scan_id = CASE WHEN excluded.source_scan_id = '' THEN dish_knowledge.source_scan_id ELSE excluded.source_scan_id END";

        const batch: D1PreparedStatement[] = [];
        for (const r of rows) {
          const dish_key = typeof r.dish_key === "string" ? r.dish_key.trim() : "";
          if (!dish_key) continue;
          const translated_name = typeof r.translated_name === "string" ? r.translated_name : "";
          const description = typeof r.description === "string" ? r.description : "";
          const romanji = typeof r.romanji === "string" ? r.romanji : "";
          const tagsArr = Array.isArray(r.tags) ? r.tags : [];
          const tags = JSON.stringify(tagsArr.filter((t) => typeof t === "string" && t.trim()));
          batch.push(env.DB.prepare(sql).bind(dish_key, language, translated_name, description, tags, romanji, source_scan_id));
        }

        if (batch.length > 0) {
          await env.DB.batch(batch);
        }

        return new Response(null, { status: 204, headers: corsHeaders(env) });
      }

      if (url.pathname === "/internal/scan_records/insert") {
        const body = (await request.json()) as {
          scan_id?: unknown;
          image_hash_sha256?: unknown;
          language?: unknown;
          items?: unknown;
        };
        const scan_id = typeof body.scan_id === "string" ? body.scan_id : "";
        const image_hash_sha256 = typeof body.image_hash_sha256 === "string" ? body.image_hash_sha256 : "";
        const language = typeof body.language === "string" ? body.language : "";
        const items = Array.isArray(body.items) ? body.items : [];

        if (!scan_id || !language) {
          return new Response(null, { status: 204, headers: corsHeaders(env) });
        }

        const created_at = new Date().toISOString();
        await env.DB.prepare(
          "INSERT OR IGNORE INTO scan_records (scan_id, created_at, image_hash_sha256, language, items) VALUES (?, ?, ?, ?, ?)"
        ).bind(scan_id, created_at, image_hash_sha256, language, JSON.stringify(items)).run();

        return new Response(null, { status: 204, headers: corsHeaders(env) });
      }

      return new Response("Not Found", { status: 404, headers: corsHeaders(env) });
    }

    if (url.pathname.startsWith("/internal/")) {
      return await handleInternal();
    }

    // Route policy:
    // - /api/* => proxy to Cloud Run
    // - /assets/* => proxy to Cloud Run (v1)
    // - /healthz => proxy to Cloud Run
    // - else => 404
    const isApi = url.pathname.startsWith("/api/");
    const isAssets = url.pathname.startsWith("/assets/");
    const isHealth = url.pathname === "/health" || url.pathname === "/healthz";

    if (!isApi && !isAssets && !isHealth) {
      return new Response("Not Found", { status: 404, headers: corsHeaders(env) });
    }

    const upstreamPath = url.pathname === "/healthz" ? "/health" : url.pathname;
    const upstreamUrl = new URL(upstreamBase + upstreamPath + url.search);

    const headers = copyRequestHeaders(request);

    // Important for SSE: ask upstream for streaming response.
    // Keep Accept header if client provided it.
    if (!headers.has("accept") && isApi) {
      headers.set("Accept", "text/event-stream");
    }

    const init: RequestInit = {
      method: request.method,
      headers,
      redirect: "manual",
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = request.body;
    }

    const upstreamResp = await fetch(upstreamUrl.toString(), init);

    // Copy response headers (excluding hop-by-hop). Ensure SSE isn't buffered.
    const respHeaders = new Headers();
    upstreamResp.headers.forEach((value, key) => {
      if (isHopByHopHeader(key)) return;
      respHeaders.set(key, value);
    });

    // CORS for web debugging
    const c = corsHeaders(env);
    for (const [k, v] of Object.entries(c)) respHeaders.set(k, v);

    // SSE best-practice headers
    const ct = respHeaders.get("content-type") ?? "";
    if (ct.includes("text/event-stream")) {
      respHeaders.set("Cache-Control", "no-cache");
      respHeaders.set("Connection", "keep-alive");
      // Helps avoid buffering in some proxies.
      respHeaders.set("X-Accel-Buffering", "no");
    }

    return new Response(upstreamResp.body, {
      status: upstreamResp.status,
      statusText: upstreamResp.statusText,
      headers: respHeaders,
    });
  },
};
