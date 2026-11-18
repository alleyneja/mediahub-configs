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
