# Music Discovery with Discord Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A container that posts 2 album suggestions a week from Aurral to #media-alerts with Approve/Reject buttons, and requests only approved albums through Lidarr.

**Architecture:** One Python container (`discovery-bot`) on mediahub-i7. A long-lived `discord.py` client handles buttons; an asyncio loop runs the weekly picker, expiry and follow-ups. Aurral, Lidarr and Prowlarr are reached over `localhost` HTTP through thin client modules; all decisions live in pure functions so they test without network. State is SQLite on a mounted volume.

**Tech Stack:** Python 3.12, `discord.py` 2.x, `requests`, `pytest`, SQLite (stdlib), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-06-music-discovery-approval-design.md`

## Global Constraints

- Repo `mediahub-configs` is **public**: no tokens, keys or personal data committed. Env file `stacks/discovery-bot/discovery-bot.env` is gitignored; commit only `discovery-bot.env.example`.
- Stack lives at `stacks/discovery-bot/`, deployed from the repo on mediahub-i7 only; pinned image (`python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54`) and pinned `requirements.txt`.
- Match artists/albums on MusicBrainz ID first; fall back to case-insensitive name only if an ID is missing.
- Request via `POST /api/library/albums/request` with `managedBy: "lidarr"` and `triggerSearch: true` (Aurral at `http://localhost:3001`, header `x-api-key`). Lidarr at `http://localhost:8686` (header `X-Api-Key`).
- Never delete anything in Lidarr/Aurral/disk. Lidarr writes are limited to unmonitoring extra albums.
- 2 cards/week; schedule Saturday 10:00 America/Chicago; cards expire after 14 days; artist re-eligible after 60 days; pause posting at 4+ pending cards; 7-day "not found yet" note on approved albums with no files; catch-up within 3 days of a missed schedule.
- Channel ID `1549633462351175730`. Reject = Aurral feedback `less_like_this` plus the bot's own rejected list.
- No-indexer-hit albums are still posted (note only); artists with no confident album pick are skipped.

## Review Focus

- Aurral returns an empty or failed recommendation list: skip the week, alert, post nothing.
- Lidarr is down or errors on Approve: card keeps its buttons and shows the error; nothing is marked approved.
- Approve double-clicked, or clicked on an expired/already-answered card: exactly one request, later clicks are told the outcome.
- Artist whose discography is only compilations/live/singles, or has two albums with the same title (Johnny Gill): skipped, or resolved by year and MBID, never by title.
- Bot restarts with pending cards: old cards' buttons still work.

## File Structure

All under `stacks/discovery-bot/`:

