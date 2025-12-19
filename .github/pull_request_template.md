# Summary

Describe what this PR does and why.

# Type of change

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor / cleanup
- [ ] Docs-only
- [ ] Infra / ops

# Normative contract / spec impact (required)

If this PR affects any of the following, you **must** update the relevant `spec/*` document(s) in the same PR:

- Public API surface (`/api/v1/...`)
- SSE event types / ordering / payload schema
- Asset URL format under `https://omakase.thinkwithblack.com/assets/...`
- Persistence semantics (e.g., job/event storage, TTL semantics)

## Spec checklist

- [ ] No public contract changes (API/SSE/assets/persistence)
- [ ] OR: I updated the relevant normative spec(s) in this PR

### Normative specs (source of truth)

- [ ] `spec/00_ARCHITECTURE.md`
- [ ] `spec/01_API_SSE.md`
- [ ] `spec/02_STORAGE_R2.md`
- [ ] `spec/03_CACHE_VECTORIZE.md`
- [ ] `spec/04_SECRETS_ENV.md`

# Compatibility rules (v1)

- [ ] Changes are additive only (new optional fields / new event types); existing fields and meanings preserved
- [ ] If breaking change is required, I proposed/implemented `/api/v2/...` and included a migration plan
- [ ] If deprecating, I marked deprecation + target removal window in `spec/01_API_SSE.md`

# Testing

- [ ] Manual test performed
- [ ] Automated tests added/updated (if applicable)
- [ ] No tests needed (explain):

# Deployment notes

- [ ] No deploy impact
- [ ] Requires deploy/config change (describe):

# Screenshots / recordings (if UI)

Attach before/after if applicable.
