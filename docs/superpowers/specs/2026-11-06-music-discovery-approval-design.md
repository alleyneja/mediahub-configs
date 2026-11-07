# Weekly Music Discovery with Discord Approval — Design

**Date:** 2026-11-06
**Status:** Draft for Jay's approval
**Owner:** Jay
**Host:** mediahub-i7 (where Aurral and Lidarr run)

## Intent and why

Jay's music library is almost entirely music he already knows. Maria's complaint about
music availability is closed for known music, but the library never surprises anyone.
Apple Music and Spotify expose you to new artists without you asking. The mediahub
should do the same: a small, steady trickle of new-to-Jay music, chosen from taste
signals Aurral already collects, that Jay vets before anything is downloaded.

Success looks like: every week, one or two albums by artists Jay has never listened
to appear in #media-alerts. Jay taps Approve or Reject. Approved albums arrive in
Plex/Plexamp through the existing Lidarr flow and are logged in Lidarr like any other
music. Over months, rejections make the suggestions fit better.

## Goals

1. Once a week, post 1–2 album suggestions to #media-alerts as Discord cards with
   **Approve** and **Reject** buttons.
2. **Nothing is requested or downloaded until Jay approves.**
3. Approve sends the one chosen album to Lidarr (via Aurral), monitoring only that album.
4. Reject is remembered and the artist does not come back.
5. Everything suggested is new to Jay: not already in the library.
6. The flow reuses the existing pipeline: Lidarr → SABnzbd/qBittorrent (and the
   lidarr-ytdl fallback) → `/mnt/media/music` → Plex/Plexamp.

## Non-goals

