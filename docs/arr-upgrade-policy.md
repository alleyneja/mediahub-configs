# \*arr upgrade policy — auto-grab new, never auto-upgrade

## 2026-08-03 — automatic upgrades disabled

**Decision:** Sonarr and Radarr should grab content that is *missing*, and never
replace content already in the library. Upgrades are a manual choice.

**Rationale (Jay's):** storage and breadth matter more than per-file quality.
A compressed copy is nearly always indistinguishable in practice, but the NAS
notices. Automatic upgrades were consuming bandwidth and disk to replace files
that were already fine.

**Applied** — `upgradeAllowed = false` on all four profiles:

| App | Profile | Series/movies affected |
|---|---|---|
| Sonarr | 1 — Streaming Minimums | 101 |
| Sonarr | 4 — Anime Profile | 28 |
| Radarr | 1 — Streaming Minimums | — |
| Radarr | 7 — Anime Profile | — |

**Effect:**

| | before | after |
|---|---|---|
| Sonarr episodes "cutoff unmet" | 1,126 | 112 |
| Radarr movies "cutoff unmet" | 51 | 40 |
| Sonarr episodes missing (still auto-grabbed) | 796 | 796 |
| RSS sync result | grabbing upgrades | 500+ reports found, **0 grabbed** |

The residual "cutoff unmet" entries are files below the profile's *allowed*
range rather than merely below cutoff. They show in the report as a manual
to-do list but are not acted on.

## Important limitation

`upgradeAllowed = false` governs **grabbing**, not **importing**. A release that
was already downloaded before the change will still import over an existing
file. Observed on The Studio S01E01: grabbed 2026-08-03 00:39, imported over a
3.72 GB file with a 2.19 GB one after the policy was set.

So the policy stops new upgrade grabs, but any already in the pipeline must be
removed from the download client to prevent them landing.

This was initially misread: 10 completed Bob's Burgers jobs appeared to have
been "refused" by the policy, when in fact SAB's post-processing was wedged and
nothing had reached the import stage at all.

## Manual upgrades

Unchanged — use interactive search. The cutoff-unmet list is now a useful
shortlist of candidates rather than a queue of automated churn.

## Related

The churn this policy ends was previously mitigated by tuning
`minUpgradeFormatScore` and `cutoff` — see `docs/sonarr-quality-profile.md`.
That tuning is now largely moot, but the profile values remain as documented.

Note `cutoffFormatScore = 200` on profile 1 is unreachable for single-language
shows (200 requires matching both `Language: English` +100 and
`Language: Spanish` +100). With upgrades disabled this no longer causes grabs,
but it would resume if `upgradeAllowed` were ever turned back on.
