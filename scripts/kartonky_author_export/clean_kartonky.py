"""
Clean the official Kartonky export (2026-09-21) for H2.

Input : data/kartonky_author_export/2026-09-21/kartonky-corpus-2026-09-21.csv  (author's export)
Output: data/clean/kartonky_clean.csv
        data/clean/cleaning_log.txt   (every change we made, for the report)

What it does
 1. Drops exact duplicates (same id or same image_url).
 2. Fixes two rows dated 2025 -> 2026 (archive + protests are 2026; year typo).
 3. Builds one best-guess date per poster:
      taken_at (EXIF)  >  created_at if created_at_kind == event_date  >  upload time
    and flags whether that date is reliable for day-level analysis.
 4. Converts dates to Kyiv time (UTC+3 in July) so "a day" means a local day.
 5. Normalizes free-text city names and marks Ukraine vs abroad.
 6. Normalizes text for counting slogans; flags possible re-uploads
    (same normalized text + same city + same day).
 7. Drops the three empty review columns (they're for the archive author).
"""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # scripts/<group>/this_file.py
SRC = ROOT / "data/kartonky_author_export/2026-09-21/kartonky-corpus-2026-09-21.csv"
OUT_DIR = ROOT / "data/clean"
KYIV_TZ = "Europe/Kyiv"

# Free-text city -> canonical name. Anything not listed is kept as typed (stripped).
CITY_MAP = {
    "київ": "Київ",
    "Харківо": "Харків",
    "Льв": "Львів",
    "Німечина": "Німеччина",
    "Німеччина, Кассель": "Кассель",
    "Mюнхен": "Мюнхен",  # Latin M
    "Осло, Норвегія": "Осло",
    "Відень, Австрія": "Відень",
    "Italia Napoli": "Неаполь",
    "Самбір, Львівська область, УКРАЇНА": "Самбір",
    "Харківська обл, селище Донець": "Донець (Харківська обл.)",
    "москва": "Москва",
    "Сполучені Штати Америки": "США",
    "Frankenberg": "Франкенберг",
    "Тернопіль / Київ": "Тернопіль",  # first city named
}

# Cities / countries outside Ukraine (after normalization).
ABROAD = {
    "Дюссельдорф", "Варшава", "Кассель", "Берлін", "Німеччина", "Мюнхен",
    "Торонто", "Осло", "Валенсія", "Сакраменто", "Атланта", "Англія", "Париж",
    "Москва", "США", "Гаага", "Франкенберг", "Норвегія", "Вільнюс", "Неаполь",
    "Бухарест", "Вестпорт", "Вроцлав", "Брюссель", "Відень", "Беллінцона",
    "Бразилія", "Прага",
}


def normalize_text(s: str) -> str:
    """Lowercase, unify apostrophes, drop punctuation/emoji, collapse spaces."""
    s = s.lower().replace("’", "'").replace("ʼ", "'")
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log = []
    df = pd.read_csv(SRC)
    log.append(f"loaded {len(df)} rows, {df.shape[1]} columns from {SRC.name}")

    # 1. exact duplicates
    n0 = len(df)
    df = df.drop_duplicates("id").drop_duplicates("image_url")
    log.append(f"exact duplicates removed (id / image_url): {n0 - len(df)}")

    # 2. year typo
    for col in ["created_at", "taken_at"]:
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    bad_year = df["created_at"].dt.year < 2026
    for _, r in df[bad_year].iterrows():
        log.append(f"year fixed 2025->2026: id={r.id} created_at={r.created_at} kind={r.created_at_kind}")
    df["date_fixed"] = bad_year
    df.loc[bad_year, "created_at"] = df.loc[bad_year, "created_at"] + pd.DateOffset(years=1)

    # 3. best date + source
    df["date_source"] = "upload_time"
    df.loc[df["created_at_kind"] == "event_date", "date_source"] = "event_date"
    df.loc[df["taken_at"].notna(), "date_source"] = "exif"
    best = df["created_at"].where(df["taken_at"].isna(), df["taken_at"])
    df["date_reliable"] = df["date_source"] != "upload_time"

    # 4. Kyiv local day. event_date rows are midnight UTC placeholders -> keep their date as is.
    local = best.dt.tz_convert(KYIV_TZ)
    df["date_local"] = local.dt.strftime("%Y-%m-%d")
    is_ev = df["date_source"] == "event_date"
    df.loc[is_ev, "date_local"] = best[is_ev].dt.strftime("%Y-%m-%d")
    df["datetime_local"] = local.dt.strftime("%Y-%m-%d %H:%M")
    df.loc[is_ev, "datetime_local"] = pd.NA
    early = df["date_local"] < "2026-07-15"
    log.append(f"rows dated before 15.07 (kept, check manually): {early.sum()} -> "
               + ", ".join(df.loc[early, "id"].str[:8]))

    # 5. city
    raw_city = df["city"].str.strip()
    df["city_norm"] = raw_city.replace(CITY_MAP)
    changed = (raw_city != df["city_norm"]) & raw_city.notna()
    log.append(f"city values normalized: {changed.sum()} rows "
               f"({raw_city.nunique()} -> {df['city_norm'].nunique()} unique)")
    df["location_group"] = "Україна"
    df.loc[df["city_norm"].isin(ABROAD), "location_group"] = "за кордоном"
    df.loc[df["city_norm"].isna(), "location_group"] = "невідомо"

    # 6. text
    df["text"] = df["text"].str.strip()
    df["has_text"] = df["text"].notna() & (df["text"] != "")
    df["text_norm"] = df["text"].fillna("").map(normalize_text)
    df["text_len_words"] = df["text_norm"].str.split().str.len().where(df["has_text"], 0)
    df["text_source"] = df["text_source"].fillna("unknown")
    df["slogan_count"] = df.groupby("text_norm")["id"].transform("count").where(df["has_text"], 0)
    key = ["text_norm", "city_norm", "date_local"]
    df["possible_reupload"] = df["has_text"] & df.duplicated(key, keep=False)
    log.append(f"repeated slogans: {(df['slogan_count'] > 1).sum()} rows share text with another poster")
    log.append(f"possible re-uploads (same text+city+day), flagged not dropped: {df['possible_reupload'].sum()}")

    # 7. output
    cols = ["id", "text", "text_norm", "has_text", "text_source", "text_len_words",
            "slogan_count", "city", "city_norm", "location_group",
            "date_local", "datetime_local", "date_source", "date_reliable", "date_fixed",
            "created_at", "created_at_kind", "taken_at", "likes", "possible_reupload",
            "image_url"]
    df = df[cols].sort_values(["date_local", "datetime_local"], na_position="last")
    out = OUT_DIR / "kartonky_clean.csv"
    df.to_csv(out, index=False)
    log.append(f"saved {len(df)} rows, {len(cols)} columns -> {out.relative_to(ROOT)} "
               f"({out.stat().st_size / 1e6:.2f} MB)")

    (OUT_DIR / "cleaning_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log))


if __name__ == "__main__":
    main()
