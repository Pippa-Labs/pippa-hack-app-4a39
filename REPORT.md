# Bridge Up: build report

Bridge Up answers when Tower Bridge next lifts, and for which vessel. It has one
outcome (`bridge.lifts`), two optional inputs (`day`, `time`), and no keys.

## How it gets data

1. **Live:** one request to `https://www.towerbridge.org.uk/lift-times`
   (5 s timeout, browser-like User-Agent), parsed with `html.parser`.
2. **Fallback:** `lift_times.json`, bundled beside `app.py`, used if the site is
   blocked, times out or shows no lifts. The answer then says
   "published list as of <date>".

## Assumptions and things skipped (10-minute build)

- The official site could not be fetched from the cloud session, so the
  bundled snapshot comes from London Drum's copy of the published list
  (updated 13 Oct 2026). It has **times only, no direction or vessel**, and it
  may be out of date. Refresh it from the official page before relying on it.
- No `scripts/build_data.py`: a snapshot-refresh script was skipped for time.
- Parts of the brief about TfL and station names did not apply to this app
  and were dropped.
- "Up at a time" means a lift is listed within 15 minutes of that time.
- The repo's CLAUDE.md took priority over the brief: the work was done in the
  organiser-made repo rather than a fresh template, and no author name was
  invented.

## Demo questions (checked with stubbed live data, not on Pippa)

| Question | Reply |
|---|---|
| When does Tower Bridge open next? | Tower Bridge next lifts Tuesday 24 November at 13:45, upriver, for Thames Barge. |
| Tower Bridge lift times on Wednesday | Wednesday has 2 lifts: 18:00, 18:45. Tower Bridge next lifts Wednesday 25 November at 18:00, upriver, for Paddle Steamer Dixie Queen. |
| Will Tower Bridge be up at 6pm Wednesday? | A lift is listed within 15 minutes of 18:00 Wednesday, so expect the road to close briefly. ... |
| Is Tower Bridge lifting this weekend? | No lifts are listed for the weekend. Tower Bridge next lifts ... |
| When does it open next? (site blocked) | Tower Bridge next lifts Friday 27 November at 19:15. (Source: published list as of 2026-10-13 ...) |

## Routing check

Not run: it needs the Pippa SDK, which only the build installs. The manifest
was checked by hand against `scripts/routing_check.py`: there are 8 example
phrases, none of the reserved words, no "by" in the summary, and the summary's
first clause names the fact.

## How to publish

1. Push to `main` of `Pippa-Labs/pippa-hack-app-4a39`. This publishes a
   private draft, and the build takes about 2 minutes.
2. At app.getpippa.ai/apps, open **Created by me**, find Bridge Up, press
   **Install**, and turn on **Always use my latest version**.
3. Ask "@Bridge Up when does Tower Bridge open next?" and look for the
   "via Bridge Up" badge.
4. When it works, press **Submit for review**.

## Risks

- The guide notes another builder is already making a "bridge lifts" app,
  which could compete in routing.
- The official site may block Pippa's servers. In that case answers come from
  the stale snapshot.
- If the site's HTML changes, the parser finds nothing and the app falls back
  to the snapshot.
- Scheduled lifts can be cancelled at short notice. Every answer says so.

## Notes

- Republished from GitHub with a private no-reply commit address.
