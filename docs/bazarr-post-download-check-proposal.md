# Draft: Bazarr feature suggestion, "verify a downloaded subtitle against the audio" (2026-11-18)

Status: DRAFT for Jay. Nothing has been posted. Written to the rules in `upstream-issue-standard.md`: redacted (no hostnames, IPs, titles tied
to people), AI assistance disclosed, one idea per post. Private tracking issue: mediahub-issues #88.

## Where it can go (checked 2026-11-18)
- Bazarr's GitHub issues are for bugs only: `blank_issues_enabled: false`, the only template is `bug_report.md`, Discussions are off, and the
  config sends feature ideas to **https://bazarr.featureupvote.com/**. The maintainer closed a nearby request (#3336, "show subtitle quality
  score and sync status") as "more a feature request than an issue".
- The maintainer does merge evidence-backed reports quickly (#3599 and #3608, both about the sync quality guard, were fixed within weeks) and
  says he "would gladly accept a PR with the final implementation" for a larger change.
- Related, already shipped in 1.6.2: a quality guard inside the sync step (`--skip-sync-on-low-quality`, min score, max offset). It protects
  the sync step. It does not check a subtitle that never went through sync, a subtitle for the wrong episode or cut, or a truncated file.
- Searches of open and closed issues for "verify", "quality check", "wrong episode subtitle", "post-download" found no existing request for this.
  Closest: #234 (2018, "bad subtitle blacklist", closed), #821 (2020, "subtitle score is not correlating with matching results", closed).

So the honest options are: (1) a featureupvote suggestion (text below), (2) an opt-in pull request later if the suggestion gets traction or
the maintainer asks for one, (3) both. A GitHub bug report would be wrong: nothing here is a defect.

## Suggestion text (for featureupvote)

**Title:** Optional check that a downloaded subtitle actually matches the audio, with automatic retry

**What I'd like:** After a subtitle is downloaded (and synced, if sync is on), Bazarr could optionally check it against the video's audio.
If it clearly does not match (wrong episode or cut, drifting, truncated, two tracks merged), treat it like a failed download: blacklist it
and try the next candidate, instead of keeping it.

**Why:** Provider scores come from release metadata, never from the content. In my library a 53-cue file scored 94%. The sync step applies
one shift and, until 1.6.2, nothing rejected a bad result; the new guard helps but only covers files that go through sync. Over a library of
about 15,000 English subtitles, an audit against the audio flagged about 10% (a floor: it caught 4 of 5 known-bad files), and a smaller
random audit found roughly 18% clearly off. A subtitle timed to another cut of the video cannot be fixed by any global shift, so the only
fix is a different subtitle, which is what Bazarr's retry already does once something marks the first one bad.

**How it could work (it is what I run today, outside Bazarr):**
- Take 5 to 7 short audio windows spread across the video (180 to 480 s each), cut the cues that fall in each window, and run ffsubsync, which
  Bazarr already bundles, to read the offset per window.
- PASS if every usable window agrees within a small bound (0.75 s for episodes) and the last cue is near the end of the video. FAIL only on
  positive evidence: windows that disagree, a large consistent shift, a truncated file, overlapping cues. If there is too little speech to
  tell (4 or fewer usable windows), say "unverifiable" and leave the subtitle alone. Never replace on "unverifiable".
- One stray window is allowed if the other windows pass (openings and recaps produce a few).
- Benchmarked on known-good subtitles with injected faults: shifts of 1 s or more, drift, truncation and different cuts were caught
  (shifts of 5 s or more: 96 of 96; 1 s: 32 of 32); untouched good subtitles were never flagged in my tests (0 of 16 for movies, 8 of 8 for
  episodes; too small a sample to prove a low false-positive rate).
- Opt-in per profile, off by default, cost-aware (it takes about a minute per file at low priority), and the existing blacklist/retry
  machinery does the rest.

**Results so far outside Bazarr:** it has replaced bad subtitles on movies and on 65 episodes; every episode I spot-checked in
a player was in sync. Two things were not obvious beforehand: a "bad subtitle" is often a different cut, not a shifted one (0 of 42 flagged
episodes were fixable by a constant shift), and matching the release to the library file matters (subtitle packs for the same encode pass
where HDTV-era subtitles fail).

*Investigated with the help of an AI assistant (Claude); findings and numbers verified by me.*

## Before posting (Jay)
1. Read it and cut anything you do not want under your name; check the numbers against `docs/subtitle-quality-gate.md` and
   `docs/subtitle-tv-mode.md`.
2. Decide: suggestion only, or suggestion plus an offer to send a PR for an opt-in `verify after download` step.
3. Post on featureupvote with your account; I cannot and will not. Then add the link to #88.

## Update 2026-11-18 (evening): what featureupvote already has (Jay pasted the list; my fetch was blocked with 403)
Closest existing suggestions:
- **"Auto blacklist downloaded subtitle if sub synchronization fails"** (Potato quality, Sep 2020): 20 votes, Under consideration, 0 comments. Body: "If subtitle synchronization fails (offset higher then 600) blacklist the subtitle automatically." This is the same idea in its smallest form.
- "Mass sync all subtitles" (262 votes, **Won't happen**): shows how many people have out-of-sync libraries, but it is a bulk action, not a check, and it cannot fix a subtitle timed to another cut.
- "Be able to choose between Audio and Sub synchronisation or both" (23 votes), "Add alass as subtitle sync tool" (48), "Add LAPSE as a subtitle sync engine option" (25): sync engines, not verification.
Not seen: anything about wrong-episode, wrong-cut or truncated subtitles, or a check on files that were not synced. The list I was given may be only the first page (about 50 items, ordered by votes).
What Bazarr does now (PR #3625, merged 2026-09-30, fixes #3608): when ffsubsync rejects an alignment, Bazarr keeps the original subtitle and logs a warning. It does not blacklist the subtitle or try another one. That is exactly the gap the 2020 suggestion describes.

**Recommendation:** do not open a new suggestion. Upvote the 2020 one and add a comment that extends it (our standard says to add to an existing request rather than duplicate). Comment text:

> Voting for this, and adding what's changed since 2020. Since 1.6.2 (PR #3625) a rejected sync keeps the original file and logs a warning, but the subtitle is not blacklisted, so the next search can pick the same one again, and nothing checks files that never went through sync (wrong episode or cut, truncated file, a signs-only forced track saved as the full English subtitle). A provider score is metadata only; in my library a 53-cue file scored 94%.
> I run an external check that measures a downloaded subtitle against the audio at 5 to 7 points with ffsubsync, fails only on positive evidence (windows disagree, a large consistent shift, truncation, overlapping cues) and leaves it alone as "unverifiable" when there is too little speech. When it fails, the subtitle is blacklisted and the next candidate is tried. Across a library of about 15,000 English subtitles it flagged about 10% (a floor), and on the roughly 65 episodes and the movies I've replaced with it, every one I checked in a player was in sync. Happy to share the method or send a PR for an opt-in version if that would help.
> (Investigated with the help of an AI assistant, Claude; numbers verified by me.)

**Second look (Jay searched "blacklist" and "verify"):** nothing new beyond the 2020 request, but two supporting ones: "Adding a list of failed subtitle synchronizations" (6 votes, 2021, Under consideration: a History-tab list with a button to blacklist and search again) and "More max offset second options, use as quality controller" (2 votes, July 2025: "subs that get synced +5 seconds are bad subs and good subs only get sync with less than one second", i.e. the same signal we use). "Verify" returned only these two or fewer, so no request exists for checking wrong-cut or truncated subtitles or files that skipped sync. Plan unchanged: upvote the 2020 request, post the comment; optionally upvote these two and mention them in the comment.

**Posted 2026-11-18 by Jay:** upvoted "Auto blacklist downloaded subtitle if sub synchronization fails" on bazarr.featureupvote.com and added a comment (856 characters, the shortened version of the text above, without the two supporting suggestions). Nothing else filed upstream. Watch for a maintainer reply; if he asks for a PR, scope an opt-in "verify after download" step against the existing blacklist/retry path.
