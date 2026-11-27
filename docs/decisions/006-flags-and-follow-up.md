# 006 — Abnormal-behaviour flags and follow-up

**Status:** accepted (27 Nov 2026)

**Rules as small functions, thresholds in config.** Each rule (R1–R15 per collector-day, S1–S2 across teams) is a Python function in `app/services/flags.py` that returns candidate flags with the evidence in `details`. Severity, thresholds and on/off live in the campaign config (`flags` section, merged over `core/defaults.py`), editable through `PUT /campaigns/{id}/config`. We chose config over a separate `flag_rules` table: one place for every tunable, already audited and versioned with the campaign.

**When rules run.** After every sync that brings GPS points or surveys (in the same background task that recomputes stops), and nightly at 03:30 (re-check yesterday for late syncs, then S1 "collector not supervised in N days" and S2 "high flag left untouched for H hours").

**Idempotent, respectful of people's work.** Flags are unique per (rule, collector, day, subject). Re-evaluation updates evidence, adds new flags, and deletes only flags still in status `new` that no longer apply. Anything a supervisor has touched stays, with its timeline.

**Risk score.** Sum of severity weights (low 1, medium 3, high 8 — configurable) of the day's flags that are not dismissed, capped at 100, stored with a per-rule breakdown so the score is always explainable. Dismissing a false positive lowers it immediately.

**Follow-up.** Actions are logged on the flag's timeline (who, what, when, comment): acknowledge, note, call, message, request explanation (the flag and question then appear on the collector's phone, which can reply offline), revisit (sends the point back to the collector with a note), back-check (creates a back-check task for another team member or the supervisor; when the back-check form is synced, answers are compared automatically with the original survey and the result added to the flag), escalate, resolve / dismiss (comment required), reopen. Status moves forward only (new → acknowledged → under review) until closed.

**Not in this phase.** Push notifications to phones (Phase 6) and the supervisor screens (mobile Phase 6, web Phase 7) — the API for them is complete.
