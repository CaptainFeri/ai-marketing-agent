# The workspace calendar: drag-and-drop, timezone, spacing, holidays

Handoff section 11 (phase 2): "تقویم کشیدن‌ورهاکردنی، منطقه زمانی هر بازار،
قوانین فاصله بین پست‌ها، هشدار تعطیلات و مناسبت‌ها" — a drag-and-drop
calendar, per-market timezone, spacing rules between posts, and
holiday/occasion warnings.

```
GET /workspaces/{id}/calendar?start=...&end=...
  ├─ app.services.calendar.workspace_calendar()
  │    ├─ every Publication in range, across every package (join on ContentPackage)
  │    └─ app.services.holidays.holidays_in_range()   fixed-date table, see below
  └─ CalendarOut { timezone, calendar, min_publish_spacing_minutes, publications, holidays }

PATCH /packages/{package_id}/publications/{publication_id}   {scheduled_at}
  └─ app.services.publishing.reschedule_publication()
       └─ _check_spacing()   same rule schedule_publication() already enforces
```

## Timezone

`Workspace.timezone` (an IANA zone, e.g. `Asia/Tehran`) already existed but
was dead — nothing read it. The calendar page is the first place that does:
every publication's UTC `scheduled_at` is displayed in the workspace's own
timezone via `Intl.DateTimeFormat(..., { timeZone: workspace.timezone })`,
so an operator in a different timezone than the target market still sees
the time their audience will actually see the post at.

Drag-and-drop moving a chip to a different day keeps the same UTC
time-of-day and only changes the calendar date — it does not attempt a full
wall-clock-preserving conversion across the workspace's timezone (which
would also need to reason about DST at the drop target). That is a
documented simplification, not a silent one.

## Spacing rule

`Workspace.min_publish_spacing_minutes` (default 60, `0` disables it) is
enforced by `app.services.publishing._check_spacing()`, shared by both
`schedule_publication()` and `reschedule_publication()`: a new or moved
slot within that many minutes of another `scheduled`/`publishing`/
`published` post **on the same channel** in the same workspace is refused
(409) with the conflicting post's own time in the error. It is per channel,
not global — a WordPress post and a Telegram post going out the same
minute is fine; two WordPress posts is not.

## Holidays

See `app/services/holidays.py`'s module docstring for the full reasoning.
In short: only Iran's **fixed-date** Jalali-calendar national holidays are
included (Nowruz, Islamic Republic Day, Nature Day, the death of Imam
Khomeini, the 15 Khordad uprising, the Islamic Revolution anniversary, Oil
Industry Nationalization Day) — the ones that land on the same Jalali
month/day every year. The **lunar** Hijri holidays (Eid al-Fitr, Ashura,
...) are deliberately left out: they shift roughly 11 days earlier every
Gregorian year and need region-specific moon-sighting data this platform
has no reliable source for, so guessing at their Gregorian date would be
worse than not showing them.

The table itself was generated once from ICU's own Persian calendar
(Node's `Intl.DateTimeFormat` with the `persian` calendar) — a verified
oracle, not a hand-derived Jalali/Gregorian conversion algorithm, which is
exactly where calendar-math bugs usually hide. It covers 2024 through 2041;
extending it is a re-run of the same generation approach, documented in the
module itself.

The panel's calendar page computes its own Jalali month grid the same
way, live, in the browser (`jalaliOf()`/`jalaliMonthDays()` in
`calendar/page.tsx`) — the browser's own ICU implementation, not a
precomputed table, since a live client can always ask for "today" without
needing pregenerated data the way the backend's static holiday list does.

## What's real, what's a documented gap

| Piece | Status |
|---|---|
| Month grid (Jalali or Gregorian, per workspace) | real — browser ICU, no library added |
| Drag-and-drop reschedule | real — HTML5 drag events → `PATCH .../publications/{id}` |
| Spacing rule | real — enforced server-side, same rule on create and reschedule |
| Timezone display | real — `Intl` per-viewer conversion of the stored UTC instant |
| Holidays (fixed Jalali dates) | real, generated + verified data, documented 2024-2041 range |
| Holidays (lunar Hijri) | not attempted — no reliable data source, documented gap |
| Suggested publish time | not this task — see `docs/HANDOFF.md` phase 2 (task #47) |
