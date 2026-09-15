# Bazarr's built-in translator: default reports success, does nothing — Gemini works

Found and confirmed 2026-09-14, while checking whether AI-translated Spanish subtitles
were actually being produced. Written down because the failure mode is silent — Bazarr
tells you it worked. Fixed the same day by switching to the Gemini backend, which took
its own round of trial and error to land on a model name that actually works.

## What's actually configured

`general.translator_type` defaults to `google_translate` — the free, unofficial Google
Translate scraper library, not an authenticated Google API. No key required to turn it
on, which is exactly why it looks like it's ready to use.

## Why it doesn't work

Bazarr's translate action sends **one HTTP request per subtitle line**, not batched.
Google's free scraped endpoint hard-caps at 5 requests/second. Any real subtitle file —
hundreds of lines — blows through that in under a second. Confirmed live: triggering
`PATCH /api/subtitles` with `action=translate` on a real episode produced dozens of
consecutive log entries in a few hundred milliseconds:

```
Google Translate API error after retries: Server Error: You made too many requests to
the server. According to google, you are allowed to make 5 requests per second and up
to 200k requests per day.
```

**The API call still returns `HTTP 204` (success).** The request itself is accepted and
processed without throwing a fatal exception — that's all 204 means here. No translated
`.srt` file is produced. There is no error surfaced to the Bazarr UI beyond the log.

**Don't trust the response code for this endpoint.** Verify by checking for the actual
output file on disk, same rule as verifying a subtitle provider is really returning
results instead of trusting `status: Good` on `/api/providers`.

## The real fix: the Gemini backend — but the default model name is dead too

Bazarr also ships a Gemini-based translator
(`subtitles/tools/translate/services/gemini_translator.py`), which calls Google's actual
Gemini API instead of scraping the consumer-facing Translate page — no per-second wall
like the free scraper.

To get a key: **aistudio.google.com/apikey**, sign in with a Google account, generate a
key. Free, instant, no approval flow — unlike the AniDB/Jimaku registrations documented
in `bazarr-anime-subtitle-providers.md`.

**The model name matters and churns fast — don't trust Bazarr's shipped default
(`gemini-2.0-flash`), it's fully deprecated and 404s immediately.** Getting to a working
model name took three attempts, each teaching something worth keeping:

1. `gemini-2.0-flash` (Bazarr's default) → immediate 404, deprecated.
2. `gemini-flash-latest` → model resolved, but hit two consecutive `503 Service
   Unavailable` errors.
3. `gemini-2.5-flash` (a real, established, non-preview model — confirmed present in
   the account's own `/v1beta/models` listing) → a *different* 404, with an explicit,
   useful message: `"This model models/gemini-2.5-flash is no longer available to new
   users. Please update your code to use models/gemini-3.6-flash."` **A brand-new API
   key/project only gets access to currently-new-user-eligible models — a model showing
   up in the listing endpoint doesn't mean a new key can actually call it.**
4. `gemini-3.6-flash` → confirmed working, first via a direct `curl` against the raw
   Gemini API, then end-to-end through Bazarr: a real, correctly-timed, accurate
   3,202-line Spanish translation of a Money Heist episode.
5. **`gemini-3.6-flash`'s free tier is capped at 20 requests/day, period** — hit this
   almost immediately from repeated testing. Confirmed via the API's own 429 response
   body, which names the exact quota:
   ```
   "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
   "quotaValue": "20", "model": "gemini-3.6-flash"
   ```
   Bazarr batches ~300 subtitle lines per API call, so a typical episode (800+ entries)
   costs ~3 requests — this model can only process ~6-7 episodes/day. Useless for any
   real backlog. **Switched to `gemini-3.5-flash-lite`** (Google's own explicitly
   recommended lite replacement, surfaced in the 404 messages above) instead — lite-tier
   models consistently get much larger free daily quotas than full flash models, though
   the exact number here was never confirmed (didn't want to burn requests testing it to
   exhaustion). If bulk translation stalls, check for the same `RESOURCE_EXHAUSTED` /
   `quotaId` error pattern before assuming anything else broke — it names the model and
   the limit directly.

**How to re-derive the current working model if this breaks again** (it will — Gemini's
lineup moves fast): `curl "https://generativelanguage.googleapis.com/v1beta/models?key=<key>"`
to see the account's actual list, then test candidates directly with `curl` against
`:generateContent` — a 404 response often names the exact model to use instead, as it
did here. Confirm outside Bazarr before changing its config.

**It's slow — that's normal, not hung.** A full episode took roughly 2 minutes (this
model has "thinking" token overhead per batch). Bazarr writes a `.progress` sidecar file
next to the source subtitle while a translate job is active — check that and the
container's CPU usage before assuming a stuck job.

Set in `config.yaml` (not tracked in this repo — same reasoning as elsewhere, it's a
credential):

```yaml
translator:
  translator_type: gemini
  gemini_model: gemini-3.5-flash-lite
  gemini_keys:
    - <key>
```

## A second, separate bug: translations never actually mark the episode as done

Even with a working model, the first several real translations produced a correct file
on disk that Bazarr **still reported as missing** forever after. Traced to
`api/subtitles/subtitles.py`'s `postprocess_subtitles()`: the call to `store_subtitles(id)`
— the function that updates Bazarr's own "missing subtitles" database — is nested inside
`if chmod:`, which only evaluates truthy when `general.chmod_enabled` is `True`. It
defaults to `False`. So every translate action silently skipped its own bookkeeping step.

Fixed by setting `general.chmod_enabled: true` (the `general.chmod: '0640'` value was
already sitting in config, just unused). Confirmed this doesn't break anything else:
Plex runs as the same host user (`jay`, matching Bazarr's `PUID`), so `0640` (owner
read/write) doesn't block it from reading subtitles.

**Verify translations two ways, not one:** check the output file has real content *and*
that the item actually drops off `GET /api/episodes/wanted` (or `/api/movies/wanted`)
afterward — a `204` response and even a populated file on disk both looked like success
while this bug was still live.

## Bulk backlog script

`stacks/arr-stack/scripts/bazarr-translate-missing-es.py` in this repo re-derives its
worklist from Bazarr's live wanted list on every run (Spanish missing, English already
present) and calls the translate action for each — safe to stop and re-run any time,
since completed items now correctly disappear from the list thanks to the chmod fix
above. It stops itself after 5 consecutive failures/timeouts (quota exhaustion, API
outage) rather than grinding uselessly through the rest of a long list. Logs to
`/srv/docker/bazarr/config/translate-missing-es.log`.

**This only helps the subset of the backlog with English already present and no
Spanish anywhere.** Most real Spanish coverage should keep coming from the subtitle
providers themselves (`bazarr-anime-subtitle-providers.md`) finding genuine subtitles —
translation is a fallback for what's left after that, not the primary path.
