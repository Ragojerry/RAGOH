# RAGOH mobile app

## Get the APK
1. GitHub → **Actions** → **Mobile** workflow → open the latest successful run (on `main`, or on a pull request).
2. Scroll to **Artifacts** → download **ragoh-android-apk** (a zip) → unzip → `ragoh-xxxxxxx.apk`.
3. Send it to the phone (WhatsApp, Google Drive, USB). On the phone, open it and allow "Install unknown apps" when asked.

Phones need **Android 8.0 or newer**. Uninstalling the app deletes unsynced work — always sync first.

> To have the server address filled in automatically, add the GitHub repository variable `RAGOH_API_URL` (e.g. `https://13-48-10-20.sslip.io`, from the Deploy run) and re-run the Mobile workflow. Otherwise, type the address on the sign-in screen once.

## First use
1. Open RAGOH → enter username, password (and server address if asked). **Needs internet.**
2. Create a 4–6 digit **PIN**. From now on the app opens with the PIN, even offline.
3. The app downloads your points and forms automatically.

## Daily use (collectors)
- **Start shift** (records your location) → **My points** → nearest points first (or **Map**).
- Open a point → **Start: Household survey** → answer → **Finish and save**. Answers save as you type; you can leave and come back (**Continue**).
- If you can't do the survey: **Not found / Refused / Skip for now** with a reason.
- **End shift** at the end of the day.
- Sync happens on its own (every 5 minutes, when the internet comes back, and after each survey). **Sync now** on the home screen forces it. **My day** shows which surveys are sent.

## Testing the Phase 2 goal (offline survey, no duplicates)
1. Sign in as `col1` (password: Parameter Store `/ragoh/staging/DEMO_PASSWORD`).
2. Turn on **airplane mode**. Start shift, fill and finish 2 surveys, mark one point **Refused**. Home shows "3+ waiting".
3. Close the app completely, reopen it (PIN), check **My day** — the surveys are still there as "Waiting".
4. Turn airplane mode off. Within a few seconds the status becomes "Up to date" and My day shows "Sent".
5. Press **Sync now** several times. In the API (`/docs` → `GET /api/v1/visit-points` as admin, or the database) each survey exists once.

## Location tracking (Phase 3)
- The first time you start a shift, the app explains what is recorded. Location is recorded **only while the shift is on**; a notification "RAGOH shift in progress" shows while it runs.
- When asked, allow location **"Allow all the time"** — otherwise tracking stops when the screen is off.
- Exempt RAGOH from battery optimisation (the app opens the right settings page the first time). On Tecno/Infinix/itel also allow "Auto-start" / "Run in background" in the phone manager app.
- The home screen shows "Location tracking on · N points today". If it says tracking is not running, open the app (it restarts tracking automatically) and check GPS is on.

### Testing tracking
1. Start a shift, put the phone in your pocket, walk to two nearby places and stay ~5 minutes at each, then walk back.
2. Sync, then open `/docs` → `GET /api/v1/collectors/{your user id}/day?date=today` as a supervisor or admin: you should see a stop at each place, with the time spent there, and the walked distance.
3. Or use the simulator (`tools/gps-simulator/README.md`) to push a whole synthetic day, including a "cheater" day.

## Routes and offline map (Phase 4)
- **My points → Plan my route**: puts today's open visits in the best walking order from where you are, numbers them on the list and the map, and shows the walking distance and time to each. When visits change (done, skipped, new ones), it says "out of date" — tap **Re-plan**.
- Without internet the app still plans, using straight-line distances ("estimated — offline").
- On a point, **Walking route** shows the route on the map with turn-by-turn steps (or a straight line when offline).
- **Settings → Offline map → Download offline map**: downloads the map around your points (about 10–40 MB — use Wi-Fi). Available once the server's map has been prepared (see DEPLOYMENT.md).

