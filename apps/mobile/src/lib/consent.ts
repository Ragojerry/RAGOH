/** One-time location-tracking notice (Nigeria Data Protection Act 2023). */
import { api } from "./api";
import { getMeta, setMeta } from "./db";

export const NOTICE_VERSION = "1";

export const NOTICE_TEXT =
  "While your shift is on, RAGOH records your phone's location every few seconds to minutes, " +
  "and the time you spend at each place. This is used to plan routes, confirm visits and " +
  "support you in the field. Your supervisor and programme managers can see it. Nothing is " +
  "recorded when your shift is off. Raw location data is deleted after 90 days.";

export async function hasConsented(): Promise<boolean> {
  return (await getMeta("consent_version")) === NOTICE_VERSION;
}

export async function recordConsent(): Promise<void> {
  await setMeta("consent_version", NOTICE_VERSION);
  await setMeta("consent_pending", "1");
  await sendPendingConsent();
}

/** Tell the server (retried after later syncs if the phone is offline now). */
export async function sendPendingConsent(): Promise<void> {
  if ((await getMeta("consent_pending")) !== "1") return;
  try {
    await api("/api/v1/me/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notice_version: NOTICE_VERSION }),
    });
    await setMeta("consent_pending", null);
  } catch {
    // stays pending
  }
}
