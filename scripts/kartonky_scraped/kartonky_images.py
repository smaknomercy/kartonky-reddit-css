"""
Download Kartonky poster images listed in kartonky.csv.

- Saves to images/<id>.jpg
- Resumable: already downloaded files are skipped, so you can stop (Ctrl+C) and rerun
- Writes images_manifest.csv: id, status, bytes, path
- Polite: few parallel workers + small delay per request

Project layout (paths are resolved from the script location, so it works
from any working directory):
    kartonky/
      scripts/kartonky_scraped/kartonky_images.py  <- this file
      data/kartonky_scraped/2026-09-18/kartonky.csv    <- input (latest dated folder by default)
      data/kartonky_scraped/2026-09-18/images_manifest.csv  <- output log
      images/<id>.jpg                 <- output images

Usage:
    python scripts/kartonky_scraped/kartonky_images.py                        # latest snapshot in data/kartonky_scraped/
    python scripts/kartonky_scraped/kartonky_images.py --snapshot 2026-09-18  # specific snapshot
    python scripts/kartonky_scraped/kartonky_images.py --limit 50             # quick test
    python scripts/kartonky_scraped/kartonky_images.py --workers 2            # gentler
"""

import argparse
import csv
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

try:
    from tqdm import tqdm
except ImportError:  # fallback: plain progress lines
    tqdm = None

USER_AGENT = "KartonkyStudentTest/0.3 (NaUKMA CSS course, non-commercial)"

# Project root: this file lives in scripts/kartonky_scraped/
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "kartonky_scraped"
OUT_DIR = ROOT / "images"
DELAY_SEC = 0.3  # per request, per worker

_local = threading.local()


def session():
    # one requests.Session per thread
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


def download(pid, url, retries=3):
    path = OUT_DIR / f"{pid}.jpg"
    if path.exists() and path.stat().st_size > 0:
        return pid, "skipped", path.stat().st_size, str(path.relative_to(ROOT))

    tmp = path.with_suffix(".part")
    for attempt in range(retries):
        try:
            time.sleep(DELAY_SEC)
            r = session().get(url, timeout=60, stream=True)
            if r.status_code == 404:
                return pid, "not_found", 0, ""
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            if not r.headers.get("Content-Type", "").startswith("image/"):
                return pid, f"not_image:{r.headers.get('Content-Type')}", 0, ""
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    f.write(chunk)
            os.replace(tmp, path)  # atomic: no half-written .jpg files
            return pid, "ok", path.stat().st_size, str(path.relative_to(ROOT))
        except requests.RequestException:
            time.sleep(2 ** attempt * 2)
    if tmp.exists():
        tmp.unlink()
    return pid, "failed", 0, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=None,
                    help="dated folder inside data/kartonky_scraped/, e.g. 2026-09-18 (default: latest)")
    ap.add_argument("--csv", default=None, help="explicit path to a CSV (overrides --snapshot)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if args.csv:
        csv_path = Path(args.csv).resolve()
    else:
        snapshots = sorted(d for d in DATA_DIR.iterdir() if d.is_dir()) if DATA_DIR.exists() else []
        if args.snapshot:
            snap = DATA_DIR / args.snapshot
        elif snapshots:
            snap = snapshots[-1]  # names are dates, so the last one is the newest
        else:
            raise SystemExit(f"No snapshot folders found in {DATA_DIR}")
        csv_path = snap / "kartonky.csv"
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")
    manifest_path = csv_path.parent / "images_manifest.csv"

    print(f"Input:    {csv_path.relative_to(ROOT) if csv_path.is_relative_to(ROOT) else csv_path}")
    print(f"Images:   {OUT_DIR.relative_to(ROOT)}/")
    OUT_DIR.mkdir(exist_ok=True)
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = [(r["id"], r["url"]) for r in csv.DictReader(f)]
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(rows)} images to process")

    results, total_bytes, start = [], 0, time.time()
    counts = {"ok": 0, "skip": 0, "err": 0}
    bar = tqdm(total=len(rows), unit="img", dynamic_ncols=True) if tqdm else None
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(download, pid, url) for pid, url in rows]
            for i, fut in enumerate(as_completed(futures), 1):
                res = fut.result()
                results.append(res)
                total_bytes += res[2]
                status = res[1]
                counts["ok" if status == "ok" else "skip" if status == "skipped" else "err"] += 1
                if bar:
                    bar.set_postfix(MB=f"{total_bytes / 1e6:.0f}", **counts)
                    bar.update(1)
                elif i % 100 == 0 or i == len(rows):
                    print(f"  {i}/{len(rows)}  {total_bytes / 1e6:.1f} MB  {time.time() - start:.0f}s")
    except KeyboardInterrupt:
        print("\nInterrupted, saving manifest. Rerun to resume.")
    finally:
        if bar:
            bar.close()

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "status", "bytes", "path"])
        w.writerows(results)

    statuses = {}
    for _, st, _, _ in results:
        statuses[st] = statuses.get(st, 0) + 1
    print(f"\nDone: {statuses}")
    print(f"Manifest: {manifest_path.relative_to(ROOT)}")
    print(f"Total size: {total_bytes / 1e6:.1f} MB ({total_bytes / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
