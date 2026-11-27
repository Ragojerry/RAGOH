/**
 * Privacy notice (Nigeria Data Protection Act 2023). Shown at first sign-in and again
 * whenever its version changes; tracking can't start until it is accepted. The text comes
 * from the server (so the retention periods match what the server enforces); a bundled
 * copy is used if the phone has never been online since installing.
 */
import { api } from "./api";
import { getMeta, setMeta } from "./db";

export interface Notice {
  version: string;
  title: string;
  sections: { heading: string; body: string }[];
}

const BUNDLED: Notice = {
  version: "2",
  title: "Your location and privacy",
  sections: [
    { heading: "What RAGOH records", body: "While your shift is on: your phone's location (every few seconds when you are walking, every minute or two when you are still), battery level and network type, the places you stop and for how long, and the surveys you fill with the time and place they were filled." },
    { heading: "When", body: "Only during a shift you start yourself. Nothing is recorded when your shift is off, and the app shows a notice while it is recording." },
    { heading: "Why", body: "To plan your walking routes, confirm that visits happened, support you in the field, and check the quality of the data collected." },
    { heading: "Who can see it", body: "Your supervisor and the programme's managers for your area, and system administrators. Every time someone views your route or exports data, it is recorded." },
    { heading: "How long it is kept", body: "Detailed location points are deleted after 90 days. After that only a summary is kept (where you stopped and for how long). Surveys are kept as part of the programme's data." },
    { heading: "Your rights", body: "You can ask to see the data held about you, or to correct it. Contact your supervisor or the programme's data protection officer." },
  ],
};

/** The notice to show: the latest from the server, else the bundled copy. */
export async function currentNotice(): Promise<Notice> {
  const raw = await getMeta("privacy_notice");
  return raw ? (JSON.parse(raw) as Notice) : BUNDLED;
}

/** Ask the server for the latest notice and whether this user accepted it. Offline: null. */
export async function refreshNotice(): Promise<boolean | null> {
  try {
    const r = await api<{ notice: Notice; accepted_version: string | null; needs_acceptance: boolean }>("/api/v1/me/privacy", { timeoutMs: 15_000 });
    await setMeta("privacy_notice", JSON.stringify(r.notice));
    if (r.accepted_version && !r.needs_acceptance) await setMeta("consent_version", r.accepted_version);
    return r.needs_acceptance;
  } catch {
    return null;
  }
}

export async function hasConsented(): Promise<boolean> {
  return (await getMeta("consent_version")) === (await currentNotice()).version;
}

export async function acceptedVersion(): Promise<{ version: string | null; at: string | null }> {
  return { version: await getMeta("consent_version"), at: await getMeta("consent_at") };
}

export async function recordConsent(): Promise<void> {
  const { version } = await currentNotice();
  await setMeta("consent_version", version);
  await setMeta("consent_at", new Date().toISOString());
  await setMeta("consent_pending", version);
  await sendPendingConsent();
}

/** Tell the server (retried after later syncs if the phone is offline now). */
export async function sendPendingConsent(): Promise<void> {
  const version = await getMeta("consent_pending");
  if (!version) return;
  try {
    await api("/api/v1/me/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notice_version: version }),
    });
    await setMeta("consent_pending", null);
  } catch {
    // stays pending
  }
}
