"""
Kartonky API scraper (test version).

Pulls all posters from https://kartonky.propellercrew.com/api/photos?page=N
in two orders (default = newest first, sort=likes) until hasMore == false,
then merges them. NOTE: ?city= returns HTTP 500, do not use it. Saves:
  - kartonky_raw.jsonl   raw API records, one per line (for reproducibility)
  - kartonky.csv         cleaned table for pandas

Usage:
    pip install requests
    python kartonky_api.py                 # full run
    python kartonky_api.py --max-pages 3   # quick test
"""

import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import re
from urllib.parse import unquote

import requests

API = "https://kartonky.propellercrew.com/api/photos"
USER_AGENT = "KartonkyStudentTest/0.2 (NaUKMA CSS course, non-commercial)"
DELAY_SEC = 1.0
KYIV = ZoneInfo("Europe/Kyiv")


def get_page(session, page, sort=None, retries=3):
    params = {"page": page}
    if sort:
        params["sort"] = sort
    for attempt in range(retries):
        r = session.get(API, params=params, timeout=30)
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 2 ** attempt * 2
            print(f"  ! page {page}: HTTP {r.status_code}, retry in {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"page {page} failed after {retries} retries")


def crawl(session, sort, start_page, max_pages):
    """Crawl one sort order. Returns {id: record}."""
    records = {}
    page, empty_streak = start_page, 0
    while max_pages is None or page < start_page + max_pages:
        try:
            data = get_page(session, page, sort)
        except (RuntimeError, requests.RequestException) as e:
            print(f"  ! [{sort or 'default'}] stopped at page {page}: {e}")
            print(f"  ! keeping {len(records)} records collected so far (INCOMPLETE)")
            break
        photos = data.get("photos", [])
        new = 0
        for p in photos:
            if p["id"] not in records:
                p["_page"] = page
                records[p["id"]] = p
                new += 1
        print(f"[{sort or 'default'}] page {page}: {len(photos)} photos, {new} new, total {len(records)}")

        if not data.get("hasMore") or not photos:
            break
        # page 0 and 1 are known to overlap, so tolerate a few pages without new ids
        empty_streak = empty_streak + 1 if new == 0 else 0
        if empty_streak >= 3:
            print("  ! 3 pages in a row without new ids, stopping")
            break
        page += 1
        time.sleep(DELAY_SEC)
    return records


HOME = "https://kartonky.propellercrew.com/"
IMG_ID_RE = re.compile(r"img\.kartonky\.propellercrew\.com/kartonky/([^/?#\"'&\s]+)\.(?:jpe?g|png|webp)", re.I)
MONTHS = {"січ": 1, "лют": 2, "бер": 3, "кві": 4, "тра": 5, "чер": 6,
          "лип": 7, "сер": 8, "вер": 9, "жов": 10, "лис": 11, "гру": 12}
DATE_RE = re.compile(r"^(\d{1,2})\s+([а-яіїєґ]{3})", re.I)


def parse_homepage(html):
    """Parse poster cards from the homepage HTML.
    Card structure: <figure> -> <img alt=caption style="aspect-ratio:W / H">,
    like button with count, <figcaption> -> <p>caption</p><p><span>City ·</span> 17 сер</p>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    records = {}
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        m = IMG_ID_RE.search(unquote(str(img))) if img else None
        if not m:
            continue
        pid = m.group(1)
        alt = (img.get("alt") or "").strip()

        # size from inline style "aspect-ratio:1080 / 1080"
        w = h = None
        ar = re.search(r"aspect-ratio:\s*(\d+)\s*/\s*(\d+)", img.get("style") or "")
        if ar:
            w, h = int(ar.group(1)), int(ar.group(2))

        # likes: number inside the like button (absent when 0)
        likes = 0
        like_btn = fig.find("button", attrs={"aria-label": "Подобається"})
        if like_btn:
            digits = re.sub(r"\D", "", like_btn.get_text())
            likes = int(digits) if digits else 0

        # meta line: optional <span>City ·</span> followed by the date
        city, date_iso = "", None
        figcap = fig.find("figcaption")
        meta = figcap.find_all("p")[-1] if figcap and figcap.find_all("p") else None
        if meta is not None:
            span = meta.find("span")
            if span is not None:
                city = span.get_text(" ", strip=True).replace("·", "").strip()
                span.extract()
            d = DATE_RE.match(meta.get_text(" ", strip=True).replace("·", "").strip())
            if d and MONTHS.get(d.group(2).lower()):
                date_iso = f"2026-{MONTHS[d.group(2).lower()]:02d}-{int(d.group(1)):02d}"

        records[pid] = {
            "id": pid,
            "url": f"https://img.kartonky.propellercrew.com/kartonky/{pid}.jpg",
            "caption": "" if alt == "Протестна картонка" else alt,
            "city": city,
            "created_at": f"{date_iso}T12:00:00.000Z" if date_iso else None,
            "likes": likes, "w": w, "h": h, "reports": None, "hidden": None,
            "_page": "home", "_date_only": True,
        }
    return records


def crawl_homepage(session):
    """The API's page 0 skips the newest ~35 posters, but the homepage HTML shows them."""
    records = parse_homepage(session.get(HOME, timeout=30).text)
    print(f"[homepage] {len(records)} posters, with city: {sum(bool(r['city']) for r in records.values())}")
    return records


def save_jsonl(records, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  -> checkpoint saved: {path}")


def clean(p):
    created = datetime.fromisoformat((p["created_at"] or "1970-01-01T00:00:00.000Z").replace("Z", "+00:00"))
    local = created.astimezone(KYIV)
    date_only = p.get("_date_only", False)
    caption = (p.get("caption") or "").strip()
    w, h = p.get("w") or 0, p.get("h") or 0
    return {
        "id": p["id"],
        "caption": caption,
        "has_caption": bool(caption),
        "caption_len": len(caption),
        "city": (p.get("city") or "").strip() or None,
        "created_at_utc": p["created_at"],
        "date_kyiv": (p["created_at"] or "")[:10] if date_only else local.date().isoformat(),
        "hour_kyiv": None if date_only else local.hour,
        # midnight UTC exactly -> probably a manually set date, not a real upload time
        "time_is_placeholder": date_only or created.strftime("%H:%M:%S.%f") == "00:00:00.000000",
        "likes": p.get("likes", 0),
        "reports": p.get("reports", 0),
        "hidden": p.get("hidden", False),
        "w": w,
        "h": h,
        "orientation": None if not (w and h) else "portrait" if h > w else "landscape" if w > h else "square",
        "url": p["url"],
        "api_page": p["_page"],
        "found_in": p["_found_in"],
        "first_pass": p.get("_pass"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-page", type=int, default=0,
                    help="page 0 overlaps page 1 by 35 items; duplicates are dropped")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--passes", type=int, default=1, help="how many times to crawl the date order")
    ap.add_argument("--skip-likes", action="store_true", help="crawl only the default (by date) order")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    # Default order is by date (newest first); "likes" is a second independent order.
    # Crawling both and taking the union is a completeness check.
    # The date order is not fully stable, so crawl it several times and merge.
    # New ids per pass show whether the union has converged.
    by_date = {}
    for n in range(1, args.passes + 1):
        run = crawl(session, None, args.start_page, args.max_pages)
        added = 0
        for pid, p in run.items():
            if pid not in by_date:
                p["_pass"] = n
                by_date[pid] = p
                added += 1
        print(f"== pass {n}: {len(run)} in this pass, +{added} new, union {len(by_date)}\n")
    save_jsonl(by_date.values(), "kartonky_raw_by_date.jsonl")

    by_likes = {}
    if not args.skip_likes:
        by_likes = crawl(session, "likes", args.start_page, args.max_pages)
        save_jsonl(by_likes.values(), "kartonky_raw_by_likes.jsonl")

    home = crawl_homepage(session)

    merged = {}
    for pid in by_date.keys() | by_likes.keys() | home.keys():
        p = dict(by_date.get(pid) or by_likes.get(pid) or home[pid])
        if pid in by_date and pid in by_likes:
            p["_found_in"] = "both"
        elif pid in by_date:
            p["_found_in"] = "date_only"
        elif pid in by_likes:
            p["_found_in"] = "likes_only"
        else:
            p["_found_in"] = "homepage_only"
        merged[pid] = p
    records = sorted(merged.values(), key=lambda p: p["created_at"] or "", reverse=True)

    print(f"\nby date: {len(by_date)} | by likes: {len(by_likes)} | union: {len(records)}")
    print("Found in:", Counter(p["_found_in"] for p in records))

    save_jsonl(records, "kartonky_raw.jsonl")

    rows = [clean(r) for r in records]
    with open("kartonky.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)

    # Quick summary
    print(f"\nSaved {len(rows)} posters -> kartonky.csv, kartonky_raw.jsonl")
    if not rows:
        return
    dates = sorted(r["date_kyiv"] for r in rows)
    print(f"Date range (Kyiv): {dates[0]} .. {dates[-1]}")
    print(f"With caption: {sum(r['has_caption'] for r in rows)} | "
          f"with city: {sum(bool(r['city']) for r in rows)} | "
          f"hidden: {sum(bool(r['hidden']) for r in rows)} | "
          f"placeholder time: {sum(r['time_is_placeholder'] for r in rows)}")
    print("Top cities:", Counter(r["city"] for r in rows if r["city"]).most_common(10))
    print("Posters per day:", sorted(Counter(dates).items()))


if __name__ == "__main__":
    main()