## Messages and supervisor follow-up (Phase 5)
- **Messages** on the home screen shows new messages from your supervisor and any **question about your work** ("Please explain"). Type your explanation and send — it goes as soon as you have internet.
- You can message or call your supervisor from the same screen.
- **Back-check** tasks (verify another collector's visit) and **revisits** appear in My points with an orange label and the supervisor's note. Back-check tasks open the short back-check form.

### Testing the flags end to end (staging)
1. Push a "cheater" day for `col1` with the simulator (`tools/gps-simulator`).
2. In `/docs` as `sup1`: `GET /api/v1/risk?date=<today>` (col1 at the top), `GET /api/v1/flags` (surveys far from houses, points done without a visit, very short interviews, long idle at home).
3. `POST /api/v1/flags/{id}/actions` with `{"action": "request_explanation", "comment": "Did you visit?"}` → on col1's phone, sync → Messages shows the question → reply.
4. Back as sup1: `GET /api/v1/flags/{id}` shows the reply; then `{"action": "resolve", "comment": "..."}`.

## Supervisor mode (Phase 6)
Supervisors open the app on **My team** (tap **My own work ›** for your own shift, points and checklists; **My team →** to go back). Everything shows the last known data with "as of HH:MM" when there's no network.
- **My team**: each team's progress, who needs attention (no signal, idle, high flags), and every collector with status, visits done, distance, battery and unsynced records.
- **Live map**: collectors coloured by status (moving / at a point / idle / no signal), refreshed every 30 seconds. Tap one → **Go to** (walking route from you to them) or **Details**.
- **Collector**: call, go to, today's **route replay** (track, numbered stops with time spent, play/pause, any past day), **spot check** (the supervision checklist opens with the collector filled in), **move points** to another collector of the same team (only points nobody has started), open flags, send a message.
- **Flags**: open flags, high first. On a flag: acknowledge, call, ask the collector to explain, message, note, request a revisit, do a back-check yourself, escalate, resolve or dismiss (with a comment). The history shows every step.
- Your own shift and GPS tracking work exactly like a collector's, and spot checks count towards rule S1 ("collector not supervised").

### Status meanings
| Status | Meaning (all thresholds in campaign config `live`) |
|---|---|
| Moving | On shift, seen in the last 10 min |
| At a point | Within 40 m of one of their open points |
| Idle | On shift, hasn't moved more than 50 m in 30 min |
| No signal | Nothing received for 10 min (phone off, no network, or tracking stopped) |
| Off shift / Not seen today | Shift not started or ended / nothing at all today |

Phones send a small heartbeat every 3 minutes on shift, so the map is current even between full syncs.

### Notifications
Supervisors are notified of: a new **high-severity flag**, a collector with **no signal for 30 min during a shift**, a collector who **hasn't started by 09:00** although they have work, and messages. Collectors are notified of messages, questions about their work, revisits, back-checks and moved points. No alerts after 20:00. Tapping a notification opens the right screen.

Without push set up (below), the app still shows high-flag alerts as phone notifications while it is open, and everything is visible in the app.

### No network? Hand data to the supervisor (phone to phone)
1. Collector: Home → **No network? Hand data to supervisor** → **Show codes**. A sequence of QR codes cycles on the screen.
2. Supervisor: My team → **Receive data from a collector** → point the camera at the collector's screen until it says "Received N records" (a few seconds; the order doesn't matter, missed codes are picked up on the next cycle).
3. The supervisor's phone sends it with its next sync. On the server it is stored exactly as if the collector had synced. **Receive data** lists each bundle as Waiting / Sent / Refused.

Neither phone needs network or pairing. The data is sealed with the collector's own key, so the supervisor's phone can't read or change it. It also stays on the collector's phone and is sent again at their own sync (counted once). GPS tracks are not included (too large) — they arrive with the collector's own sync. The collector's phone must have synced once since installing Phase 6 to get its key.

## Push notifications setup (one-time, optional)
Push needs a free Firebase project and a free Expo account. Until this is done, the APK works without push.
1. **Firebase**: console.firebase.google.com → Add project "ragoh" → Add app → Android, package name `ng.ragoh.app` → download `google-services.json`.
2. **GitHub secret**: base64-encode the file (`base64 -w0 google-services.json`, or any online base64 tool) and add it as repository secret `GOOGLE_SERVICES_JSON_B64` (Settings → Secrets and variables → Actions).
3. **Expo**: create an account at expo.dev → Create project "ragoh" → copy the **Project ID** → add it as repository *variable* `EXPO_PROJECT_ID`.
4. **Connect them**: Firebase → Project settings → Service accounts → Generate new private key. In expo.dev → project → Credentials → Android → `ng.ragoh.app` → FCM V1 service account key → upload that file.
5. Re-run the **Mobile** workflow; the build summary says "Push notifications: enabled". Install the new APK; the phone asks to allow notifications after the first sync.

### Testing the Phase 6 goal
1. Install the APK on two phones; sign in as `sup1` on one and `col1` on the other; both sync once.
2. On `col1`'s phone start a shift and walk (or push a simulated "cheater" day for `col1` with the simulator). `sup1` sees col1 on the **Live map**, and receives a **High-priority flag** notification.
3. `sup1`: tap the notification → **Go to collector** (walking route) → **Spot check** → fill and finish the checklist → back on the flag, **Resolve** with a comment.
4. Device-to-device: turn on airplane mode on **both** phones. col1 fills a survey, then **Hand data to supervisor**; sup1 **Receive data**. Turn sup1's network back on → the bundle shows **Sent**, and the survey is on the server under col1 (`GET /api/v1/collectors/{col1}/day?date=…`).

## What's not in this phase
The web dashboard (Phase 7). Barcode questions are typed by hand for now.
