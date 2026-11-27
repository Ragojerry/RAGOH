# RAGOH — Guide for supervisors

You use RAGOH in two places:
- **The phone app** in the field: your team live, flags, spot checks, moving points, receiving data, and your own shift.
- **The web dashboard** on a laptop: replay, team comparison, coverage, flags, exports, phones. Open the server address in a browser (e.g. `https://<server>.sslip.io`).

You see only your own teams. Managers see their area; admins see everything.

---

## 1. Setting up your team (with the admin)
1. The admin creates accounts and teams and uploads the visit points (CSV/Excel/GeoJSON with coordinates).
2. Points are assigned to collectors. You can move unstarted points later (§4).
3. Give each collector the APK, their username and password, and **the collector guide**. Check on their phone, before the first day:
   - location is allowed **All the time**,
   - battery optimisation is **off** for RAGOH (and Auto-start is allowed on Tecno/Infinix/itel),
   - the **offline map** is downloaded,
   - they have read the **privacy notice** with you, and know they can ask questions.

## 2. Your day on the phone
- The app opens on **My team**. Tap **My own work ›** for your own shift and checklists. Start your own shift too: your route is recorded like the collectors', and your spot checks count.
- **My team** shows who needs attention: no signal, idle, high flags. **Live map** shows everyone by colour:

| Colour | Meaning |
|---|---|
| Blue | Moving (seen in the last 10 minutes) |
| Green | At one of their points |
| Amber | Idle: on shift, hardly moved for 30 minutes |
| Red | No signal for 10+ minutes |
| Gray | Off shift / not seen today |

- Notifications: a **high-priority flag**, a collector with **no signal for 30 minutes during a shift**, a collector who **hasn't started by 09:00**, and messages. Tap a notification to open the right screen.
- **Go to** a collector: walking route from you to their last position.

## 3. Handling flags
A flag means "please look", **not** "this person cheated". GPS drifts, phones die, households move. Always hear the collector before deciding.

1. Open the flag (phone: **Flags**; dashboard: **Flags**). Read the evidence and **replay that day**.
2. **Acknowledge** so others know you're on it.
3. Choose what to do:
   - **Ask the collector to explain** — the question appears in their app; their answer comes back to the flag.
   - **Call** or **message**.
   - **Spot check** — go there and fill the supervision checklist (the collector is filled in for you).
   - **Request a revisit** or **assign a back-check** for the household.
   - **Escalate** to your manager if it is serious or repeated.
4. Close it: **Resolve** (what happened and what was done) or **Dismiss** (false alarm, with the reason). Every step is recorded in the flag's history.

The most important flags (high): survey filled far from the household, visits impossibly fast, fake GPS apps, the same location for many households, travel speeds impossible on foot.

## 4. Other actions
- **Move points**: collector → **Move points to another collector** → pick unstarted points and a teammate. Both get a notification. Points already started stay with whoever started them.
- **Receive data phone-to-phone** (no network): **Receive data from a collector** → scan the codes on their screen. Your phone sends it at your next sync; **Receive data** shows Waiting / Sent / Refused.
- **Messages**: to one collector or the whole team.

## 5. The dashboard
| Page | Use it for |
|---|---|
| Live map | Everyone's position and points now. |
| Route replay | A collector's day on a timeline: route, stops (circle size = time spent), surveys, planned route. |
| Team overview | Compare collectors over any period: coverage, interview time, time at points, distance, flags per 100 surveys, risk, spot checks. Sort any column. |
| Coverage | Visited vs not-visited points by area; who hasn't been spot-checked. |
| Flags | Inbox with filters and every follow-up action. |
| Exports | Surveys, stops, flags, points (Excel/CSV), GPS tracks (GeoJSON). |
| Phones | Each phone's app version, crashes, battery use per shift, data use; block or erase a phone. |
| Audit log | (Managers/admins) who viewed routes, exported data, blocked phones. |

**A good weekly routine:** Team overview for the week (sort by risk and flags per 100) → open the top flags → replay → spot-check anyone with high risk **or** with no spot check yet (Coverage page) → close flags with clear comments.

## 6. Lost or stolen phone
Dashboard → **Phones** → find the phone → **Block…** → tick **Also erase it** → give the reason. The phone is signed out at once and cannot sync. The next time it has internet, it deletes everything on it. Unsynced work on that phone is lost, so first ask whether the collector handed data to you recently. If the phone is found, **Unblock** it; the collector signs in again.

## 7. Privacy — your responsibilities
- You are seeing people's movements. Look only at what you need for supervision.
- Every route you open and every export you download is recorded with your name.
- Don't share exports by WhatsApp or personal email; store them only where your programme says.
- Never ask collectors to share their password or PIN, and never use a collector's account.
- Collectors may ask what is recorded about them: the dashboard's **Privacy** page shows exactly what they agreed to.

## 8. When something goes wrong
| Problem | What to do |
|---|---|
| Collector shows red (no signal) for long | Call them. Common causes: battery saver killed the app, GPS off, no network (data arrives later). |
| A collector's track has gaps | Check the battery-optimisation setting on their phone (§1.3). |
| No notifications | Push needs a one-time setup by the admin. Until then, keep the app open or check the Flags screen. |
| Data refused for a relayed bundle | The collector may not be in your team, or their phone was never online since installing. |
| Anything else | Contact the RAGOH admin. |
