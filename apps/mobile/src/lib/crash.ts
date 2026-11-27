/**
 * Crash reporting without a third-party service: JavaScript errors are written to the
 * outbox as "app_error" device events and reach the server with the next sync. Admins see
 * a per-phone crash count on the dashboard's Devices page. No personal data is included,
 * only the error message, the top of the stack and the screen.
 */
import Constants from "expo-constants";

interface ErrorUtilsLike {
  getGlobalHandler(): (error: unknown, isFatal?: boolean) => void;
  setGlobalHandler(handler: (error: unknown, isFatal?: boolean) => void): void;
}

export async function recordError(error: unknown, details: Record<string, unknown> = {}): Promise<void> {
  try {
    const e = error instanceof Error ? error : new Error(String(error));
    const { addDeviceEvent } = await import("./tracking");
    await addDeviceEvent("app_error", {
      message: e.message.slice(0, 300),
      name: e.name,
      stack: (e.stack ?? "").split("\n").slice(0, 8).join("\n").slice(0, 1500),
      app_version: Constants.expoConfig?.version ?? null,
      ...details,
    });
  } catch {
    // never let crash reporting crash the app
  }
}

let installed = false;
export function installCrashHandler(): void {
  const eu = (globalThis as { ErrorUtils?: ErrorUtilsLike }).ErrorUtils;
  if (installed || !eu) return;
  installed = true;
  const previous = eu.getGlobalHandler();
  eu.setGlobalHandler((error, isFatal) => {
    void recordError(error, { fatal: !!isFatal });
    previous(error, isFatal);
  });
}
