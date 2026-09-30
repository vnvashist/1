#!/usr/bin/env python3
"""DC apartment hunter.

Pulls 1BR/1BA listings from Craigslist (Washington DC proper), throws out
basements, scams and listings in neighborhoods you don't want, scores the rest
on light, location, price and amenities, and writes a ranked report of what's
new since the last run. Listings you find elsewhere (Zillow, Apartments.com,
HotPads...) can be scored the same way with --import-csv.

Standard library only; Python 3.11+.

    python apartment_hunt.py                   # search, report new listings
    python apartment_hunt.py --all             # include previously seen ones
    python apartment_hunt.py --import-csv found.csv
    python apartment_hunt.py --email           # also email the report
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import os
import re
import smtplib
import sqlite3
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Neighborhood knowledge
#
# Tiers:
#   prime    - lively, walkable, lots of restaurants/bars, easy Metro
#   good     - nice and walkable but quieter or a bit farther out
#   caution  - mixed/transitional; check the specific block before touring
#   excluded - suburban-feeling/boring, or high reported violent crime
#
# These are judgment calls. Override any of them in config.toml
# ([location] tier_overrides) and check blocks on https://crimecards.dc.gov.
# Centroids are approximate (lat, lon).
# ---------------------------------------------------------------------------

NEIGHBORHOODS: dict[str, dict] = {
    # prime
    "Logan Circle": {"tier": "prime", "at": (38.9097, -77.0297), "aliases": ["logan circle", "logan", "14th st", "14th street"]},
    "Dupont Circle": {"tier": "prime", "at": (38.9096, -77.0434), "aliases": ["dupont"]},
    "U Street": {"tier": "prime", "at": (38.9170, -77.0290), "aliases": ["u street", "u st", "u st corridor"]},
    "Shaw": {"tier": "prime", "at": (38.9124, -77.0214), "aliases": ["shaw", "blagden alley", "howard university"]},
    "Adams Morgan": {"tier": "prime", "at": (38.9215, -77.0422), "aliases": ["adams morgan", "adams-morgan", "admo", "18th st"]},
    "Columbia Heights": {"tier": "prime", "at": (38.9282, -77.0325), "aliases": ["columbia heights", "columbia hts", "colheights"]},
    "Navy Yard": {"tier": "prime", "at": (38.8765, -77.0035), "aliases": ["navy yard", "capitol riverfront", "nationals park", "yards park"]},
    "Capitol Hill": {"tier": "prime", "at": (38.8867, -76.9960), "aliases": ["capitol hill", "cap hill", "eastern market", "barracks row", "lincoln park"]},
    "H Street / Atlas": {"tier": "prime", "at": (38.9001, -76.9884), "aliases": ["h street", "h st ne", "h st corridor", "atlas district", "near northeast"]},
    "NoMa": {"tier": "prime", "at": (38.9070, -77.0030), "aliases": ["noma", "no ma", "north of massachusetts"]},
    "Union Market": {"tier": "prime", "at": (38.9085, -76.9970), "aliases": ["union market"]},
    "Mount Vernon Triangle": {"tier": "prime", "at": (38.9030, -77.0180), "aliases": ["mount vernon triangle", "mt vernon triangle", "mvt", "mount vernon square", "mt vernon sq"]},
    "Penn Quarter / Chinatown": {"tier": "prime", "at": (38.8990, -77.0220), "aliases": ["penn quarter", "chinatown", "gallery place", "capital one arena"]},
    "The Wharf / SW Waterfront": {"tier": "prime", "at": (38.8790, -77.0240), "aliases": ["the wharf", "wharf", "sw waterfront", "southwest waterfront", "waterfront"]},
    "Georgetown": {"tier": "prime", "at": (38.9076, -77.0654), "aliases": ["georgetown"]},
    "West End": {"tier": "prime", "at": (38.9040, -77.0500), "aliases": ["west end"]},
    "Bloomingdale": {"tier": "prime", "at": (38.9160, -77.0120), "aliases": ["bloomingdale"]},
    "Kalorama": {"tier": "prime", "at": (38.9180, -77.0470), "aliases": ["kalorama", "sheridan-kalorama"]},
    # good
    "Downtown": {"tier": "good", "at": (38.9020, -77.0320), "aliases": ["downtown", "mcpherson", "farragut", "franklin square"]},
    "Petworth": {"tier": "good", "at": (38.9420, -77.0240), "aliases": ["petworth"]},
    "Park View": {"tier": "good", "at": (38.9335, -77.0215), "aliases": ["park view", "parkview"]},
    "Mount Pleasant": {"tier": "good", "at": (38.9300, -77.0390), "aliases": ["mount pleasant", "mt pleasant"]},
    "Eckington": {"tier": "good", "at": (38.9150, -77.0020), "aliases": ["eckington"]},
    "Truxton Circle": {"tier": "good", "at": (38.9110, -77.0120), "aliases": ["truxton circle", "truxton"]},
    "Foggy Bottom": {"tier": "good", "at": (38.8990, -77.0500), "aliases": ["foggy bottom", "gwu", "george washington university"]},
    "Woodley Park": {"tier": "good", "at": (38.9250, -77.0520), "aliases": ["woodley park", "woodley"]},
    "Cleveland Park": {"tier": "good", "at": (38.9340, -77.0580), "aliases": ["cleveland park"]},
    "Glover Park": {"tier": "good", "at": (38.9230, -77.0750), "aliases": ["glover park"]},
    "Brookland": {"tier": "good", "at": (38.9330, -76.9940), "aliases": ["brookland", "catholic university", "cua"]},
    "Southwest": {"tier": "good", "at": (38.8770, -77.0150), "aliases": ["southwest dc", "sw dc", "l'enfant", "lenfant"]},
    # caution
    "Trinidad": {"tier": "caution", "at": (38.9050, -76.9830), "aliases": ["trinidad"]},
    "Ivy City": {"tier": "caution", "at": (38.9150, -76.9850), "aliases": ["ivy city"]},
    "Kingman Park": {"tier": "caution", "at": (38.8960, -76.9770), "aliases": ["kingman park"]},
    "Hill East": {"tier": "caution", "at": (38.8840, -76.9800), "aliases": ["hill east", "stadium armory"]},
    "Edgewood": {"tier": "caution", "at": (38.9230, -76.9990), "aliases": ["edgewood", "rhode island ave"]},
    # excluded: quiet / suburban-feeling
    "Chevy Chase DC": {"tier": "excluded", "at": (38.9640, -77.0750), "aliases": ["chevy chase"]},
    "Friendship Heights": {"tier": "excluded", "at": (38.9600, -77.0850), "aliases": ["friendship heights"]},
    "Tenleytown / AU Park": {"tier": "excluded", "at": (38.9480, -77.0850), "aliases": ["tenleytown", "tenley", "au park", "american university park"]},
    "Spring Valley / Palisades": {"tier": "excluded", "at": (38.9300, -77.1000), "aliases": ["spring valley", "palisades", "foxhall"]},
    "Takoma / Shepherd Park": {"tier": "excluded", "at": (38.9800, -77.0230), "aliases": ["takoma", "shepherd park", "brightwood", "fort totten"]},
    # excluded: high reported violent crime (MPD data)
    "Anacostia": {"tier": "excluded", "at": (38.8620, -76.9860), "aliases": ["anacostia"]},
    "Congress Heights": {"tier": "excluded", "at": (38.8440, -76.9990), "aliases": ["congress heights"]},
    "Washington Highlands": {"tier": "excluded", "at": (38.8310, -77.0000), "aliases": ["washington highlands", "bellevue"]},
    "Benning / Deanwood": {"tier": "excluded", "at": (38.8980, -76.9360), "aliases": ["benning", "deanwood", "marshall heights", "minnesota ave"]},
}

METRO_STATIONS: dict[str, tuple[float, float]] = {
    "Dupont Circle": (38.9096, -77.0434), "U St": (38.9170, -77.0280),
    "Shaw-Howard U": (38.9126, -77.0219), "Columbia Heights": (38.9285, -77.0325),
    "Georgia Ave-Petworth": (38.9370, -77.0235), "Woodley Park": (38.9249, -77.0524),
    "Cleveland Park": (38.9347, -77.0580), "Farragut North": (38.9032, -77.0397),
    "Farragut West": (38.9014, -77.0420), "McPherson Sq": (38.9014, -77.0336),
    "Metro Center": (38.8983, -77.0281), "Gallery Place": (38.8983, -77.0219),
    "Mt Vernon Sq": (38.9055, -77.0220), "Judiciary Sq": (38.8960, -77.0166),
    "Union Station": (38.8977, -77.0074), "NoMa-Gallaudet": (38.9070, -77.0030),
    "Rhode Island Ave": (38.9210, -76.9959), "Brookland-CUA": (38.9332, -76.9945),
    "Eastern Market": (38.8847, -76.9960), "Capitol South": (38.8850, -77.0050),
    "Navy Yard": (38.8765, -77.0050), "Waterfront": (38.8765, -77.0175),
    "L'Enfant Plaza": (38.8848, -77.0219), "Foggy Bottom": (38.9008, -77.0503),
    "Potomac Ave": (38.8812, -76.9854), "Tenleytown": (38.9479, -77.0795),
}

TIER_POINTS = {"prime": 20, "good": 10, "caution": -15, "unknown": -5}

# DC listings love euphemisms for basements.
BASEMENT_PATTERNS = [
    r"basement", r"garden[- ]level", r"garden (unit|apartment|apt)", r"lower[- ]level",
    r"below[- ]grade", r"terrace[- ]level", r"sub[- ]?level", r"lower unit",
    r"english bsmt", r"\bbsmt\b", r"half[- ]below", r"partially below",
]
LIGHT_PATTERNS = [
    r"natural light", r"sun[- ]?(filled|drenched|lit)", r"\bsunny\b", r"sunlight", r"light[- ]filled",
    r"\bbright\b", r"south[- ]facing", r"southern exposure", r"floor[- ]to[- ]ceiling", r"large windows",
    r"oversized windows", r"huge windows", r"skylights?", r"bay windows?", r"top floor", r"penthouse",
    r"corner unit", r"tons of light", r"lots of light", r"abundant light", r"high ceilings",
]
NOT_WHAT_YOU_WANT = {
    r"\broom for rent\b|\broommate\b|\bshared (apartment|house|bath)|\bprivate room\b": "shared housing / room",
    r"\bstudio\b(?!.*\b1\s?(br|bd|bed))": "studio, not a 1BR",
    r"\b(2|two)\s?(br|bd|bed(room)?s?)\b": "more than 1 bedroom",
}
SCAM_PATTERNS = [
    r"western union", r"moneygram", r"wire (the )?(money|deposit|funds)", r"out of (the )?(country|town) (for|on)",
    r"(mail|ship|send) (you )?the keys", r"deposit before (viewing|showing|seeing)", r"can'?t show (the )?(unit|apartment)",
    r"gift ?cards?", r"missionary", r"i am currently (abroad|overseas)",
]
AMENITIES = {
    "in_unit_laundry": r"w/?d in[- ]unit|washer/?(and )?dryer in[- ](the )?unit|in[- ]unit (washer|laundry|w/?d)",
    "dishwasher": r"dishwasher",
    "central_air": r"central (air|a/?c|ac)\b|central air conditioning",
    "pets_allowed": r"(cats?|dogs?) (are )?ok|pet[- ]friendly|pets? (ok|allowed|welcome)",
    "outdoor_space": r"balcony|private patio|roof ?(top)? ?deck|rooftop",
    "gym": r"fitness (center|room)|\bgym\b",
    "doorman": r"concierge|doorman|front desk",
    "parking": r"(off[- ]street|garage|reserved) parking|parking (included|available|space)",
    "hardwood": r"hardwood",
    "dog_park_nearby": r"dog park",
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    id: str
    url: str
    title: str
    price: int | None
    location: str = ""
    source: str = "craigslist"
    description: str = ""
    lat: float | None = None
    lon: float | None = None
    # Filled in by evaluate()
    neighborhood: str = "Unknown"
    tier: str = "unknown"
    nearest_metro: str = ""
    metro_km: float | None = None
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    amenities: list[str] = field(default_factory=list)
    rejected: str | None = None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.location}\n{self.description}".lower()


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def parse_price(raw: str | None) -> int | None:
    if not raw:
        return None
    m = re.search(r"\$?\s*([\d,]{3,})", str(raw))
    return int(m.group(1).replace(",", "")) if m else None


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", html.unescape(text)).strip()


# ---------------------------------------------------------------------------
# Craigslist
# ---------------------------------------------------------------------------

def fetch(url: str, retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except OSError as exc:
            if attempt == retries - 1:
                raise
            print(f"  fetch failed ({exc}); retrying...", file=sys.stderr)
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError("unreachable")


def craigslist_search_url(cfg: dict) -> str:
    s = cfg["search"]
    params = {
        "min_bedrooms": s["bedrooms"], "max_bedrooms": s["bedrooms"],
        "min_bathrooms": s["bathrooms"], "max_bathrooms": s["bathrooms"],
        "max_price": s["max_rent"] + s.get("stretch", 0),
        "min_price": s.get("min_rent", 0),
        "sort": "date",
        **s.get("extra_params", {}),
    }
    subarea = f"/{s['subarea']}" if s.get("subarea") else ""
    return f"https://{s['site']}.craigslist.org/search{subarea}/apa?{urllib.parse.urlencode(params)}"


def parse_search_results(page: str) -> list[Listing]:
    """Parse the no-JavaScript result list Craigslist serves to plain HTTP clients."""
    listings = []
    for block in re.findall(r'<li class="cl-static-search-result"[^>]*>(.*?)</li>', page, re.S):
        href = re.search(r'href="([^"]+)"', block)
        if not href:
            continue
        url = html.unescape(href.group(1))
        pid = re.search(r"/(\d+)\.html", url)

        def div(cls: str) -> str:
            m = re.search(rf'<div class="{cls}">(.*?)</div>', block, re.S)
            return strip_tags(m.group(1)) if m else ""

        listings.append(Listing(
            id=f"cl-{pid.group(1) if pid else url}",
            url=url,
            title=div("title"),
            price=parse_price(div("price")),
            location=div("location"),
        ))
    return listings


def enrich_from_detail(listing: Listing, page: str) -> None:
    body = re.search(r'<section id="postingbody">(.*?)</section>', page, re.S)
    if body:
        listing.description = strip_tags(body.group(1)).replace("QR Code Link to This Post", "").strip()
    # Attribute blocks ("w/d in unit", "cats are OK", "apartment") sit between the map and the body.
    attrs = re.search(r'class="mapAndAttrs"(.*?)id="postingbody"', page, re.S)
    if attrs:
        listing.description += "\n" + strip_tags(attrs.group(1))
    lat = re.search(r'data-latitude="(-?[\d.]+)"', page)
    lon = re.search(r'data-longitude="(-?[\d.]+)"', page)
    if lat and lon:
        listing.lat, listing.lon = float(lat.group(1)), float(lon.group(1))


def search_craigslist(cfg: dict, seen: set[str]) -> list[Listing]:
    url = craigslist_search_url(cfg)
    print(f"Searching {url}")
    listings = parse_search_results(fetch(url))
    print(f"  {len(listings)} results")
    s = cfg["search"]
    if s.get("fetch_details", True):
        todo = [l for l in listings if l.id not in seen][: s.get("max_detail_fetches", 40)]
        for i, listing in enumerate(todo, 1):
            print(f"  details {i}/{len(todo)}: {listing.title[:60]}")
            try:
                enrich_from_detail(listing, fetch(listing.url))
            except OSError as exc:
                listing.flags.append(f"couldn't load details ({exc})")
            time.sleep(s.get("request_delay_seconds", 2.0))
    return listings


def load_csv(path: str) -> list[Listing]:
    """Columns: url,title,price,location,description,lat,lon (only url/title/price required)."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            lat, lon = row.get("lat") or None, row.get("lon") or None
            out.append(Listing(
                id=f"csv-{row['url']}",
                url=row["url"],
                title=row.get("title", ""),
                price=parse_price(row.get("price")),
                location=row.get("location", ""),
                source="manual",
                description=row.get("description", ""),
                lat=float(lat) if lat else None,
                lon=float(lon) if lon else None,
            ))
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def any_match(patterns, text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return None


def resolve_neighborhood(listing: Listing, cfg: dict) -> tuple[str, str]:
    overrides = cfg.get("location", {}).get("tier_overrides", {})

    def tier_of(name: str) -> str:
        return overrides.get(name, NEIGHBORHOODS[name]["tier"])

    # Coordinates beat free text: posters routinely claim the trendier neighborhood next door.
    if listing.lat is not None and listing.lon is not None:
        max_km = cfg.get("location", {}).get("neighborhood_match_km", 1.0)
        name, dist = min(
            ((n, haversine_km((listing.lat, listing.lon), d["at"])) for n, d in NEIGHBORHOODS.items()),
            key=lambda x: x[1],
        )
        if dist <= max_km:
            return name, tier_of(name)

    # Otherwise the location field, then the title, then the body.
    for text in (listing.location, listing.title, listing.description):
        text = text.lower()
        hits = []
        for name, d in NEIGHBORHOODS.items():
            for alias in d["aliases"]:
                m = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text)
                if m:
                    hits.append((m.start(), name))
        if hits:
            name = min(hits)[1]
            return name, tier_of(name)
    return "Unknown", "unknown"


