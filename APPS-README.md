# Smart Restaurant - Windows & Android apps (Developed by Nadish)

Both apps only open the existing site: https://smartrestaurant1210.streamlit.app/
The website, Supabase and database are NOT changed. No keys or secrets are in these files
(the Android signing key `sideload.jks` is a public, non-secret key used only so updates install over old builds).

## STATUS - please read
These files were written and statically checked only (Python compiles, YAML/XML parse).
**Neither app was built or run.** The first GitHub Actions run is the first real test; if it fails,
open the failed run, copy the red error text and send it to me.

## Where the files go (repo ROOT, next to `netlify.toml`)
    .github/workflows/build-windows.yml
    .github/workflows/build-android.yml
    desktop/...        android/...        APPS-README.md
Upload with the same folder names (GitHub app/website: Add file > Upload files, keep the folders).
`.github` starts with a dot - on a phone it is easier to use Add file > Create new file and type `.github/workflows/build-windows.yml`, then paste.

## Build from a phone (Chrome > github.com > menu > "Desktop site")
1. Repo > **Actions** tab. If asked, tap "I understand my workflows, enable them".
2. Left list: **Build Android APK** (or **Build Windows app**) > **Run workflow** > green Run workflow.
3. Wait (Android about 5-10 min, Windows about 5-8 min) until a green tick.
4. Repo home > right side **Releases** > newest release > tap `SmartRestaurant.apk` or `SmartRestaurant_Setup.exe`.
   (Also under the run page > Artifacts, as a zip.)

## Install
- Android: open the downloaded APK > allow "install unknown apps" for your browser/Files > Install.
- Windows: copy `SmartRestaurant_Setup.exe` to a PC and run it (desktop shortcut option included).
  Windows SmartScreen may warn because the installer is not code-signed: More info > Run anyway.
  Needs Microsoft Edge WebView2 (already on Windows 10/11).

## What works
- Login (same accounts), navigation, same data as the website.
- Camera: Android asks for Camera permission when a page requests it; Windows asks via WebView2.
- Downloads: Android saves to Downloads; Windows needs pywebview download support (ALLOW_DOWNLOADS on).
- External links open in the normal browser.
- No internet / server asleep: a "Try again" page (Streamlit free apps sleep).

## Android WebView limitations
- It is the website in a frame, not a native app: needs internet, no offline orders, no push notifications.
- Streamlit's own camera/QR widgets depend on the WebView supporting them; test before relying on it.
- Streamlit file downloads that use blob/data links may not work in WebView; if one fails, use the site in Chrome.
- Permissions used: INTERNET and CAMERA only.
- The site uses HTTPS only; plain http is blocked.
- APK is self-signed, for direct install, not Google Play.
