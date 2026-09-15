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
  gemini_model: gemini-3.6-flash
  gemini_keys:
    - <key>
```

**This is a manual, per-file action**, not automatic library-wide backfill —
`PATCH /api/subtitles` with `action=translate` translates one subtitle at a time,
triggered from Bazarr's UI or API. Nothing currently loops this over the whole library
automatically.

**Verify the same way every time, don't just trust the response code:** check the actual
output file landed with real translated content, not just that the API returned 204.