- `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `discovery-bot.env.example`
- `bot/config.py` — env → `Settings`
- `bot/state.py` — SQLite store (`Store`)
- `bot/aurral.py`, `bot/lidarr.py`, `bot/prowlarr.py` — HTTP clients
- `bot/albumpick.py` — `pick_album` (pure)
- `bot/picker.py` — `select_candidates` (pure) and `build_week`
- `bot/cards.py` — embed building and persistent button view
- `bot/actions.py` — `approve`, `reject`, `expire_stale`, `followup_missing`
- `bot/main.py` — Discord client, schedule loop
- `tests/` — one test file per module, plus `tests/fixtures/`

---

### Task 1: Scaffold, config and state store

**Files:**
- Create: `Dockerfile`, `requirements.txt`, `discovery-bot.env.example`, `bot/__init__.py`, `bot/config.py`, `bot/state.py`, `tests/test_state.py`
- Modify: repo `.gitignore` only if `stacks/discovery-bot/discovery-bot.env` is not already ignored (check with `git check-ignore`).

**Interfaces:**
- Produces: `Settings` (dataclass: `discord_token, channel_id:int, aurral_url, aurral_key, lidarr_url, lidarr_key, prowlarr_url, prowlarr_key, db_path, tz`) via `load_settings(env: Mapping) -> Settings`.
- Produces: `Store(path)` with `add_suggestion(artist_mbid, artist_name, album_mbid, album_title, year, message_id, now) -> int`, `get(id) -> Suggestion|None`, `by_message(message_id)`, `set_status(id, status, now)` where status ∈ `pending|approved|rejected|expired`, `pending_count() -> int`, `known_artist_mbids(now, cooldown_days=60) -> set[str]` (rejected always; pending; anything posted within cooldown), `approved_without_note(now, days=7) -> list[Suggestion]`, `mark_noted(id)`, `last_post_at() -> datetime|None`.

- [ ] **Step 1: Write failing tests** in `tests/test_state.py`: `test_rejected_artist_always_known`, `test_expired_artist_known_only_within_60_days`, `test_pending_count_ignores_answered`, `test_set_status_is_idempotent_and_returns_false_when_already_answered` (second `set_status` on a non-pending row returns `False`).
- [ ] **Step 2: Run** `pytest tests/test_state.py -v` — expect FAIL (module missing).
- [ ] **Step 3: Implement** `Store` with SQLite (single `suggestions` table keyed by id, unique on `message_id`) and `load_settings` (raise a clear error naming any missing env var). `set_status` must be a single conditional `UPDATE ... WHERE status='pending'` so double clicks are safe.
- [ ] **Step 4: Run** the tests — expect PASS.
- [ ] **Step 5: Create** `Dockerfile` (pinned base above, non-root user, `CMD ["python","-m","bot.main"]`), `requirements.txt` (pin `discord.py`, `requests`, `pytest` to the versions installed at build time), `discovery-bot.env.example` with every `Settings` key and empty values.
- [ ] **Step 6: Commit** `feat(discovery-bot): scaffold, config, state store`.

### Task 2: Aurral and Lidarr clients

**Files:**
- Create: `bot/aurral.py`, `bot/lidarr.py`, `tests/test_clients.py`, `tests/fixtures/discover.json` (trim to ~5 recs from a real `GET /api/discover`), `tests/fixtures/artist_johnny_gill.json` (real `GET /api/artists/<mbid>` response)

**Interfaces:**
- Produces `Aurral(base_url, key, session=None)`: `recommendations() -> list[dict]` (raises `AurralError` on non-200 or empty `recommendations`), `discography(mbid) -> list[dict]` (the `release-groups` list), `library_artist_mbids() -> set[str]` (from `GET /api/library/artists`; also lowercase names set via `library_artist_names()`), `request_album(artist_mbid, artist_name, album_mbid, album_title) -> dict` (the POST in Global Constraints; raises `AurralError` with the response message), `send_feedback(artist_mbid, artist_name, action, tags, seeds)`.
- Produces `Lidarr(base_url, key, session=None)`: `monitored_albums(artist_mbid) -> list[Album]` (`mbid, title, year, monitored, track_file_count`), `unmonitor(album_id)`, `album_by_mbid(mbid)`.

- [ ] **Step 1: Write failing tests** with a fake `session` returning fixture JSON: `test_recommendations_parses_fixture`, `test_recommendations_empty_raises`, `test_request_album_sends_managedby_lidarr_and_triggersearch` (assert the JSON body), `test_request_album_error_raises_with_message`, `test_library_names_are_lowercased`, `test_lidarr_monitored_albums_reports_track_file_count`.
- [ ] **Step 2: Run** `pytest tests/test_clients.py -v` — expect FAIL.
- [ ] **Step 3: Implement** both clients over `requests` with a 60s timeout; no retries (callers decide). Never log header values.
- [ ] **Step 4: Run** — expect PASS. Then run once against the live services with a throwaway script (not committed) printing only counts: expect `recommendations() > 0`, `library_artist_mbids()` ≈ 700+.
- [ ] **Step 5: Commit** `feat(discovery-bot): Aurral and Lidarr clients`.

### Task 3: Best-known album pick

**Files:**
- Create: `bot/albumpick.py`, `tests/test_albumpick.py`, `docs/discovery-album-signals.md` (one page recording the signal decision)

**Interfaces:**
- Produces `pick_album(release_groups: list[dict], popularity: Mapping[str, float]) -> Pick|None` where `Pick = (mbid, title, year, reason)`; `None` means "no confident pick, skip the artist". Pure.
- Consumes (if the signal exists): a `popularity(artist_mbid, album_mbids) -> dict[mbid, float]` provider added to `bot/aurral.py` or a small `bot/popularity.py`.

- [ ] **Step 1: Investigate the popularity signal.** Find whether album-level popularity is available from what is already running: Aurral's Last.fm integration (it already uses Last.fm data for `lastfm_related`; check its settings/routes inside the `aurral` container without printing keys), Aurral's Deezer client, or MusicBrainz ratings. Write the finding and chosen source in `docs/discovery-album-signals.md`. If no source exists, the rule is "skip unless exactly one eligible studio album, or a rating exists".
- [ ] **Step 2: Write failing tests**: `test_excludes_compilations_live_and_singles` (secondary types or non-Album primary type), `test_highest_popularity_wins`, `test_johnny_gill_resolves_to_1990` (uses `tests/fixtures/artist_johnny_gill.json` plus popularity values; expects mbid `0a32879d-47b5-3df2-8a69-1574b0c2954f`, year 1990), `test_same_title_albums_distinguished_by_mbid_and_year`, `test_no_confident_pick_returns_none` (empty popularity, multiple eligible albums, no ratings).
- [ ] **Step 3: Run** — expect FAIL. **Step 4: Implement** `pick_album`; the popularity provider per Step 1. **Step 5: Run** — expect PASS.
- [ ] **Step 6: Commit** `feat(discovery-bot): best-known album pick`.

### Task 4: Weekly candidate selection

**Files:**
- Create: `bot/picker.py`, `tests/test_picker.py`

**Interfaces:**
- Produces `select_candidates(recs, library_mbids, library_names, known_mbids, n=2) -> list[dict]` (pure): drops library artists (MBID, else case-insensitive name), `known_mbids`, ranks by `score`, applies the variety rule (no two picks share the first `sourceArtists` entry or the first `matchedTags` entry), returns up to `n`.
- Produces `build_week(aurral, store, now, n=2) -> list[Draft]` where `Draft = (rec, Pick)`: returns `[]` when `store.pending_count() >= 4`; walks ranked candidates, fetching `aurral.discography` and calling `pick_album`, skipping artists with `None`, until `n` drafts.

- [ ] **Step 1: Write failing tests**: `test_drops_library_artist_by_mbid`, `test_drops_library_artist_by_name_case_insensitively` (Lidarr "EarthGang" vs "EARTHGANG"), `test_variety_rule_avoids_same_seed_and_tag`, `test_pause_at_four_pending`, `test_skips_artist_with_no_pick_and_continues`, `test_fewer_than_n_available_returns_what_exists`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** both functions. **Step 4: Run** — PASS. **Step 5: Commit** `feat(discovery-bot): weekly candidate selection`.

### Task 5: Cards and Approve/Reject actions

**Files:**
- Create: `bot/cards.py`, `bot/actions.py`, `tests/test_actions.py`

**Interfaces:**
- Produces `build_embed(draft) -> discord.Embed` (title `Artist — Album (YYYY)`, "Because you listen to" from first 4 `sourceArtists`, first 4 `matchedTags`, cover `https://coverartarchive.org/release-group/<mbid>/front-250`, optional indexer note).
- Produces `class CardView(discord.ui.View)` with `timeout=None`, buttons whose `custom_id` are `disc:approve:<suggestion_id>` and `disc:reject:<suggestion_id>`; `register_persistent_views(client, store)` re-adds a view for every pending suggestion on startup.
- Produces `async approve(sug_id, store, aurral, lidarr, now) -> Result` and `async reject(sug_id, store, aurral, now) -> Result` where `Result = (ok: bool, message: str)`.
- Consumes: `Store`, `Aurral`, `Lidarr` from Tasks 1–2.

