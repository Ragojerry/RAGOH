# 002 — Geographic scope via materialised area paths

**Status:** accepted (26 Nov 2026)

**Context.** Every query must be limited to the user's geography (a supervisor sees only their team's area; a manager only their LGAs/wards). Areas form a tree: country → state → LGA → ward → settlement.

**Decision.** Each `admin_areas` row stores `path = "/<root-id>/.../<self-id>/"`. "Inside my scope" is a prefix match (`path LIKE '<scope-path>%'`) backed by a `text_pattern_ops` index. All list/read endpoints build their filter through `app/services/scope.py`. Supervisors' scope comes from teams they supervise (`team_members.role_in_team = supervisor`), so there is no separate `supervisor_team_assignments` table; membership history is kept with `active_from`/`active_to` (exclusive).

**Consequences.** Fast, simple scope checks without recursive queries. Moving an area to a new parent requires rewriting the paths of its subtree (rare; handled by the area upsert for the area itself — a subtree move tool will be added if ever needed).
