# Omakase v1 – iOS Image Preprocess Spec (Deskew + Enhance)

## Goals
- Increase VLM OCR accuracy for handwritten menus by improving input image quality.
- Make capture quality predictable and testable.
- Keep upload payload within reasonable size limits while preserving legibility.

## Scope
- Applies to the iOS client before uploading `image_base64` to `POST /api/v1/scan/stream`.
- Does not change backend segmentation logic.

## Pipeline (v1)
1) Capture
- Prefer the full menu page in a single frame.
- Encourage minimal glare and adequate lighting.

2) Perspective correction (deskew)
- Detect document/menu quadrilateral when possible.
- Apply perspective transform to produce a top-down rectangle.
- Fallback behavior:
  - If detection confidence is low, skip transform and continue.

3) Enhancement
- Convert to a working color space appropriate for contrast enhancement.
- Apply a mild contrast boost.
- Apply mild sharpening to help thin strokes.
- Avoid aggressive denoise that removes brush strokes.

4) Resize + JPEG encode
- Target constraints:
  - Max dimension: configurable; default should not be lower than 1600 for handwritten menus.
  - Max bytes: configurable; default <= 1.2MB.
- Encoding notes:
  - Prefer quality-first for handwritten text; only reduce quality if size exceeds max bytes.

## Acceptance criteria
- For the same handwritten menu test image(s):
  - End-to-end parse success rate improves compared to baseline.
  - Top-level `menu_data.items` count is stable across devices.
- Visual inspection:
  - Strokes remain distinct; no obvious ringing artifacts.

## Telemetry (recommended)
- Record client-side metrics in debug logs:
  - original pixel size
  - post-transform pixel size
  - upload JPEG size (bytes)
  - whether deskew was applied

## Non-goals
- Offline OCR on device.
- Multiple-crop segmentation on device.
