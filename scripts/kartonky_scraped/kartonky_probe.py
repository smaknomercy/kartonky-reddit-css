"""
Probe the Kartonky API to understand pagination and sorting.
Run: python kartonky_probe.py
"""

import time
import requests

API = "https://kartonky.propellercrew.com/api/photos"
s = requests.Session()
s.headers["User-Agent"] = "KartonkyStudentTest/0.2 (NaUKMA CSS course)"


def get(params):
    time.sleep(1)
    r = s.get(API, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def describe(label, data):
    photos = data.get("photos", [])
    if not photos:
        print(f"{label:28} empty, hasMore={data.get('hasMore')}")
        return []
    dates = [p["created_at"][:10] for p in photos]
    likes = [p["likes"] for p in photos]
    print(f"{label:28} n={len(photos):3}  dates {min(dates)}..{max(dates)}  "
          f"likes {min(likes)}..{max(likes)}  first={dates[0]}/{likes[0]}  "
          f"hasMore={data.get('hasMore')}")
    return [p["id"] for p in photos]


# 1. Is the order stable? Fetch page 0 twice.
a = describe("page=0 (1st)", get({"page": 0}))
b = describe("page=0 (2nd)", get({"page": 0}))
print(f"  same ids: {set(a) == set(b)}, same order: {a == b}\n")

# 2. How do consecutive pages overlap?
pages = {p: describe(f"page={p}", get({"page": p})) for p in range(1, 5)}
pages[0] = a
for p in range(0, 4):
    print(f"  overlap page {p} & {p+1}: {len(set(pages[p]) & set(pages[p+1]))}")
print()

# 3. Does the API accept sort / city params like the website does?
for params in ({"page": 0, "sort": "new"}, {"page": 0, "sort": "likes"},
               {"page": 0, "sort": "newest"}, {"page": 0, "city": "Київ"}):
    describe(str(params), get(params))
