# Bazarr's built-in translator: reports success, does nothing

Found and confirmed 2026-09-14, while checking whether AI-translated Spanish subtitles
were actually being produced. Written down because the failure mode is silent — Bazarr
tells you it worked.

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

## The real fix: switch to the Gemini backend

Bazarr also ships a Gemini-based translator
(`subtitles/tools/translate/services/gemini_translator.py`), which calls Google's actual
Gemini API instead of scraping the consumer-facing Translate page — no per-second wall
like the free scraper. `translator.gemini_model` was already sitting at
`gemini-2.0-flash` in config, just missing `translator.gemini_keys` (an empty list by
default — it supports multiple keys with automatic cooldown rotation between them).

To get a key: **aistudio.google.com/apikey**, sign in with a Google account, generate a
key. Free, instant, no approval flow — unlike the AniDB/Jimaku registrations documented
in `bazarr-anime-subtitle-providers.md`.

Once obtained, set in `config.yaml` (not tracked in this repo — same reasoning as
elsewhere, it's a credential):

```yaml
translator:
  translator_type: gemini
  gemini_keys:
    - <key>
```

**Verify the same way, don't just trust it this time either:** re-run the same
`action=translate` test, then check the actual `.es.srt` (or whatever target language)
landed on disk with real translated content, not just that the API returned 204.
