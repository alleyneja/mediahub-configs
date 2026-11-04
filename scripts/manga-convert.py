#!/home/jay/tools/kcc-venv/bin/python
"""Convert Suwayomi webtoon/manga CBZ downloads into Kindle-ready EPUBs via KCC.

Source:  /mnt/media/manga/mangas/<source>/<series>/<chapter>.cbz   (Suwayomi)
Output:  /mnt/media/manga/converted/<series> [<source>]/<series> - <chapter>.epub
         (Kavita "Converted (Kindle)" Book library; use its Send To button)

Idempotent: a chapter is skipped when its EPUB already exists. A CBZ is only
converted once it opens as a complete zip (partial downloads fail that) and
Suwayomi's queue no longer holds it. Run from cron
under flock (see docs/manga-pipeline.md). Never touches the source CBZs.

KCC lives in a uv venv outside the repo: ~/tools/kcc-venv (installed from
github.com/ciromattia/kcc). Profile KPW6 = Paperwhite 6/2026 (Jay's Kindle).
"""
import argparse
import re
import io
import statistics
import zipfile
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path("/mnt/media/manga/mangas")
DST = Path("/mnt/media/manga/converted")
KCC = Path.home() / "tools/kcc-venv/bin/kcc-c2e"
PROFILE = "KPW6"
SETTLE_SECONDS = 10         # small grace for the NFS/mergerfs view of a just-closed file
FALLBACK_SETTLE_SECONDS = 120   # used only if Suwayomi's API cannot be queried
SUWAYOMI_URL = "https://suwayomi.lan/api/graphql"   # via Caddy on this host (4567 is not published)
MAX_PER_RUN = 6        # ~20 s each; keeps one cron run short


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def chapter_label(stem: str) -> str:
    # Suwayomi names files "<scanlator>_<chapter>"; drop the scanlator prefix.
    return safe(stem.split("_", 1)[1] if "_" in stem else stem)


def suwayomi_queue():
    """(series, chapter) pairs Suwayomi is downloading or has queued, or None
    if its API is unreachable (caller then falls back to a longer settle)."""
    import json
    query = json.dumps({"query": "{ downloadStatus { queue { chapter { name manga { title } } } } }"})
    try:
        # --resolve pins the Caddy vhost to this host (no DNS dependency); -k because
        # Caddy's internal CA is not in the host trust store.
        r = subprocess.run(
            ["curl", "-sk", "--max-time", "10", "--resolve", "suwayomi.lan:443:127.0.0.1",
             SUWAYOMI_URL, "-H", "Content-Type: application/json", "-d", query],
            capture_output=True, text=True, timeout=15)
        queue = json.loads(r.stdout)["data"]["downloadStatus"]["queue"]
    except Exception as e:   # unreachable, bad JSON, schema change...
        print(f"queue check unavailable ({type(e).__name__}); using {FALLBACK_SETTLE_SECONDS}s settle",
              file=sys.stderr)
        return None
    return {(safe(i["chapter"]["manga"]["title"]), safe(i["chapter"]["name"])) for i in queue}


def is_complete(cbz: Path) -> bool:
    """A zip is unreadable until fully written (its index is at the end), so a
    clean open + CRC pass means Suwayomi has finished the file."""
    try:
        with zipfile.ZipFile(cbz) as z:
            return z.testzip() is None and any(
                n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def is_webtoon(cbz: Path) -> bool:
    """Long vertical strips (height >> width) need KCC webtoon mode; normal
    page-by-page manga must NOT use it (it would stitch pages together)."""
    from PIL import Image
    ratios = []
    with zipfile.ZipFile(cbz) as z:
        names = sorted(n for n in z.namelist() if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
        for n in names[:: max(1, len(names) // 8)][:8]:
            with Image.open(io.BytesIO(z.read(n))) as im:
                ratios.append(im.height / im.width)
    return bool(ratios) and statistics.median(ratios) > 2.0


def add_cover(epub: Path) -> None:
    """KCC's webtoon EPUBs declare no cover; mark the first page image as one."""
    opf_name = "OEBPS/content.opf"
    out = epub.with_suffix(".cover.tmp")
    with zipfile.ZipFile(epub) as zin:
        opf = zin.read(opf_name).decode("utf-8")
        m = re.search(r'<item id="([^"]+)" href="(Images/[^"]+)" media-type="image/[^"]+"/>', opf)
        if not m or "cover-image" in opf:
            return
        item = m.group(0)
        opf = opf.replace(item, item.replace("<item ", '<item properties="cover-image" ', 1), 1)
        opf = opf.replace("</metadata>", f'<meta name="cover" content="{m.group(1)}"/>\n</metadata>', 1)
        with zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():   # preserves order: mimetype stays first
                data = opf.encode("utf-8") if info.filename == opf_name else zin.read(info.filename)
                zout.writestr(info, data, compress_type=info.compress_type)
    out.replace(epub)


def convert(cbz: Path, series: str, label: str, out: Path, dry: bool) -> bool:
    title = f"{series} - {label}"
    if dry:
        print(f"[dry-run] {cbz} -> {out}")
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir="/var/tmp", prefix="manga-convert-") as tmp:
        tmp = Path(tmp)
        src = tmp / "in" / "chapter.cbz"
        src.parent.mkdir()
        shutil.copyfile(cbz, src)
        webtoon = is_webtoon(cbz)
        print(f"{title}: {'webtoon' if webtoon else 'standard manga'} mode")
        res = subprocess.run(
            [str(KCC), "-p", PROFILE, "-f", "EPUB", "-m", "--nokepub",
             *(["-w"] if webtoon else []),
             "-t", title, "-a", series, "-o", str(tmp), str(src)],
            capture_output=True, text=True)
        epubs = list(tmp.glob("*.epub"))
        if res.returncode != 0 or len(epubs) != 1:
            print(f"FAILED {cbz}: rc={res.returncode} {res.stdout[-300:]} {res.stderr[-300:]}",
                  file=sys.stderr)
            return False
        add_cover(epubs[0])
        part = out.with_suffix(".epub.part")
        shutil.copyfile(epubs[0], part)
        part.rename(out)   # atomic: Kavita never sees a half-written file
    print(f"converted {title}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not KCC.exists():
        print(f"KCC not found at {KCC}", file=sys.stderr)
        return 2

    queued = suwayomi_queue()
    settle = SETTLE_SECONDS if queued is not None else FALLBACK_SETTLE_SECONDS
    done = failed = 0
    for cbz in sorted(SRC.glob("*/*/*.cbz")):
        age = time.time() - cbz.stat().st_mtime
        if age < settle:
            continue
        series = safe(cbz.parent.name)
        if queued and any(series == qs and qc and qc in cbz.stem for qs, qc in queued):
            continue   # Suwayomi still has this chapter queued/running
        if not is_complete(cbz):
            print(f"skip {cbz.name}: archive not complete yet")
            continue
        source = safe(re.sub(r"\s*\([A-Za-z-]{2,5}\)$", "", cbz.parent.parent.name))  # "TCB Scans (EN)" -> "TCB Scans"
        label = chapter_label(cbz.stem)
        # Source in the folder so the same series from two sources never collides.
        out = DST / f"{series} [{source}]" / f"{series} - {label}.epub"
        if out.exists():
            continue
        if done >= MAX_PER_RUN:
            print("batch limit reached; remaining chapters next run")
            break
        if convert(cbz, series, label, out, args.dry_run):
            done += 1
        else:
            failed += 1
    print(f"done={done} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
