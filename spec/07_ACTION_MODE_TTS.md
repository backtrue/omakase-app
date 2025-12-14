# Omakase v1 – Action Mode + TTS Spec

## Goal
- Provide a "point-and-order" mode for communicating with restaurant staff.

## UX requirements (v1)
- Tap an item to enter Action Mode.
- Full-screen display:
  - Large `original_name` (Japanese)
  - `romanji` (if available)
- Provide a button to play Japanese TTS.

## Data requirements
- Backend `menu_data.items[]` must include:
  - `original_name` (required)
  - `romanji` (recommended; empty string allowed)

## TTS behavior (iOS)
- Use system TTS (AVFoundation).
- Language: `ja-JP`.
- If TTS fails:
  - show a non-blocking error message.

## Telemetry (recommended)
- Log TTS start/stop and any errors to the debug panel.
