# Manga pipeline (Suwayomi -> KCC -> Kavita -> Kindle)

Set up Nov 2-4 2026. Everything runs on mediahub-i7 (production).

```
Suwayomi (suwayomi.lan)  --CBZ-->  /mnt/media/manga/mangas/<source>/<series>/*.cbz
   |  scripts/manga-convert.py  (cron */15, flock, KCC webtoon mode, profile KPW6)
   v
/mnt/media/manga/converted/<series> [<source>]/<series> - <chapter>.epub
   |  Kavita library "Converted (Kindle)" (type Book)
   v
Kavita "Send To" -> Gmail SMTP -> Kindle address
```

## Pieces
| Piece | Where |
|---|---|
| Suwayomi stack | `stacks/suwayomi/` (config/DB `/srv/docker/suwayomi/data`, ext4) |
| Kavita stack | `stacks/kavita/` (config/DB `/srv/docker/kavita/config`, ext4) |
| Converter | `scripts/manga-convert.py`; log `~/logs/manga-convert.log` |
| KCC | uv venv `~/tools/kcc-venv`, installed from github.com/ciromattia/kcc (not on PyPI) |
| Kavita libraries | `Manga-test` -> `/manga/mangas/MangaDex (EN)` (read in browser); `Converted (Kindle)` (Book) -> `/manga/converted` |

## Why each piece exists / gotchas
- **Webtoon vs standard**: the script samples page shapes per chapter (median height/width > 2 = webtoon) and only uses KCC `-w` for strips; page-by-page manga (e.g. One Piece) converts without it, right-to-left (`-m`). Needs Pillow, so cron runs the venv python.
- **Webtoons**: MangaDex-style webtoons are ~690 px wide, 2000-9500 px tall strips. KCC `-w` cuts them into
  screen-sized pages; without it a Kindle shrinks a strip to an unreadable sliver.
- **Kavita "Send To" only offers EPUB/PDF** (not CBZ). Hence the Book library of converted EPUBs.
  A Book library needs `enableMetadata=true` or the scan logs "Unable to parse any meaningful information".
  Files must sit in a series folder, not the library root.
- **KCC webtoon EPUBs declare no cover**; the script marks the first page `cover-image` (add_cover()).
- **Suwayomi keeps thumbnails inside its downloads dir** (`/mnt/media/manga/thumbnails`), so Kavita's Manga
  library points at the `mangas/<source>` subfolder, never `/manga`.
- **Each converted chapter is its own Kavita "series"** (title comes from the EPUB, "<series> - Ch.N").
- **Kavita folder watching is disabled at the server level** (`enableFolderWatching: false`), so the per-library toggle does nothing; new EPUBs only appeared after a manual scan. Fixed Nov 3 by setting the scheduled scan (Admin -> Tasks, `taskScan`) to `*/15 * * * *`. Worst-case lag for a new chapter is ~15 min convert + ~15 min scan.
- Kavita email = same Gmail SMTP as Calibre-Web-Automated (settings live in Kavita's DB, not this repo).
  Kindle device added under Kavita Settings -> Devices.
- **Source in the output folder** (`<series> [<source>]`): the same series from two sources (e.g. TCB Scans and
  MangaDex) would otherwise collide on `<series> - <chapter>.epub` and the second would be silently skipped.
- **Suwayomi sources kept (Nov 4 2026):** TCB Scans, MangaDex, MangaFox, MangaFire, MangaKatana, Asura Scans,
  Webtoons.com, MANGA Plus (official, latest chapters only), plus two mature sources: Hiperdex and Mangago.
  Survey of 38 candidates: each tested with search + chapter list + first-page image fetch (3 series each).
  Paid/login storefronts (Kodansha, Tappytoon, Tapas, Mangamo, Comikey, Honeytoon, BookWalker) list titles but
  cannot serve pages; Manganato (Cloudflare) and a few others returned nothing. Sources break often; re-test.
- Source CBZs are never modified; deleting `/mnt/media/manga/converted` is always safe (re-converts).
- Profile `KPW6` is a best guess for a 16 GB Paperwhite; try `KPW5` if margins look wrong.

## Removal
`docker compose down` in both stacks, delete `/srv/docker/{kavita,suwayomi}`, remove the cron line,
Caddy blocks, AdGuard rewrites and Homepage tiles (commit 950756c added them), `rm -rf ~/tools/kcc-venv`.
