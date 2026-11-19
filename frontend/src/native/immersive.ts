import { Capacitor, registerPlugin } from "@capacitor/core";

interface ImmersivePlugin {
  enable(): Promise<void>;
  disable(): Promise<void>;
}

const Immersive = registerPlugin<ImmersivePlugin>("Immersive");
const isAndroidApp = Capacitor.isNativePlatform() && Capacitor.getPlatform() === "android";

/** Hides the status/nav bars (swipe to reveal) when running as the Android app. No-op elsewhere. */
export function enterImmersive() {
  if (isAndroidApp) Immersive.enable().catch(() => {});
}

export function exitImmersive() {
  if (isAndroidApp) Immersive.disable().catch(() => {});
}
