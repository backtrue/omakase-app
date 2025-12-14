# Omakase v1 – Storage Spec (Cloudflare R2)

## Goals
- Store generated watercolor images for top-3 menu items.
- Serve images via the same public domain: `omakase.thinkwithblack.com`.

## Bucket
- Name: `omakase-assets`
- Access (v1 recommendation): public read OR Worker-mediated read.

## Object Key Convention
Generated image key:
- `gen/<session_id>/<item_id>.jpg`

Optional future variants:
- `gen/<session_id>/<item_id>@2x.jpg`
- `gen/<session_id>/<item_id>.webp`

## Public URL Convention
Expose as:
- `https://omakase.thinkwithblack.com/assets/gen/<session_id>/<item_id>.jpg`

Worker handling of `/assets/*`:
- Option A (fast demo): redirect to R2 public URL
- Option B (more private): Worker fetches from R2 and streams bytes

## Content-Type
- `image/jpeg` (v1)

## Cache Headers
For generated assets:
- `Cache-Control: public, max-age=31536000, immutable`

## Deletion / Retention
v1 default:
- keep assets indefinitely during MVP
Future:
- apply TTL-based cleanup by prefix and `created_at` metadata

## Security Notes
- Do not embed secrets in object keys.
- If switching to private bucket + signed URLs later, keep the `image_url` stable by having Worker generate signed fetch internally.
