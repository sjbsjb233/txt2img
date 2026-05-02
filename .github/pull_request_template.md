<!--
PR template — keep it short. Empty sections are fine; every section is
optional except "Summary" and "Test plan". The launch checklist below is
the §19 acceptance gate from docs/backend_design.md — tick the boxes that
apply to your slice of the work.
-->

## Summary

<!-- 1–3 bullets: what changed, why. Skip the "what" of well-named code. -->

-

## Test plan

<!-- Checklist of the human/automated checks reviewers should reproduce. -->

- [ ]
- [ ]

## Design doc traceability

<!-- Reference §X.Y from docs/backend_design.md or the PR slicing plan. -->

- §

---

## §19 launch checklist

Mark every box that the PR moves from "not done" to "verified". Boxes that
were already green can stay unchecked. Items copied from
[docs/backend_design.md §19](../docs/backend_design.md#19-上线前-checklist合并自调度系统设计md11加增项).

### Scheduler

- [ ] 4-lane anti-starvation: VIP-saturated load still gives Free ≥ 5%
- [ ] WFQ uses `Fraction` and normalises virtual time every 10 000 ticks
- [ ] Deadline promotion runs once and only once per job (`promoted=true`)
- [ ] Provider HALF_OPEN probe concurrency is exactly 1 globally
- [ ] `balance < threshold` ⇒ DRAINED, never lets the ledger go negative
- [ ] All-providers-failure path returns 503 `NO_PROVIDER_AVAILABLE` and
      refunds quota

### Quota / soft-quota

- [ ] Three 429s — `HARD_QUOTA_EXCEEDED` / `USER_BUSY` / `RATE_LIMITED` —
      each surface their own frontend copy
- [ ] Soft-quota penalty escalates linearly with `overage_ratio`
- [ ] Soft-quota captcha failure returns 412, never 200 + side effects

### Emergency switches

- [ ] `pause_generation` / `pause_image_access` / `block_new_member_login`
      / `force_captcha_global` all toggled and verified end-to-end
- [ ] Currently-RUNNING jobs are **not** killed when an emergency flag
      flips on

### Auth

- [ ] Every route except `/api/health`, `/api/auth/login`, and
      `/api/auth/captcha-check` rejects unauthenticated requests
- [ ] An admin endpoint returns 403 to a regular-user token
- [ ] Tier / quota / today_count never appear in user-facing responses

### File storage

- [ ] `hash_id` regex validation covers `^j_[A-Za-z0-9]{12}$` and rejects
      directory traversal attempts with 400
- [ ] Thumbnail long edge ≤ 720, webp quality 78
- [ ] Original images stream — `fetch().blob()` is **not** used
- [ ] Cleanup marks `DELETED` in DB before `rm -rf` of the on-disk dir
- [ ] `data/jobs/<hash>/` survives a container rebuild (bind-mount
      verified)

### SSE

- [ ] Heartbeat ≤ 30 s (`SSE_HEARTBEAT_SECONDS`)
- [ ] Reconnect with `Last-Event-ID` replays the 5-minute buffer
- [ ] `SSE_MAX_CONNECTIONS_PER_USER` enforced — N+1th connection evicts
      the oldest
- [ ] Cross-device fan-out: same user, two clients, both see the broadcast

### Multi-device sync

- [ ] Device A submits → device B sees `task_created` immediately
- [ ] Device A deletes → device B sees `task_deleted` immediately
- [ ] Logging out + back in keeps each user's IndexedDB store separate

### Admin

- [ ] Announcement HTML rendering uses an allowlist (no arbitrary JS)
- [ ] `provider_scoring.weights.*` must sum to 1.0
- [ ] Tier patches take effect without restarting
- [ ] Impersonation actions show the *admin* as actor in audit_log

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
