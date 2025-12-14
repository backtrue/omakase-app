# Omakase Worker (Proxy)

This Worker proxies:
- `/api/*` to Cloud Run (FastAPI SSE)
- `/assets/*` to Cloud Run (v1; later can be served from R2)

## Configure
Set `UPSTREAM_CLOUD_RUN_URL` in `wrangler.toml`:

- Example: `https://omakase-api-xxxxx.a.run.app`

## Local dev
```bash
npm install
npm run dev
```

## Deploy
```bash
npm run deploy
```

Then configure route:
- `omakase.thinkwithblack.com/*` -> this worker
