# Discovery bot: album popularity signal

Decision date: 2026-11-13. Question: what album-level "best known" signal already exists in the running stack?

## What was checked (read-only)

- **Aurral Last.fm client** (`lastfmRequest`): only used for artist-level data (similar artists, listeners, playcount). It calls `album.getInfo` internally for Weekly Flow track picking, but no HTTP route exposes album-level Last.fm data, so the bot cannot reach it without a second Last.fm key and client.
- **Aurral Deezer client**: used for artist links, covers and track previews. No album popularity/rank is exposed by any route.
- **Aurral artist route** `GET /api/artists/<mbid>`: each release group carries `rating`, from MusicBrainz via Aurral's metadata provider, shaped `{count, value}` (value on a 0-10 scale; confirmed by a live artist-level rating of 9 from 2 votes in tests/fixtures/artist_johnny_gill.json, which a 0-5 scale could not produce) or `null`. Returned in the same single call the bot already makes for the discography.
- `GET /api/release-group/<mbid>` returned 404 for a studio album sampled; the batch ratings route is POST-only and is not needed since the data is already in the artist response.

## Coverage (10 random recommendation artists, studio albums only)

Most had ratings on most albums (for example 13/17, 9/9, 9/10, 10/11, 4/4); two had none (0/7, 0/8) and one had 1/8. Vote counts are often tiny (1 to 3). Known gap: the Johnny Gill fixture artist has no ratings at all.

## Decision

Use the MusicBrainz `rating` from the artist call. Cost: zero extra requests per artist. Because small vote counts are noisy, scores are shrunk toward a prior (`(count*value + 5*6.0) / (count + 5)`), so one 10/10 vote does not beat a well-voted 8.

`pick_album` (pure) picks the eligible studio album (primary type Album, no secondary types) with the highest score. A tie at the top returns no pick.

## Fallback rule

If no eligible album has a rating: pick only when exactly one eligible studio album exists; otherwise return no pick and skip the artist. Skipping artists such as Johnny Gill is accepted; the bot has 500 recommendations to draw from.

## Not done

Last.fm album playcounts would be a stronger signal and cover unrated artists. It would need a Last.fm API key in the bot and one call per album. Revisit if too many artists are skipped.
