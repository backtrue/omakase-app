# Omakase v1 – RAG / Knowledge Base Spec

## Goal
- Improve translation quality and provide culinary context beyond literal translation.
- Stabilize Top-3 recommendations using a lightweight knowledge layer.

## MVP approach
- Start with a small curated dictionary / taxonomy:
  - dish name variants
  - ingredients
  - common izakaya categories
  - short cultural notes

## Integration points
Option A (v1): prompt augmentation
- Inject a small set of relevant entries into the VLM prompt.

Option B (v1.1): post-processing
- After VLM returns structured items, lookup each item and enrich:
  - `description`
  - `tags`
  - adjust `is_top3` using rules

## Top-3 policy (proposed)
- Combine signals:
  - VLM `is_top3`
  - knowledge-based must-try dishes by category
  - simple heuristics (e.g., special markings, price prominence)

## Non-goals
- Full-scale web crawling.
- Heavy vector search in v1 (can be added later).
