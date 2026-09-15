# Bazarr anime subtitles: why animetosho is on, Jimaku is off

Decided 2026-09-15, after a report that Bazarr wasn't grabbing subs for a freshly-aired
One Piece episode. Written down because getting here took ruling out two dead ends the
hard way, and because the AniDB registration step is not rediscoverable by searching —
anidb.net returns 403 to every automated fetch (WebFetch, curl with a browser UA, a
jina.ai reader proxy, and archive.org were all tried and all blocked).

## The original report wasn't a provider gap

All 93 tracked One Piece episodes had zero subtitle history ever, which looked like a
missing-coverage problem. It wasn't: `subdl` (which does carry official Crunchyroll
anime subs) happened to be sitting in a `DownloadLimitExceeded` cooldown at the exact
moment of the report. Once that cleared on its own, subdl found a 93%-match Crunchyroll
English sub on the very next search. Same two providers Jay already had
(`opensubtitlescom`, `subdl`) — nothing needed to change for currently-airing content.
**If this comes up again, check `/api/providers` for a cooldown before assuming a gap.**

## Jimaku: enabled, authenticated, does nothing — by design of this Bazarr build

Jimaku (jimaku.cc, anime-focused subtitle site, free API key via Discord OAuth signup)
looked like the fix for weekly fansub releases. It isn't, and can't be from Bazarr's
settings UI: the bundled provider source
(`subliminal_patch/providers/jimaku.py`) hardcodes

```python
languages = {Language.fromietf("ja")}
```

Bazarr's core provider pool (`subliminal_patch/core.py`, `list_subtitles_provider`)
intersects the languages you're searching for against a provider's *declared*
`languages` before ever calling it. Our profile wants English + Spanish, so
`{ja} ∩ {en, es}` is empty and Jimaku is silently skipped on every single search,
regardless of being enabled and successfully authenticated (`/api/providers` shows
`status: Good` the whole time — that only means the API key is valid, not that the
provider is reachable for your languages). Confirmed by testing: zero log lines
mentioning "jimaku" across multiple real, multi-second searches.

Left disabled in `enabled_providers`. The API key stays in `config.yaml`
(`jimaku.api_key`) in case a future Bazarr release fixes the language declaration —
worth re-checking before writing this off permanently, but don't re-enable it without
verifying the source has actually changed.

## animetosho: "frozen" doesn't mean dead — verify before ruling a site out

AnimeTosho (the site, not the Bazarr provider) announced a permanent freeze on
2026-05-09 — no new content added since. First instinct was to write it off entirely as
a dead end for anime subtitles. That was wrong, and it took Jay pushing back on it to
catch: **frozen means no new data, not no service.** Its API
(`feed.animetosho.org/json`) is still fully live, and its archive runs right up through
2026-05-06. That's exactly the range covering the show backlog that triggered this
whole investigation in the first place — Naruto Shippuden, Bleach, Black Clover,
Berserk, Cowboy Bebop, Death Note, Demon Slayer, Dragon Ball Kai, Dr. STONE, Baki
(2018) — all long-running or completed series well inside the archive window.

**It will not help with anything airing after ~2026-05-06.** For One Piece specifically,
that's still `subdl`'s job. For the backlog, animetosho is genuinely useful.

