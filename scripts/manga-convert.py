#!/home/jay/tools/kcc-venv/bin/python
"""Convert Suwayomi webtoon/manga CBZ downloads into Kindle-ready EPUBs via KCC.

Source:  /mnt/media/manga/mangas/<source>/<series>/<chapter>.cbz   (Suwayomi)
Output:  /mnt/media/manga/converted/<series>/<series> - <chapter>.epub
         (Kavita "Converted (Kindle)" Book library; use its Send To button)

Idempotent: a chapter is skipped when its EPUB already exists. Run from cron
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
SETTLE_SECONDS = 120   # skip files still being written by Suwayomi
MAX_PER_RUN = 6        # ~20 s each; keeps one cron run short


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def chapter_label(stem: str) -> str:
    # Suwayomi names files "<scanlator>_<chapter>"; drop the scanlator prefix.
    return safe(stem.split("_", 1)[1] if "_" in stem else stem)


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

    done = failed = 0
    for cbz in sorted(SRC.glob("*/*/*.cbz")):
        if time.time() - cbz.stat().st_mtime < SETTLE_SECONDS:
            continue
        series = safe(cbz.parent.name)
        label = chapter_label(cbz.stem)
        out = DST / series / f"{series} - {label}.epub"
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
