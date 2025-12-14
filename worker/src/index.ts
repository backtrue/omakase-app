export interface Env {
  UPSTREAM_CLOUD_RUN_URL: string;
  CORS_ALLOW_ORIGIN?: string;
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

    // Route policy:
    // - /api/* => proxy to Cloud Run
    // - /assets/* => proxy to Cloud Run (v1). Later you can serve from R2.
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
