# Kartonky × Reddit: offline vs online protest discourse (July 2026)

Computational Social Science course, NaUKMA, 2026.

We compare what people wrote on cardboard protest posters during the July 2026
protests (Kartonky archive) with how the same events were discussed on Reddit.

## Data sources

| Source | What | Period | Where |
|---|---|---|---|
| Kartonky archive | 4448 posters: text, city, dates, likes | 16.07–17.08.2026 | official export from the archive author (2026-09-21) |
| Reddit (Pushshift dumps, Academic Torrents) | submissions + comments from selected subreddits | July–August 2026 | `RS_/RC_2026-07/08.zst` |

Dataset link (Google Drive): _TBD_

Citation: «Картонки: народна галерея протестних плакатів» [онлайн-архів],
автор Артем Сах, 2026, https://kartonky.propellercrew.com

**Ethics.** Poster photos are not redistributed (archive author's condition:
publish analysis, not images). Data is not stored in this repo.

## Pipeline

1. **Kartonky collection (first pass)** — `scripts/kartonky_scraped/kartonky_probe.py`,
   `scripts/kartonky_scraped/kartonky_api_v4.py`: our own scraper over the site API
   (4436 posters, 2026-09-18) → `data/kartonky_scraped/2026-09-18/`;
   `kartonky_images.py` downloads photos locally (never published). Replaced by the author's fuller export,
   which adds text provenance, event dates and EXIF time.
2. **Kartonky cleaning (author export)** — `scripts/kartonky_author_export/clean_kartonky.py`
   reads `data/kartonky_author_export/2026-09-21/`
   → `data/clean/kartonky_clean.csv` + `cleaning_log.txt`
3. **Reddit subreddit scan** — `scripts/reddit/run_scan.sh` +
   `scan_subreddits.py`: streams the monthly dumps (no decompression to disk)
   and counts keyword hits per subreddit to choose 5–10 communities.
4. **Reddit extraction** — [SanGreel/reddit-dump-extractor](https://github.com/SanGreel/reddit-dump-extractor)
   (recommended in H2; cloned to `tools/`, zstandard/Python method) filters dumps by subreddit.
   Selected 8 subreddits (`data/reddit_scan/subreddits_selected.txt`), chosen by Fedorov mentions
   and volume from step 3: Ukrainian-language (reddit_ukr, RedditUATalks, Kyiv),
   English-language Ukraine (ukraine, UkrainianConflict), outside view (worldnews, europe,
   UkraineRussiaReport). Excluded: combat-video subs, news bots, Indian protest subs,
   religious subs ("Protestant" false positives).
5. **Exploration** — `notebooks/` (Task 2 metrics)

## Layout

```
scripts/kartonky_scraped/        our own scraper (site API) + photo downloader
scripts/kartonky_author_export/  cleaning of the author's official export
scripts/reddit/                  Reddit dump processing
notebooks/          exploration (Task 2)
reports/            figures / PDF for submissions
data/               (gitignored)
  kartonky_scraped/<date>/        our scrape
  kartonky_author_export/<date>/  author's export
  clean/                          analysis-ready CSVs
  reddit_scan/                    subreddit hit counts
images/             (gitignored) poster photos, local only
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