def evaluate(listing: Listing, cfg: dict) -> Listing:
    s, loc, prefs = cfg["search"], cfg.get("location", {}), cfg.get("preferences", {})
    text = listing.text
    score = 50

    # --- hard filters -----------------------------------------------------
    if (m := any_match(BASEMENT_PATTERNS, text)):
        listing.rejected = f"basement / below grade ('{m}')"
    elif (m := any_match(SCAM_PATTERNS, text)):
        listing.rejected = f"scam language ('{m}')"
    else:
        for pattern, why in NOT_WHAT_YOU_WANT.items():
            if re.search(pattern, listing.title.lower()):
                listing.rejected = why
                break

    budget, stretch = s["max_rent"], s.get("stretch", 0)
    if listing.price is None:
        listing.flags.append("no price listed")
    elif listing.price > budget + stretch:
        listing.rejected = listing.rejected or f"${listing.price} is over budget"
    elif listing.price > budget:
        score -= 10
        listing.flags.append(f"${listing.price - budget} over budget (within stretch)")
    elif listing.price < s.get("min_rent", 0):
        score -= 30
        listing.flags.append(f"${listing.price} is suspiciously cheap for a DC 1BR - likely scam")
    else:
        savings = min(10, round((budget - listing.price) / budget * 50))
        if savings:
            score += savings
            listing.reasons.append(f"${budget - listing.price} under budget (+{savings})")

    listing.neighborhood, listing.tier = resolve_neighborhood(listing, cfg)
    if listing.tier == "excluded":
        listing.rejected = listing.rejected or f"neighborhood excluded ({listing.neighborhood})"
    elif listing.tier == "unknown":
        if not loc.get("allow_unknown", True):
            listing.rejected = listing.rejected or "neighborhood unknown"
        listing.flags.append("couldn't pin down the neighborhood - check the map")
    elif listing.tier == "caution":
        listing.flags.append(f"{listing.neighborhood}: mixed area, check the block on crimecards.dc.gov")
    pts = TIER_POINTS.get(listing.tier, 0)
    score += pts
    if pts > 0:
        listing.reasons.append(f"{listing.neighborhood} ({listing.tier}, +{pts})")

    # --- light --------------------------------------------------------------
    light_hits = sorted({m.group(0) for p in LIGHT_PATTERNS if (m := re.search(p, text))})
    if light_hits:
        pts = min(16, 4 * len(light_hits))
        score += pts
        listing.reasons.append(f"light: {', '.join(light_hits)} (+{pts})")
    else:
        listing.flags.append("no mention of light/windows - ask or check photos")
    floor = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]floor\b", text)
    if floor and int(floor.group(1)) >= 3:
        score += 4
        listing.reasons.append(f"{floor.group(0)} (+4)")
    elif re.search(r"ground[- ]floor|first[- ]floor|1st[- ]floor|street[- ]level", text):
        score -= 5
        listing.flags.append("ground floor - less light and privacy")

    # --- amenities ------------------------------------------------------------
    weights = prefs.get("weights", {})
    for name, pattern in AMENITIES.items():
        if re.search(pattern, text):
            listing.amenities.append(name)
            score += weights.get(name, 0)
    if listing.amenities:
        listing.reasons.append("has: " + ", ".join(a.replace("_", " ") for a in listing.amenities))
    for must in prefs.get("must_have", []):
        if must not in listing.amenities:
            score -= 10
            listing.flags.append(f"doesn't mention {must.replace('_', ' ')} (must-have)")

    # --- transit / commute ------------------------------------------------------
    if listing.lat is not None and listing.lon is not None:
        here = (listing.lat, listing.lon)
        listing.nearest_metro, listing.metro_km = min(
            ((n, haversine_km(here, at)) for n, at in METRO_STATIONS.items()), key=lambda x: x[1]
        )
        walk_min = round(listing.metro_km / 0.08)  # ~4.8 km/h
        if listing.metro_km <= 0.8:
            score += 8
            listing.reasons.append(f"{walk_min} min walk to {listing.nearest_metro} Metro (+8)")
        elif listing.metro_km <= 1.6:
            score += 4
            listing.reasons.append(f"{walk_min} min walk to {listing.nearest_metro} Metro (+4)")
        elif listing.metro_km > 2.4:
            score -= 5
            listing.flags.append(f"far from Metro ({walk_min} min walk)")

        work = loc.get("work")
        if work:
            km = haversine_km(here, (work["lat"], work["lon"]))
            if km <= work.get("max_km", 5):
                score += 5
                listing.reasons.append(f"{km:.1f} km from work (+5)")
            else:
                score -= 5
                listing.flags.append(f"{km:.1f} km from work")

    listing.score = max(0, min(100, score))
    return listing


