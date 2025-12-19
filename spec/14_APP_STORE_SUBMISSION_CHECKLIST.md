# App Store / TestFlight Submission Checklist (Expo + EAS)

This checklist is written for this repo’s Expo/React Native app under `mobile/`.

## 0) Account & Access (P0 blocker)

- [ ] Apple Developer Program enrollment complete (paid membership)
- [ ] App Store Connect access confirmed (Admin / App Manager)
- [ ] Decide developer display name
  - [ ] Individual (personal name)
  - [ ] Organization (company name, usually requires D-U-N-S)

## 1) App identity & configuration

- [ ] Decide iOS Bundle Identifier (e.g. `com.yourname.omakase`)
- [ ] Confirm app name (App Store display name)
- [ ] Set `version` and `buildNumber` (iOS)
- [ ] Icons and splash are final and compliant
- [ ] Permissions strings are correct and user-friendly
  - [ ] Camera usage
  - [ ] Photo library usage
  - [ ] Notifications (if enabled)

## 2) EAS setup

- [ ] `eas-cli` installed and login works
- [ ] EAS project configured for `mobile/`
- [ ] `eas.json` configured
  - [ ] `production` profile (for App Store)
  - [ ] `preview` profile (optional, for internal testing)
- [ ] iOS credentials / signing configured through EAS

## 3) Build & upload

- [ ] Create iOS production build (`.ipa`) via EAS Build
- [ ] Upload to App Store Connect (via EAS submit or Transporter)
- [ ] Confirm the build appears in TestFlight
- [ ] Wait for Apple processing to finish (build status ready)

## 4) App Store Connect metadata

- [ ] App information
  - [ ] Name
  - [ ] Subtitle
  - [ ] Description
  - [ ] Keywords
  - [ ] Primary category
- [ ] Support URL
- [ ] Privacy Policy URL (required)
- [ ] Contact info

## 5) Privacy compliance (high risk for rejection if wrong)

- [ ] App Privacy questionnaire completed accurately
  - [ ] Photo / image upload usage disclosed (menu photo upload)
  - [ ] Device identifiers / push token disclosed if collected
  - [ ] Diagnostics / crash logs disclosed if collected
- [ ] Review Notes added
  - [ ] Explain app uploads menu images to server for OCR/translation
  - [ ] Mention AI-generated images are labeled with an in-app badge (“AI示意”) and have a disclaimer

## 6) Screenshots

- [ ] Prepare required iPhone screenshots (at least one device size set)
  - [ ] Scan camera screen
  - [ ] Analysis/loading screen
  - [ ] Menu list screen
  - [ ] Dish detail screen (include “AI示意” badge if AI image shown)

## 7) TestFlight QA

- [ ] Install via TestFlight on a real device
- [ ] Verify
  - [ ] Camera capture flow
  - [ ] Upload + processing
  - [ ] Streaming updates
  - [ ] TTS (prefers `reading`, falls back to `original_name`)
  - [ ] AI image disclaimer badge + alert
  - [ ] Permission-denied UX
  - [ ] Network interruption behavior

## 8) Submit for review

- [ ] Select build for release
- [ ] Export compliance (answer questions)
- [ ] Set pricing/availability
- [ ] Submit for review

---

## Notes specific to this app

- AI-generated images should always be labeled in-app.
- The app uses a backend for OCR/translation; ensure privacy policy explains what is uploaded, how it’s stored, and retention/deletion policy.
