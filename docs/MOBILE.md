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

## What's not in this phase
Supervisor team views (Phase 6). Barcode questions are typed by hand for now.