- [ ] **Step 1: Write failing tests** with fakes: `test_approve_requests_album_then_marks_approved`, `test_approve_failure_leaves_pending_and_returns_error`, `test_approve_twice_requests_once` (second call returns the stored outcome, `request_album` call count == 1), `test_approve_unmonitors_any_extra_monitored_album` (fake Lidarr reports a second monitored album; expects `unmonitor` called for it and not for the requested one), `test_reject_records_rejected_and_sends_less_like_this`, `test_reject_still_records_when_aurral_feedback_fails` (the bot's own list is authoritative), `test_click_on_expired_card_reports_expired`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** In `approve`, mark approved only after `request_album` succeeds; set status via the conditional `set_status` before calling Aurral so concurrent clicks cannot double-request, reverting to `pending` on failure. After the request, verify with `lidarr.monitored_albums`.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `feat(discovery-bot): cards and approve/reject actions`.

### Task 6: Scheduling, expiry, follow-ups, alerts

**Files:**
- Create: `bot/schedule.py`, `bot/main.py`, `tests/test_schedule.py`
- Modify: `bot/actions.py` (add `expire_stale`, `followup_missing`)

**Interfaces:**
- Produces `next_run(now: datetime, tz) -> datetime` (next Saturday 10:00 in tz, DST-correct) and `should_catch_up(now, last_post_at, tz) -> bool` (true if the most recent scheduled Saturday 10:00 is after `last_post_at` and within 3 days of `now`).
- Produces `expire_stale(store, now) -> list[Suggestion]` (pending older than 14 days → `expired`) and `followup_missing(store, lidarr, now) -> list[Suggestion]` (approved ≥7 days, album has 0 track files, not yet noted).
- `bot/main.py`: on ready, `register_persistent_views`, then loop every 15 minutes: run catch-up/weekly post, `expire_stale` (edit message: "expired", remove view), `followup_missing` (edit message: add "not found yet"). Any exception in a loop pass is caught and posted to #media-alerts as a short plain message, then the loop continues.

- [ ] **Step 1: Write failing tests**: `test_next_run_is_saturday_10_chicago`, `test_next_run_across_dst_change` (week of 2026-11-01 and 2027-03-14), `test_catch_up_within_three_days`, `test_no_catch_up_after_three_days`, `test_no_double_post_same_week`, `test_expire_stale_after_14_days`, `test_followup_only_when_no_track_files`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (`zoneinfo`). **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(discovery-bot): schedule, expiry, follow-ups`.

### Task 7: Informational indexer note

**Files:**
- Create: `bot/prowlarr.py`, `tests/test_prowlarr.py`
- Modify: `bot/picker.py` (attach `note` to a `Draft`), `bot/cards.py` (render it)

**Interfaces:**
- Produces `Prowlarr(base_url, key, session=None).has_release(artist, album) -> bool|None` (`None` when the search errors or times out; never raises).

- [ ] **Step 1: Find Prowlarr's local address and key** from the existing stack/config (do not print the key); record the address in `discovery-bot.env.example` as a placeholder-free hostname only.
- [ ] **Step 2: Write failing tests**: `test_has_release_true_on_results`, `test_has_release_false_on_empty`, `test_has_release_none_on_error`, `test_note_added_only_when_false` (`None` adds no note; never removes a draft).
- [ ] **Step 3: Run** — FAIL. **Step 4: Implement**; if Prowlarr proves unreliable per-album, leave `has_release` returning `None` and document why (the spec allows dropping it). **Step 5: Run** — PASS. **Step 6: Commit** `feat(discovery-bot): informational indexer note`.

### Task 8: Deploy and live end-to-end check

**Files:**
- Create: `docker-compose.yml`, `docs/discovery-bot.md` (how it works, env, how to rerun, rollback)
- Modify: `docs/discord-alerts.md` (mention the new bot posts to #media-alerts)

- [ ] **Step 1: Compose file**: service `discovery-bot`, build from this directory, `network_mode: host` (it must reach `localhost:3001/8686`), `restart: unless-stopped`, `env_file: discovery-bot.env`, named volume for the SQLite file, memory limit 256m.
- [ ] **Step 2: Create** the real `discovery-bot.env` (gitignored): Discord token and guild from `scripts/discord-bot.env`, Aurral key read from the container into the file without echoing, Lidarr key from its `config.xml`. Verify with `git status` that it is untracked and ignored.
- [ ] **Step 3: Run the full test suite** in the container: `docker compose run --rm discovery-bot pytest -q` — expect all PASS.
- [ ] **Step 4: Live dry-run**: add a one-shot flag `python -m bot.main --post-now` that runs `build_week` and posts cards immediately. Run it; expect 2 cards in #media-alerts with sensible albums (for each: correct year, cover art, "because you listen to").
- [ ] **Step 5: Live Approve**: click Approve on one card. Verify in Lidarr that exactly that album is monitored for the artist, a search ran, and the card edited to "approved". Click Reject on the other; verify a `rejected` row in the DB, `GET /api/discover/feedback` shows the entry, and the artist is absent from a fresh `--post-now` pick.
- [ ] **Step 6: Restart check**: `docker compose restart discovery-bot`, then click Approve on a still-pending card posted before the restart; expect it to work.
- [ ] **Step 7: Start normally** (`docker compose up -d`), confirm the log shows the next scheduled run, add a Kuma push monitor or log-based alert only if one already exists as the fleet pattern (check `docs/log-monitoring.md`).
- [ ] **Step 8: Docs and commit** the stack and docs; update memory `project_discovery_discord_approval.md` to "built". Commit `feat(discovery-bot): deploy`.

---

## Self-Review notes

- Spec coverage: weekly picker → T4; album choice → T3; availability note → T7; card → T5; approve/reject → T5; expiry/follow-up/catch-up/alerts → T6; persistence and double-click → T1, T5, T8; matching rules → T4; secrets/public repo → Global Constraints, T8 step 2.
- Known open item carried from the spec: whether Reject feedback via the API key shapes Aurral's own screen. T8 step 5 checks the feedback entry exists; whether it changes what Jay sees in Aurral's UI is verified by hand after a week and is not a blocker (the bot's own list guarantees non-return).