# ---------------------------------------------------------------------------
# State, reporting, email
# ---------------------------------------------------------------------------

def open_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS seen (
        id TEXT PRIMARY KEY, url TEXT, title TEXT, price INTEGER, neighborhood TEXT,
        score INTEGER, rejected TEXT, first_seen TEXT)""")
    return db


def write_reports(listings: list[Listing], cfg: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    min_score = cfg.get("output", {}).get("min_score", 55)
    keep = sorted((l for l in listings if not l.rejected and l.score >= min_score), key=lambda l: -l.score)
    below = [l for l in listings if not l.rejected and l.score < min_score]
    rejected = [l for l in listings if l.rejected]

    csv_path = out_dir / f"apartments_{stamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["score", "price", "neighborhood", "tier", "title", "url", "nearest_metro",
                    "metro_km", "amenities", "reasons", "flags", "rejected"])
        for l in sorted(listings, key=lambda l: (l.rejected is not None, -l.score)):
            w.writerow([l.score, l.price, l.neighborhood, l.tier, l.title, l.url, l.nearest_metro,
                        f"{l.metro_km:.2f}" if l.metro_km is not None else "", ";".join(l.amenities),
                        " | ".join(l.reasons), " | ".join(l.flags), l.rejected or ""])

    lines = [f"# DC apartment hunt - {datetime.now():%a %b %d, %Y %I:%M %p}", "",
             f"{len(keep)} worth a look, {len(below)} below score {min_score}, {len(rejected)} filtered out.", ""]
    for l in keep:
        price = f"${l.price:,}" if l.price else "$?"
        lines += [f"## {l.score}/100 - {price} - {l.neighborhood}", f"[{l.title}]({l.url})", ""]
        lines += [f"- ✅ {r}" for r in l.reasons] + [f"- ⚠️ {f}" for f in l.flags] + [""]
    if rejected:
        lines += ["<details><summary>Filtered out</summary>", ""]
        lines += [f"- {l.rejected}: [{l.title}]({l.url})" for l in rejected]
        lines += ["", "</details>", ""]
    md_path = out_dir / f"apartments_{stamp}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path


def send_email(subject: str, body: str, to: str) -> None:
    host, user, pw = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not (host and user and pw and to):
        print("Email skipped: set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and [email] to.", file=sys.stderr)
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as smtp:
        smtp.starttls()
        smtp.login(user, pw)
        smtp.send_message(msg)
    print(f"Emailed report to {to}")


def load_config(path: str) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--all", action="store_true", help="report previously seen listings too")
    ap.add_argument("--import-csv", metavar="FILE", help="score listings from a CSV instead of searching")
    ap.add_argument("--no-details", action="store_true", help="skip fetching each listing page (faster, less accurate)")
    ap.add_argument("--email", action="store_true", help="email the report (see README for SMTP setup)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.no_details:
        cfg["search"]["fetch_details"] = False
    out_dir = Path(cfg.get("output", {}).get("dir", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    db = open_db(out_dir / "seen.sqlite")
    seen = {row[0] for row in db.execute("SELECT id FROM seen")}

    listings = load_csv(args.import_csv) if args.import_csv else search_craigslist(cfg, seen)
    if not args.all:
        listings = [l for l in listings if l.id not in seen]
    for l in listings:
        evaluate(l, cfg)

    now = datetime.now().isoformat(timespec="seconds")
    db.executemany(
        "INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?,?,?,?)",
        [(l.id, l.url, l.title, l.price, l.neighborhood, l.score, l.rejected, now) for l in listings],
    )
    db.commit()

    if not listings:
        print("Nothing new since last run.")
        return 0
    md_path, csv_path = write_reports(listings, cfg, out_dir)
    good = sorted((l for l in listings if not l.rejected), key=lambda l: -l.score)
    print(f"\n{len(good)} candidates ({len(listings) - len(good)} filtered). Top picks:")
    for l in good[:10]:
        print(f"  {l.score:>3}  ${l.price or 0:>5,}  {l.neighborhood:<26} {l.title[:50]}\n       {l.url}")
    print(f"\nReport: {md_path}\nSpreadsheet: {csv_path}")

    min_score = cfg.get("output", {}).get("min_score", 55)
    if args.email and any(l.score >= min_score for l in good):
        send_email(f"{len(good)} new DC apartments", md_path.read_text(encoding="utf-8"),
                   cfg.get("email", {}).get("to", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