- Building a new recommendation engine. Aurral's recommendations are the source.
- Using Aurral's own downloader or its WeeklyFlow playlists (both deliberately unused).
- Replacing Aurral's web UI. Jay can still browse and request there by hand.
- A "your album arrived" notification (possible later; not v1).
- Multi-user taste profiles. One profile (Jay's) for now.

## Anti-goals (a failure even if it technically works)

- An album downloading that Jay never saw or approved.
- Approving an artist and ending up with their whole discography.
- A rejected artist reappearing.
- A card that offers an obscure or odd album first (for example a debut, compilation or
  live record) when a well-known studio album exists.
- Cards piling up unanswered until the channel becomes noise.
- Anything secret (tokens, API keys) ending up in the repo, which is **public**.

## What the spike proved (Nov 6 2026)

All of this was tested end to end on mediahub-i7.

- **Aurral API.** Authenticates with an `x-api-key` header. `GET /api/discover` returns
  500 recommendations; all are artists, none are albums. Each has `id` (MusicBrainz ID),
  `name`, `score`, `confidence`, `matchedTags`, `sourceArtists` (the library artists that
  triggered it). None of the 500 were in the 726-artist library.
- **Discography.** `GET /api/artists/<mbid>` returns `release-groups` with `id`, `title`,
  `first-release-date`, `primary-type`, `secondary-types`, `rating` (often null).
- **Requesting.** `POST /api/library/albums/request` with `albumMbid`, `albumName`,
  `artistMbid`, `artistName`, `triggerSearch: true`, `managedBy: "lidarr"` adds the artist
  and the album in one call. Verified in Lidarr: only the requested album is monitored,
  every other album for the artist stays unmonitored, and artist-level
  `monitorNewItems` is `none`. (Aurral reports `monitorOption: "missing"` on the artist,
  which is misleading; Lidarr's own state is what matters.) `managedBy: "lidarr"` must be
  explicit; Aurral otherwise may default to its own unused downloader.
- **Rejecting.** `POST /api/discover/feedback` accepts actions `more_like_this`,
  `less_like_this`, `block_artist` with `artistId`/`artistName`.
- **Buttons.** Discord delivers button clicks only to a bot holding a live connection.
  The existing webhooks cannot receive clicks. A `discord.py` bot using the existing
  "Homelab Ops" bot token (`scripts/discord-bot.env`, gitignored) posted cards with
  buttons to #media-alerts and received both clicks.
- **Fulfilment is not guaranteed.** The three test albums were searched once by Lidarr
  and not found on the configured indexers. Lidarr does not retry on its own. The
  lidarr-ytdl scheduler (every 6 hours, 5 never-attempted albums per run) is the fallback.

## Design

### Components

One new small container, `discovery-bot`, in `stacks/discovery-bot/` (repo-managed, per
the fleet convention; deployed only on mediahub-i7). It does three jobs:

1. **Weekly picker** — builds this week's suggestions and posts the cards.
2. **Button handler** — stays connected to Discord and acts on Approve/Reject.
3. **State** — a small local database (SQLite in a mounted volume) of every artist it
   has suggested, with outcome and date.

It talks to Aurral (API key), Lidarr (reads state; its only write is the Approve safety
correction that unmonitors any extra album, never a delete), Prowlarr (read-only
searches), and Discord (bot token). Credentials come from gitignored env files, never
the repo.

### Environment facts (verified Nov 6 2026)

- All services run on mediahub-i7. Aurral: `http://localhost:3001`. Lidarr:
  `http://localhost:8686`. Prowlarr's address and key: the builder finds them in the
  existing stack (not looked up here).
- Aurral's API key lives in Aurral's own settings (retrievable inside the container; the
  spike read it into a variable and never printed it). Lidarr's key is in its
  `config.xml`. The Discord bot token and guild ID are in `scripts/discord-bot.env`
  (gitignored). The new bot gets its own gitignored env file.
- #media-alerts channel ID `1549633462351175730`. The "Homelab Ops" bot has already
  posted and edited messages there (the spike did both), so permissions are confirmed.
- The Aurral API key acts as an internal "api" admin user. Feedback and discovery
  results are stored per user, so Reject feedback sent with that key may not shape the
  recommendations Jay sees in Aurral's own UI. The builder must verify this; the bot's
  own rejected list is what guarantees an artist never returns regardless.

### Weekly picker

Runs weekly (day and time are a builder default; see "Calls I made").

1. Fetch `GET /api/discover`.
2. Drop any artist that is: already in the Aurral/Lidarr library, previously rejected,
   previously posted and still pending, or posted in the last 60 days with no answer.
3. Rank by Aurral `score`. Pick the top candidates with some variety: no two cards in
   the same week should come from the same cluster of seed artists or the same main tag,
   so a week doesn't deliver two near-identical R&B artists.
4. For each picked artist, choose **one album** (see below).
5. Check the album is actually obtainable (see below). If not, move to the next
   candidate or album.
6. Post a card per artist, record it in state as *pending*.

If more than a few cards are still unanswered, skip posting this week.

### Choosing the album

One "start here" album per artist, from the discography endpoint.

- Eligible: `primary-type` Album with no secondary types (no compilations, live, remixes,
  soundtracks).
- Prefer the best-known studio album, not the earliest one. The spike's "earliest studio
  album when ratings are missing" fallback picked Johnny Gill's 1983 debut instead of the
  well-known 1990 *Johnny Gill*; that is a known miss.
- Artists can have two albums with the same title (Johnny Gill has two). Always carry and
  display the **year** and identify the album by MusicBrainz release-group ID, never by
  title.
- How to measure "best known" is left to the builder (candidate signals: Last.fm
  playcounts that Aurral already pulls, Deezer data, MusicBrainz ratings when present).
  The test case: Johnny Gill must resolve to the 1990 album.

### Availability pre-check

**Decided by Jay (Nov 6 2026): never skip an album for lack of an indexer hit.** The
lidarr-ytdl fallback attempts every album Lidarr is missing (every 6 hours, 5 albums per
run, never-attempted albums first), so an album usenet and torrents lack can still
arrive from YouTube. The fallback is not guaranteed (YouTube bot-blocks, fingerprint
mismatches, albums not on YouTube).

So the Prowlarr check is **informational only**: if a Prowlarr search finds nothing, the
card carries a short note ("not found on usenet/torrents; will try the YouTube fallback,
may take longer or not arrive"). It never removes a card. The 7-day "not found yet"
follow-up covers albums that never arrive. If the Prowlarr check proves unreliable or
awkward to build, the builder may drop it and the note; nothing else depends on it.

### The card

One Discord message per suggestion in #media-alerts: artist, album title and year, cover
art (Cover Art Archive release-group image), and the "why": the library artists that
triggered it plus matched tags. Buttons: **Approve**, **Reject**. After a click, the
message is edited to show the outcome and the buttons are removed.

### On Approve

1. Call `POST /api/library/albums/request` (managedBy `lidarr`, triggerSearch true) with
   the MusicBrainz IDs.
2. Verify in Lidarr that only that album is monitored for the artist; if any other album
   got monitored, unmonitor it and alert.
3. Edit the card: approved, search started. Mark state *approved*.
4. If the call fails, edit the card to say so and keep the buttons so Jay can retry.

### On Reject

1. Mark the artist *rejected* in state (the bot's own list is the source of truth, so a
   rejected artist never reappears even if Aurral's feedback behaves unexpectedly).
2. Send Aurral feedback so its recommendations adapt. Which action to use is a builder
   choice; `less_like_this` teaches taste, `block_artist` hides the artist outright.
3. Edit the card: rejected.

### Unanswered cards

Cards unanswered after 14 days are edited to "expired" and the buttons removed. The
artist is eligible again after 60 days.

### Matching rules

Match artists and albums on **MusicBrainz ID first**. Fall back to **case-insensitive
name** match only when an ID is missing (Aurral says "EARTHGANG", Lidarr says
"EarthGang"; the spike's name lookup broke on this).

### Robustness

- Button handling survives a container restart: pending cards are reconstructed from
  state, so old cards still work.
- Double-clicking a button does not request twice (the handler checks state first).
- If Aurral or Lidarr is down at pick time, skip the week and log; don't post partial
  results. Failures go to the existing alerts channels rather than silently vanishing.
- Never deletes anything in Lidarr, Aurral or on disk.

## Constraints

- mediahub-configs is public: no tokens, keys, or personal data in the repo.
- Follow the fleet convention: stack lives in `stacks/discovery-bot/`, pinned image tag,
  deployed from the repo, committed before the session ends.
- Aurral is pinned (currently 2.10.0). API paths above are for this version; the bot
  should fail loudly, not silently, if they change.
- No blanket auto-update; the bot's own dependencies are pinned.
- Prefer usenet over torrents (existing policy; Lidarr handles this).

## Calls I made (Jay: please check these)

- Posts **2 cards per week**, matching "one or two albums a week".
- Weekly schedule: Saturday morning, since cards are read at leisure. Builder default.
- **Variety rule** in the weekly pick so two similar artists don't arrive together.
- Cards expire after **14 days**; artist is eligible again after **60 days**.
- Posting pauses if more than a few cards are unanswered.
- "In the library" means the artist exists in Lidarr/Aurral at all, monitored or not.
  That includes the spike's Johnny Gill and EarthGang, which are no longer suggestions.
- Reject uses `less_like_this` plus the bot's own rejected list. It does not use
  `block_artist`, to avoid hiding similar artists household-wide.
- "More than a few unanswered" means **4 or more** pending cards. Approved albums do not
  count toward it and are never re-requested by the bot.
- If an approved album has no files in Lidarr after 7 days, the bot adds a short note to
  its card ("not found yet") so a silent failure is visible. No re-request.
- If no confident "best-known" album can be picked for an artist, skip that artist
  rather than guess. This applies if no popularity signal turns out to be available.
- Failure alerts go to #media-alerts (the media category channel).
- Schedule time zone is America/Chicago. If the container was down at the scheduled
  time, it posts on restart if that is within 3 days of the schedule, otherwise it waits
  for the next week.
- Anyone who can see #media-alerts may click the buttons (no per-user restriction).
  Jay confirmed he is the only member of #media-alerts, so this is fine for now. If
  others are ever added to the channel, add an allowed-user check first.
- The bot runs on mediahub-i7 next to Aurral and Lidarr, not on r9.
- Python with `discord.py`, in a container, SQLite for state.
- Availability pre-check via Prowlarr before posting (feasibility unverified).
- Test artifacts left in Lidarr from the spike: Johnny Gill (artist 680; the 1990 and 1983
  albums monitored) and EarthGang (artist 681; *Mirrorland* monitored). These are not
  created by the feature and can stay or be cleaned up by Jay.

## Left to the builder

Language and library details, exact schedule, scoring and variety logic, how to rank
"best-known album", card wording and layout, state schema, how to express fallback-only
availability, logging and alert wiring, tests.