The five sites AnimeTosho's shutdown notice suggested as successors (ameNZB, "Anime
Tosho NEW", aninzb, TsukiHime, Otakuness) are **not usable here** — they're NZB/torrent
release indexers (AnimeTosho's actual core function), not subtitle sources. None of them
appear in Bazarr's provider list, and none could without someone writing an entirely new
Bazarr provider against them.

### Setting it up: the AniDB HTTP API registration

animetosho only returns results if Bazarr can first resolve an AniDB episode ID for the
video, via a refiner (`subtitles/refiners/anidb.py`) that calls AniDB's HTTP API. Without
valid AniDB credentials, animetosho silently returns nothing on every search — same
symptom as the Jimaku dead end, different cause. Confirmed via a real error from AniDB's
own server during testing with an unregistered placeholder client name:

```
AniDB API Client error. Client is disabled or does not exists.
```

There is no separate "API key" to copy anywhere — **the registered client name + a
single-digit version number is itself the credential**, sent as plain `client=` /
`clientver=` query parameters on every request. Registration path (not documented
anywhere Google/an LLM can currently reach, since anidb.net blocks scraping):

1. Log into an AniDB account (free).
2. Account/profile settings → **Add Project**. Fill in a project name and the basic
   metadata fields (type, state, public/private, target OS, language, contact,
   description) — none of this metadata affects whether the API works. Set
   **Public Project? → no, private only** unless you actually want to publish it.
3. Inside that project → **Add Client**. This is the step that actually matters:
   - **Client Name** — whatever string you choose; this exact string goes into Bazarr.
   - **API** — choose **HTTP**, not UDP. Bazarr's refiner calls
     `api.anidb.net:9001/httpapi` specifically.
   - **Version** — a single digit (0-9). Used `1`.
4. Once the client shows as active, there is nothing else to copy — no key, no secret,
   just the name and version you picked.

In Bazarr, set in `config.yaml` (not tracked in this repo — see below):

```yaml
anidb:
  api_client: <the client name you registered>
  api_client_ver: 1
```

Then add `animetosho` to `general.enabled_providers` and restart the `bazarr` container.

**Verify it actually works, don't trust "status: Good".** Force a manual search
(`GET /api/providers/episodes?episodeid=<id>`) on a backlog episode that's still
missing subtitles and confirm real `animetosho` results come back — `status: Good` on
`/api/providers` only means the settings parsed without a config error, exactly like it
did for Jimaku while Jimaku was doing nothing.

## subdl's free tier download quota is tiny — and separate from its request quota

Once animetosho started backfilling the anime backlog, subdl (which our profile also
leans on for regular English/Spanish content) got throttled with a "retry in 21 hours"
message almost immediately. Checked the actual history: only **17 downloads** (7
episodes + 10 movies) happened before subdl returned `DownloadLimitExceeded`, all inside
a ~25 minute window. subdl markets a generous free-tier *request* limit (thousands/day
for search and metadata calls), but actual subtitle *downloads* are metered far more
strictly and separately — confirmed empirically here, not from subdl's own docs, since
subdl.com blocks automated fetching the same way anidb.net does.

Bazarr's retry countdown for subdl is always framed as time-until-next-midnight-GMT
(`midnight_gmt_limit_reset_timedelta()` in `app/get_providers.py`), not a fixed cooldown
— that's why the wait time varies depending on when in the day the limit gets hit.

**Fixed by upgrading to SubDL Pro ($5/mo, 2,000 downloads/day)** — the same API key
carried over after upgrading, no Bazarr config change was needed. One thing that *did*
need doing manually: Bazarr's provider-throttle state is written to a file
(`config/throttled_providers.dat`) that **survives a container restart** — after
confirming the account was upgraded, the stale cooldown had to be cleared explicitly via
`POST /api/providers` with `action=reset`, rather than waiting it out or expecting a
restart to fix it.

Pro's marketing also mentions a dedicated Bazarr plugin for AI-translating missing
languages directly into the library. **Checked and it doesn't exist in this Bazarr
version** — grepped the entire `subdl` provider source and the whole app for any
AI-translation code and found none. That feature isn't wired into this Bazarr release
regardless of account tier. Bazarr's own separate built-in translator turned out to be
the real path for AI-filled Spanish — see `bazarr-subtitle-translator.md`.

## Current state (2026-09-15)

`enabled_providers`: `opensubtitlescom`, `subdl` (Pro tier), `animetosho`. Jimaku present
in config, disabled. The actual `anidb.api_client` value, the subdl API key, and the
Jimaku API key live only in Bazarr's runtime `config.yaml` under
`/srv/docker/bazarr/config/` — deliberately not reproduced here or committed anywhere in
this repo, same reason the Vaultwarden token leak was a problem before: this repo is
public.

**Backlog snapshot as of 2026-09-15, before letting the improved providers run
unattended:**

| | Episodes | Movies |
|---|---|---|
| Missing Spanish, English already present (translation could help) | 1,568 | 58 |
| Missing both languages (needs a provider to find *something* first) | 2,135 | 24 |

Decision: let `subdl` (now Pro, 2,000 downloads/day) and `animetosho` run unattended for
a few hours via Bazarr's normal scheduled search before deciding whether to build
automation around the Gemini translator for the leftover English-only gap. Most of the
library's real Spanish coverage comes from providers finding genuine subtitles, not
translation — translation is a fallback for whatever's left after that, not the primary
path.
