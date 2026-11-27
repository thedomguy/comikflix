import type { CapacitorConfig } from "@capacitor/cli";

// The app loads the deployed site instead of the bundled build, so the PIN session cookie and
// the server's same-origin checks work unchanged, and a frontend deploy reaches the app without
// an APK release. COMIKFLIX_URL (at `cap sync`) points a build at another server.
const config: CapacitorConfig = {
  appId: "dev.domguy.comikflix", // the package KT Remote releases as "comikflix"
  appName: "Comicflix",
  webDir: "../web-dist",
  server: {
    url: process.env.COMIKFLIX_URL || "https://watch.domguy.dev/readers/",
  },
};

export default config;
