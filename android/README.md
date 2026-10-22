# Comicflix for Android

A native WebView app around the live site, `https://watch.domguy.dev/readers/` (package `dev.domguy.comikflix`).
It loads the deployed web app instead of bundling `frontend/`, so the PIN session cookie and the server's
same-origin checks work unchanged, and a frontend deploy reaches the app without an APK release.

What the shell adds to the PWA:
- Fullscreen, like the PWA manifest asks; Back walks the app's hash history and exits at the start.
- Share an Asura Scans or mgeko link to Comicflix: it starts an import (`POST /api/ingest`, start chapter 1,
  tagged `via:android`) and opens Downloads. Other shared text opens Search.
- Links to `https://watch.domguy.dev/readers/...` (including Jarvis's `/go/...` links) open in the app once
  enabled under App info > Open by default > Add links (the domain isn't auto-verified).
- The screen stays on in the reader (`#/read/...`); links to other sites open in the browser.

## Build and release

JDK 17 and the Android SDK (`ANDROID_HOME`). Debug builds sign with `~/.android/debug.keystore`, the key
every KT Remote app shares, so phone installs and updates go through KT Remote:

```sh
./gradlew :app:assembleDebug                                    # app/build/outputs/apk/debug/app-debug.apk
../../mobile-apps/kt-remote/scripts/release-app.sh comikflix . --deploy all   # from a kt-remote checkout
```

`-PcomikflixUrl=https://host/path/` points a build at another server.
