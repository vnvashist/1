#!/usr/bin/env python3
"""DC apartment hunter.

Collects 1BR/1BA rentals in Washington DC from several sources, throws out
basements, scams, non-DC listings and neighborhoods you don't want, scores the
rest on light, privacy, location, size, move-in timing, price and amenities,
and writes a ranked report of what's new since the last run.

Sources (turn on/off in config.toml):
  craigslist    - searched directly
  email_alerts  - reads Zillow / Apartments.com / HotPads / Redfin / Trulia
                  saved-search alert emails from your inbox over IMAP
  rentcast      - RentCast listings API (aggregates MLS + rental sites; API key)
  --import-csv  - anything else you paste in by hand

Standard library only; Python 3.11+.

    python apartment_hunt.py                   # search, report new listings
    python apartment_hunt.py --all             # include previously seen ones
    python apartment_hunt.py --email           # also email the report
"""

from __future__ import annotations

import argparse
import csv
import email
import email.policy
import html
import imaplib
import json
import math
import os
import re
import smtplib
import sqlite3
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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

# Rough outline of DC (lat, lon), with the Potomac as the western edge so that
# Rosslyn/Arlington pins land outside even though they're a few hundred meters away.
DC_BOUNDARY = [
    (38.9955, -77.0410), (38.8930, -76.9094), (38.7916, -77.0390), (38.8500, -77.0300),
    (38.8700, -77.0420), (38.8850, -77.0530), (38.8960, -77.0600), (38.9030, -77.0700),
    (38.9200, -77.0980), (38.9342, -77.1197),
]
NOT_DC_TEXT = (
    r"\b(arlington|alexandria|rosslyn|clarendon|ballston|courthouse|crystal city|pentagon city|"
    r"falls church|bethesda|silver spring|takoma park|hyattsville|college park|chevy chase,? md|"
    r"national harbor|virginia|maryland)\b|,\s*(va|md)\b"
)

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
GROUND_FLOOR_PATTERNS = [r"ground[- ]floor", r"first[- ]floor", r"1st[- ]floor", r"street[- ]level", r"main level"]
NOT_WHAT_YOU_WANT = {
    r"\broom for rent\b|\broommate\b|\bshared (apartment|house|bath)|\bprivate room\b": "shared housing / room",
    r"\bstudio\b(?!.*\b1\s?(br|bd|bed))": "studio, not a 1BR",
    r"\b(2|two)\s?(br|bd|bed(room)?s?)\b": "more than 1 bedroom",
    r"\bsublet\b|\bsublease\b|short[- ]term|month[- ]to[- ]month only": "sublet / short term",
}
SCAM_PATTERNS = [
    r"western union", r"moneygram", r"wire (the )?(money|deposit|funds)", r"out of (the )?(country|town) (for|on)",
    r"(mail|ship|send) (you )?the keys", r"deposit before (viewing|showing|seeing)", r"can'?t show (the )?(unit|apartment)",
    r"gift ?cards?", r"missionary", r"i am currently (abroad|overseas)",
]
AMENITIES = {
    "in_unit_laundry": r"w/?d in[- ]unit|washer/?(and )?dryer in[- ](the )?unit|in[- ]unit (washer|laundry|w/?d)"
                       r"|washer (and|&) dryer included|stacked washer|private (washer|laundry)",
    "central_air": r"central (air|a/?c|ac)\b|central air conditioning|central heat(ing)? (and|&) (air|a/?c|cooling)"
                   r"|mini[- ]split|ductless|heat pump",
    "dishwasher": r"dishwasher",
    "outdoor_space": r"balcony|private patio|roof ?(top)? ?deck|rooftop",
    "hardwood": r"hardwood",
    "workspace": r"\bden\b|office nook|home office|office space|flex (room|space)|bonus room|work[- ]from[- ]home|wfh",
    "pets_allowed": r"(cats?|dogs?) (are )?ok|pet[- ]friendly|pets? (ok|allowed|welcome)",
    "gym": r"fitness (center|room)|\bgym\b",
    "doorman": r"concierge|doorman|front desk",
    "parking": r"(off[- ]street|garage|reserved) parking|parking (included|available|space)",
}
# If a listing says one of these (and not the positive pattern above), the amenity is definitely missing.
AMENITY_NEGATIVES = {
    "in_unit_laundry": r"shared laundry|laundry (room|facilit(y|ies)) (in|on)|laundry in (the )?(building|basement)"
                       r"|coin[- ]op|coin laundry|laundromat|on[- ]site laundry|laundry on[- ]site|common laundry"
                       r"|no (in[- ]unit )?(laundry|w/?d|washer)",
    "central_air": r"window (a/?c|ac|air|units?)|wall (a/?c|ac|units?)|portable (a/?c|ac)|no (a/?c|air conditioning)"
                   r"|radiator heat only",
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_AVAIL = r"(?:avail(?:able|\.)?|move[- ]in(?: date)?|lease start(?:s|ing)?|ready)\s*(?:on|from|starting|beginning|as of|by|:|-)?\s*"
AVAILABLE_NOW_RE = re.compile(_AVAIL + r"(now|immediately|today|asap)\b")
AVAILABLE_MONTH_RE = re.compile(_AVAIL + r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?")
AVAILABLE_SLASH_RE = re.compile(_AVAIL + r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
SQFT_RE = re.compile(r"(\d{3,4})\s*(?:sq\.?\s?ft\.?|sqft|square feet|square foot|ft2|ft²|sf)\b")

STREET_TYPES = (r"St|Street|Ave|Avenue|Rd|Road|Pl|Place|Ter|Terrace|Ct|Court|Dr|Drive|Blvd|Boulevard|Way|"
                r"Cir|Circle|Sq|Square|Ln|Lane|Pkwy|Parkway|Aly|Alley|Row|Walk")
# DC addresses always carry a quadrant, which makes them easy to pick out of email text.
ADDRESS_RE = re.compile(
    rf"\b(\d{{1,5}}(?:-\d+)?\s+(?:(?:[A-Za-z][A-Za-z.'-]*|\d+(?:st|nd|rd|th))\s+){{1,4}}?(?:{STREET_TYPES})\.?,?\s+"
    rf"(?:NW|NE|SW|SE|N\.W\.|N\.E\.|S\.W\.|S\.E\.))(?![A-Za-z])"
    rf"(?:[ ,]*(?:Apt\.?|Unit|#|Ste\.?|Suite)\s*#?([A-Za-z0-9-]+))?",
    re.I,
)
ALERT_SENDERS = {
    "zillow.com": "zillow", "apartments.com": "apartments.com", "hotpads.com": "hotpads",
    "redfin.com": "redfin", "trulia.com": "trulia", "rent.com": "rent.com", "zumper.com": "zumper",
    "padmapper.com": "padmapper",
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
    address: str = ""
    unit: str = ""
    sqft: int | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    also_on: list[str] = field(default_factory=list)
    # Filled in by evaluate()
    neighborhood: str = "Unknown"
    tier: str = "unknown"
    nearest_metro: str = ""
    metro_km: float | None = None
    available: date | None = None
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    amenities: list[str] = field(default_factory=list)
    rejected: str | None = None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.location}\n{self.description}".lower()

    @property
    def address_key(self) -> str:
        return normalize_address(self.address, self.unit) if self.address else ""


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def in_dc(lat: float, lon: float) -> bool:
    inside, pts = False, DC_BOUNDARY
    for (y1, x1), (y2, x2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def parse_price(raw) -> int | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    m = re.search(r"\$?\s*(\d{1,2},?\d{3})(?!\d)", str(raw))
    return int(m.group(1).replace(",", "")) if m else None


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(text)).strip()


_ABBREV = {"street": "st", "avenue": "ave", "road": "rd", "place": "pl", "terrace": "ter", "court": "ct",
           "drive": "dr", "boulevard": "blvd", "circle": "cir", "square": "sq", "lane": "ln", "parkway": "pkwy",
           "alley": "aly", "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se"}


def normalize_address(address: str, unit: str = "") -> str:
    a = re.sub(r"[.,#]", " ", address.lower())
    a = re.sub(r"\b(n) w\b|\bn\s*w\b", "nw", a)
    words = [_ABBREV.get(w, w) for w in a.split()]
    words = [w for w in words if w not in ("apt", "unit", "ste", "suite", "washington", "dc")]
    words = [w for w in words if not re.fullmatch(r"200\d\d", w)]  # zip
    u = re.sub(r"[^a-z0-9]", "", unit.lower().replace("apt", "").replace("unit", ""))
    return " ".join(words) + (f" #{u}" if u else "")


def unit_floor(unit: str) -> int | str | None:
    """Best guess at the floor from a unit number: an int, 'basement', 'maybe_lower', 'top' or None."""
    u = re.sub(r"^(apt|apartment|unit|ste|suite|#)\s*", "", unit.strip().lower()).lstrip("#").strip()
    if not u:
        return None
    if re.search(r"\b(ll|bsmt|basement|lower|garden|terrace|cellar)\b|^ll\d*$", u):
        return "basement"
    if u.startswith("ph"):
        return "top"
    if m := re.fullmatch(r"(\d{3,4})[a-z]?", u):
        floor = int(m.group(1)[:-2])
        return "basement" if floor == 0 else floor
    if m := re.fullmatch(r"(\d{1,2})-?[a-z]", u):
        return int(m.group(1))
    if re.fullmatch(r"[gltb]-?\d*", u):
        return "maybe_lower"
    return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(url: str, retries: int = 3, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en", **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            if attempt == retries - 1:
                raise
        except OSError:
            if attempt == retries - 1:
                raise
        print(f"  fetch failed; retrying {url[:80]}", file=sys.stderr)
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("unreachable")


def geocode(address: str, db: sqlite3.Connection) -> tuple[float, float] | None:
    """US Census geocoder: free, no key. Results are cached in the state DB."""
    row = db.execute("SELECT lat, lon FROM geocache WHERE address = ?", (address,)).fetchone()
    if row:
        return None if row[0] is None else (row[0], row[1])
    q = urllib.parse.urlencode({"address": f"{address}, Washington, DC", "benchmark": "Public_AR_Current", "format": "json"})
    try:
        data = json.loads(fetch(f"https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?{q}", retries=2))
        matches = data["result"]["addressMatches"]
        coords = (matches[0]["coordinates"]["y"], matches[0]["coordinates"]["x"]) if matches else None
    except (OSError, ValueError, KeyError) as exc:
        print(f"  geocode failed for {address}: {exc}", file=sys.stderr)
        return None  # don't cache transient failures
    db.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?)", (address, *(coords or (None, None))))
    return coords


# ---------------------------------------------------------------------------
# Source: Craigslist
# ---------------------------------------------------------------------------

def craigslist_search_url(cfg: dict) -> str:
    s, c = cfg["search"], cfg["sources"]["craigslist"]
    params = {
        "min_bedrooms": s["bedrooms"], "max_bedrooms": s["bedrooms"],
        "min_bathrooms": s["bathrooms"], "max_bathrooms": s["bathrooms"],
        "max_price": s["max_rent"] + s.get("stretch", 0),
        "min_price": s.get("min_rent", 0),
        "sort": "date",
        **c.get("extra_params", {}),
    }
    subarea = f"/{c['subarea']}" if c.get("subarea") else ""
    return f"https://{c['site']}.craigslist.org/search{subarea}/apa?{urllib.parse.urlencode(params)}"


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
    # Attribute blocks ("w/d in unit", "650ft2", "available jan 31") sit between the map and the body.
    attrs = re.search(r'class="mapAndAttrs"(.*?)id="postingbody"', page, re.S)
    if attrs:
        listing.description += "\n" + strip_tags(attrs.group(1))
    lat = re.search(r'data-latitude="(-?[\d.]+)"', page)
    lon = re.search(r'data-longitude="(-?[\d.]+)"', page)
    if lat and lon:
        listing.lat, listing.lon = float(lat.group(1)), float(lon.group(1))
    mapaddr = re.search(r'<div class="mapaddress">(.*?)</div>', page, re.S)
    if m := ADDRESS_RE.search(strip_tags(mapaddr.group(1)) if mapaddr else listing.description):
        listing.address, listing.unit = m.group(1), m.group(2) or listing.unit


def search_craigslist(cfg: dict, seen: set[str], db: sqlite3.Connection) -> list[Listing]:
    c = cfg["sources"]["craigslist"]
    url = craigslist_search_url(cfg)
    print(f"[craigslist] {url}")
    listings = parse_search_results(fetch(url))
    print(f"  {len(listings)} results")
    if c.get("fetch_details", True):
        todo = [l for l in listings if l.id not in seen][: c.get("max_detail_fetches", 40)]
        for i, listing in enumerate(todo, 1):
            print(f"  details {i}/{len(todo)}: {listing.title[:60]}")
            try:
                enrich_from_detail(listing, fetch(listing.url))
            except OSError as exc:
                listing.flags.append(f"couldn't load details ({exc})")
            time.sleep(c.get("request_delay_seconds", 2.0))
    return listings


# ---------------------------------------------------------------------------
# Source: saved-search alert emails (Zillow, Apartments.com, HotPads, ...)
# ---------------------------------------------------------------------------

def parse_alert_email(body: str, source: str) -> list[Listing]:
    """Pull listing cards out of an alert email without knowing its exact layout.

    Every card has a DC street address (always with a quadrant), a price and a
    link nearby, so find each address and take the closest price and link that
    aren't closer to a different address.
    """
    links: list[str] = []

    def mark(m: re.Match) -> str:
        links.append(html.unescape(m.group(1)))
        return f" \x00{len(links) - 1}\x00 "

    marked = re.sub(r'<a\b[^>]*?href="([^"]+)"[^>]*>', mark, body, flags=re.I)
    text = re.sub(r"\s+", " ", strip_tags(marked) if "<" in body else marked)
    matches = list(ADDRESS_RE.finditer(text))
    out: dict[str, Listing] = {}
    for i, m in enumerate(matches):
        lo = matches[i - 1].end() if i else max(0, m.start() - 400)
        hi = matches[i + 1].start() if i + 1 < len(matches) else m.end() + 400
        before, after = text[max(lo, m.start() - 400):m.start()], text[m.end():min(hi, m.end() + 400)]
        near = before[-200:] + " " + after[:250]

        def nearest(pattern: str) -> str | None:
            # Closest match on either side of the address.
            cands = [(len(before) - m2.end(), m2.group(1)) for m2 in re.finditer(pattern, before)]
            cands += [(m2.start(), m2.group(1)) for m2 in re.finditer(pattern, after)]
            return min(cands)[1] if cands else None

        # Prefer a price inside the same link block as the address (whole card is one <a>).
        block = re.split(r"\x00\d+\x00", before)[-1] + " " + re.split(r"\x00\d+\x00", after)[0]
        price_re = r"(\$\s?\d{1,2},?\d{3})(?!\d)"
        price = parse_price((re.findall(price_re, block) or [nearest(price_re)])[0])
        # The card's link is the <a> the address sits in: the last one opened before it.
        link_ids = re.findall(r"\x00(\d+)\x00", before)[-1:] or re.findall(r"\x00(\d+)\x00", after)[:1]
        link_id = link_ids[0] if link_ids else None
        url = links[int(link_id)] if link_id else ""
        if not url and price is None:
            continue
        beds = re.search(r"(\d)\s*(?:bd|bds|beds?|br)\b", near, re.I)
        baths = re.search(r"(\d(?:\.\d)?)\s*(?:ba|baths?)\b", near, re.I)
        sqft = SQFT_RE.search(near.lower())
        address, unit = m.group(1), m.group(2) or ""
        listing = Listing(
            id=f"{source}-{normalize_address(address, unit)}",
            url=url, title=f"{address}{' #' + unit if unit else ''}", price=price,
            location=f"{address}, Washington, DC", source=source,
            description=re.sub(r"\x00\d+\x00", " ", near).strip(),
            address=address, unit=unit,
            sqft=int(sqft.group(1)) if sqft else None,
            bedrooms=float(beds.group(1)) if beds else None,
            bathrooms=float(baths.group(1)) if baths else None,
        )
        out.setdefault(listing.address_key, listing)
    return list(out.values())


def search_email_alerts(cfg: dict, db: sqlite3.Connection) -> list[Listing]:
    conf = cfg["sources"]["email_alerts"]
    user, pw = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    if not (user and pw):
        print("[email_alerts] skipped: set IMAP_USER and IMAP_PASSWORD (see README)", file=sys.stderr)
        return []
    since = (date.today() - timedelta(days=conf.get("days", 3))).strftime("%d-%b-%Y")
    listings: list[Listing] = []
    with imaplib.IMAP4_SSL(conf.get("imap_host", "imap.gmail.com")) as imap:
        imap.login(user, pw)
        imap.select(f'"{conf.get("folder", "INBOX")}"', readonly=True)
        for domain in conf.get("senders", list(ALERT_SENDERS)):
            _, data = imap.search(None, f'(SINCE {since} FROM "{domain}")')
            ids = data[0].split()
            print(f"[email_alerts] {len(ids)} emails from {domain}")
            for num in ids:
                _, parts = imap.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(parts[0][1], policy=email.policy.default)
                part = msg.get_body(preferencelist=("html", "plain"))
                if part is not None:
                    listings += parse_alert_email(part.get_content(), ALERT_SENDERS.get(domain, domain))
    return listings


# ---------------------------------------------------------------------------
# Source: RentCast API (https://www.rentcast.io/api)
# ---------------------------------------------------------------------------

def parse_rentcast(items: list[dict]) -> list[Listing]:
    out = []
    for it in items:
        address, unit = it.get("addressLine1", ""), it.get("addressLine2") or ""
        full = it.get("formattedAddress") or address
        contact = " / ".join(filter(None, [
            (it.get("listingAgent") or {}).get("name"), (it.get("listingAgent") or {}).get("phone"),
            (it.get("listingOffice") or {}).get("name"),
        ]))
        out.append(Listing(
            id=f"rentcast-{it.get('id', full)}",
            url="https://www.zillow.com/homes/" + urllib.parse.quote(full.replace(" ", "-")) + "_rb/",
            title=f"{it.get('propertyType', 'Rental')} at {full}",
            price=parse_price(it.get("price")),
            location=f"{full}", source="rentcast",
            description=f"Listed {str(it.get('listedDate', ''))[:10]}. {contact}".strip(),
            lat=it.get("latitude"), lon=it.get("longitude"),
            address=address, unit=unit, sqft=it.get("squareFootage"),
            bedrooms=it.get("bedrooms"), bathrooms=it.get("bathrooms"),
        ))
    return out


def search_rentcast(cfg: dict, db: sqlite3.Connection) -> list[Listing]:
    conf, s = cfg["sources"]["rentcast"], cfg["search"]
    key = os.environ.get("RENTCAST_API_KEY")
    if not key:
        print("[rentcast] skipped: set RENTCAST_API_KEY (see README)", file=sys.stderr)
        return []
    # The free plan is 50 calls/month, so don't call more often than configured.
    last = db.execute("SELECT value FROM meta WHERE key = 'rentcast_last_run'").fetchone()
    min_gap = timedelta(hours=conf.get("min_hours_between_runs", 20))
    if last and datetime.now() - datetime.fromisoformat(last[0]) < min_gap:
        print(f"[rentcast] skipped: last call {last[0]}, waiting {min_gap} between calls")
        return []
    params = {
        "city": "Washington", "state": "DC", "status": "Active",
        "bedrooms": s["bedrooms"], "bathrooms": s["bathrooms"],
        "price": f"{s.get('min_rent', 0)}:{s['max_rent'] + s.get('stretch', 0)}",
        "daysOld": conf.get("max_days_old", 14), "limit": 500,
    }
    url = f"https://api.rentcast.io/v1/listings/rental/long-term?{urllib.parse.urlencode(params)}"
    print(f"[rentcast] {url}")
    items = json.loads(fetch(url, headers={"X-Api-Key": key, "Accept": "application/json"}))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('rentcast_last_run', ?)", (datetime.now().isoformat(timespec="seconds"),))
    db.commit()
    print(f"  {len(items)} results")
    return parse_rentcast(items)


# ---------------------------------------------------------------------------
# Source: CSV you fill in by hand
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[Listing]:
    """Columns: url,title,price,location,description,lat,lon,address,unit,sqft (url/title/price required)."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            num = lambda k, t=float: t(row[k]) if row.get(k) else None  # noqa: E731
            out.append(Listing(
                id=f"csv-{row['url']}", url=row["url"], title=row.get("title", ""),
                price=parse_price(row.get("price")), location=row.get("location", ""), source="manual",
                description=row.get("description", ""), lat=num("lat"), lon=num("lon"),
                address=row.get("address", ""), unit=row.get("unit", ""), sqft=num("sqft", int),
            ))
    return out


# ---------------------------------------------------------------------------
# Combining sources
# ---------------------------------------------------------------------------

def merge_duplicates(listings: list[Listing]) -> list[Listing]:
    """The same unit often shows up on several sites; keep the most detailed copy."""
    by_key: dict[str, Listing] = {}
    out = []
    for l in listings:
        key = l.address_key
        if not key:
            out.append(l)
            continue
        if key not in by_key:
            by_key[key] = l
            out.append(l)
            continue
        keep = by_key[key]
        if len(l.description) > len(keep.description):
            out[out.index(keep)] = l
            l, keep = keep, l
            by_key[key] = keep
        keep.also_on.append(f"{l.source}: {l.url}")
        keep.also_on += [a for a in l.also_on if a not in keep.also_on]
        for attr in ("lat", "lon", "sqft", "price", "bedrooms", "bathrooms"):
            if getattr(keep, attr) is None:
                setattr(keep, attr, getattr(l, attr))
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


def parse_available(text: str, today: date) -> date | None:
    if AVAILABLE_NOW_RE.search(text):
        return today
    if m := AVAILABLE_MONTH_RE.search(text):
        month, day, year = MONTHS[m.group(1)], int(m.group(2)), m.group(3)
    elif m := AVAILABLE_SLASH_RE.search(text):
        month, day, year = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        return None
    try:
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        guess = date(today.year, month, day)
        return guess if guess >= today - timedelta(days=45) else date(today.year + 1, month, day)
    except ValueError:
        return None


def evaluate(listing: Listing, cfg: dict, today: date | None = None) -> Listing:
    s, loc, prefs = cfg["search"], cfg.get("location", {}), cfg.get("preferences", {})
    today = today or date.today()
    text = listing.text
    score = 50

    def reject(why: str) -> None:
        listing.rejected = listing.rejected or why

    # --- hard filters -----------------------------------------------------
    if (m := any_match(BASEMENT_PATTERNS, text)):
        reject(f"basement / below grade ('{m}')")
    if (m := any_match(SCAM_PATTERNS, text)):
        reject(f"scam language ('{m}')")
    for pattern, why in NOT_WHAT_YOU_WANT.items():
        if re.search(pattern, listing.title.lower()):
            reject(why)
    if listing.bedrooms is not None and listing.bedrooms != s["bedrooms"]:
        reject(f"{listing.bedrooms:g} bedrooms")
    if listing.bathrooms is not None and listing.bathrooms < s["bathrooms"]:
        reject(f"{listing.bathrooms:g} bathrooms")

    # --- DC only ------------------------------------------------------------
    where = f"{listing.location}\n{listing.address}".lower()
    if m := re.search(NOT_DC_TEXT, where):
        reject(f"outside DC ('{m.group(0).strip(', ')}')")
    if listing.lat is not None and listing.lon is not None and not in_dc(listing.lat, listing.lon):
        reject("outside DC (map pin)")

    # --- price ----------------------------------------------------------------
    budget, stretch = s["max_rent"], s.get("stretch", 0)
    if listing.price is None:
        listing.flags.append("no price listed")
    elif listing.price > budget + stretch:
        reject(f"${listing.price:,} is over budget")
    elif listing.price > budget:
        pts = round(10 * (listing.price - budget) / max(stretch, 1))
        score -= pts
        listing.flags.append(f"${listing.price - budget} over budget, within your stretch (-{pts})")
    elif listing.price < s.get("min_rent", 0):
        score -= 30
        listing.flags.append(f"${listing.price:,} is suspiciously cheap for a DC 1BR - likely scam")
    else:
        savings = min(10, round((budget - listing.price) / budget * 50))
        if savings:
            score += savings
            listing.reasons.append(f"${budget - listing.price} under budget (+{savings})")

    # --- neighborhood -----------------------------------------------------------
    listing.neighborhood, listing.tier = resolve_neighborhood(listing, cfg)
    if listing.tier == "excluded":
        reject(f"neighborhood excluded ({listing.neighborhood})")
    elif listing.tier == "unknown":
        if not loc.get("allow_unknown", True):
            reject("neighborhood unknown")
        listing.flags.append("couldn't pin down the neighborhood - check the map")
    elif listing.tier == "caution":
        listing.flags.append(f"{listing.neighborhood}: mixed area, check the block on crimecards.dc.gov")
    pts = TIER_POINTS.get(listing.tier, 0)
    if pts > 0:
        listing.reasons.append(f"{listing.neighborhood} ({listing.tier}, +{pts})")
    score += pts
    if fav := loc.get("favorites", {}).get(listing.neighborhood):
        score += fav
        listing.reasons.append(f"a favorite area (+{fav})")

    # --- light & privacy ----------------------------------------------------------
    light_hits = sorted({m.group(0) for p in LIGHT_PATTERNS if (m := re.search(p, text))})
    if light_hits:
        pts = min(16, 4 * len(light_hits))
        score += pts
        listing.reasons.append(f"light: {', '.join(light_hits)} (+{pts})")
    else:
        listing.flags.append("no mention of light/windows - ask or check photos")

    floor = unit_floor(listing.unit)
    if floor is None and (m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]floor\b", text)):
        floor = int(m.group(1))
    if floor is None and re.search(r"top floor|penthouse", text):
        floor = "top"
    ground_penalty = prefs.get("ground_floor_penalty", 12)
    if floor == "basement":
        reject(f"basement unit (#{listing.unit})")
    elif floor == "maybe_lower":
        score -= ground_penalty
        listing.flags.append(f"unit #{listing.unit} may be garden/lower level - confirm before touring (-{ground_penalty})")
    elif floor == 1 or (floor is None and any_match(GROUND_FLOOR_PATTERNS, text)):
        score -= ground_penalty
        listing.flags.append(f"ground floor - people can see in from the street (-{ground_penalty})")
    elif floor == "top" or (isinstance(floor, int) and floor >= 3):
        score += 6
        listing.reasons.append(f"{'top' if floor == 'top' else f'floor {floor}'}: more light and privacy (+6)")
    elif floor == 2:
        score += 2
        listing.reasons.append("2nd floor (+2)")

    # --- size (you work from home) -------------------------------------------------
    size = prefs.get("size", {})
    if listing.sqft is None and (m := SQFT_RE.search(text)):
        listing.sqft = int(m.group(1))
    if listing.sqft:
        if listing.sqft >= size.get("great", 750):
            score += 8
            listing.reasons.append(f"{listing.sqft} sq ft - spacious (+8)")
        elif listing.sqft >= size.get("good", 600):
            score += 5
            listing.reasons.append(f"{listing.sqft} sq ft - room for a desk (+5)")
        elif listing.sqft < size.get("min", 475):
            score -= 8
            listing.flags.append(f"only {listing.sqft} sq ft - tight for a desk (-8)")
    else:
        listing.flags.append("size not listed")

    # --- move-in timing ---------------------------------------------------------------
    mv = cfg.get("move_in", {})
    if mv.get("date"):
        target = date.fromisoformat(mv["date"])
        listing.available = parse_available(text, today)
        early, late = mv.get("earliest_ok_days", 21), mv.get("latest_ok_days", 10)
        if listing.available is None:
            pass
        elif target - timedelta(days=early) <= listing.available <= target + timedelta(days=late):
            score += 8
            listing.reasons.append(f"available {listing.available:%b %d} - matches your move (+8)")
        elif listing.available < target - timedelta(days=early):
            score -= 6
            listing.flags.append(f"available {listing.available:%b %d}, "
                                 f"{(target - listing.available).days} days early - ask if they'll hold it (-6)")
        else:
            score -= 10
            listing.flags.append(f"not available until {listing.available:%b %d} (-10)")

    # --- amenities ------------------------------------------------------------------
    weights = prefs.get("weights", {})
    for name, pattern in AMENITIES.items():
        if re.search(pattern, text):
            listing.amenities.append(name)
            score += weights.get(name, 0)
    if listing.amenities:
        listing.reasons.append("has: " + ", ".join(a.replace("_", " ") for a in listing.amenities))
    # Alert emails and API feeds carry no description, so silence there means "unknown", not "missing".
    detailed = len(listing.description) >= 250
    missing_penalty = prefs.get("must_have_missing_penalty", 12) if detailed else 0
    unchecked = []
    for must in prefs.get("must_have", []):
        if must in listing.amenities:
            continue
        label = must.replace("_", " ")
        if (neg := AMENITY_NEGATIVES.get(must)) and (m := re.search(neg, text)):
            reject(f"no {label} ('{m.group(0)}')")
        elif detailed:
            score -= missing_penalty
            listing.flags.append(f"doesn't mention {label} - confirm (-{missing_penalty})")
        else:
            unchecked.append(label)
    if unchecked:
        listing.flags.append(f"no description in this feed - open the listing to check {', '.join(unchecked)}")

    # --- transit / commute ----------------------------------------------------------
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
        score INTEGER, rejected TEXT, first_seen TEXT, address_key TEXT)""")
    if "address_key" not in [r[1] for r in db.execute("PRAGMA table_info(seen)")]:
        db.execute("ALTER TABLE seen ADD COLUMN address_key TEXT")
    db.execute("CREATE TABLE IF NOT EXISTS geocache (address TEXT PRIMARY KEY, lat REAL, lon REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
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
        w.writerow(["score", "price", "neighborhood", "tier", "sqft", "available", "address", "title", "url",
                    "source", "also_on", "nearest_metro", "metro_km", "amenities", "reasons", "flags", "rejected"])
        for l in sorted(listings, key=lambda l: (l.rejected is not None, -l.score)):
            w.writerow([l.score, l.price, l.neighborhood, l.tier, l.sqft or "", l.available or "",
                        f"{l.address} {l.unit}".strip(), l.title, l.url, l.source, " | ".join(l.also_on),
                        l.nearest_metro, f"{l.metro_km:.2f}" if l.metro_km is not None else "",
                        ";".join(l.amenities), " | ".join(l.reasons), " | ".join(l.flags), l.rejected or ""])

    lines = [f"# DC apartment hunt - {datetime.now():%a %b %d, %Y %I:%M %p}", "",
             f"{len(keep)} worth a look, {len(below)} below score {min_score}, {len(rejected)} filtered out.", ""]
    for l in keep:
        price = f"${l.price:,}" if l.price else "$?"
        lines += [f"## {l.score}/100 - {price} - {l.neighborhood}", f"[{l.title}]({l.url}) ({l.source})", ""]
        lines += [f"- also on {a}" for a in l.also_on]
        lines += [f"- ✅ {r}" for r in l.reasons] + [f"- ⚠️ {f}" for f in l.flags] + [""]
    if rejected:
        lines += ["<details><summary>Filtered out</summary>", ""]
        lines += [f"- {l.rejected}: [{l.title}]({l.url})" for l in rejected]
        lines += ["", "</details>", ""]
    md_path = out_dir / f"apartments_{stamp}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path


def send_email(subject: str, body: str, to: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    user = os.environ.get("SMTP_USER") or os.environ.get("IMAP_USER")
    pw = os.environ.get("SMTP_PASSWORD") or os.environ.get("IMAP_PASSWORD")
    if not (user and pw and to):
        print("Email skipped: set SMTP_USER, SMTP_PASSWORD and [email] to.", file=sys.stderr)
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as smtp:
        smtp.starttls()
        smtp.login(user, pw)
        smtp.send_message(msg)
    print(f"Emailed report to {to}")


def load_config(path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def gather(cfg: dict, seen: set[str], db: sqlite3.Connection) -> list[Listing]:
    sources = cfg.get("sources", {})
    runners = {"craigslist": lambda: search_craigslist(cfg, seen, db),
               "email_alerts": lambda: search_email_alerts(cfg, db),
               "rentcast": lambda: search_rentcast(cfg, db)}
    listings: list[Listing] = []
    for name, run in runners.items():
        if not sources.get(name, {}).get("enabled", False):
            continue
        try:
            listings += run()
        except Exception as exc:  # one broken source shouldn't sink the others
            print(f"[{name}] failed: {exc}", file=sys.stderr)
    return listings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--all", action="store_true", help="report previously seen listings too")
    ap.add_argument("--import-csv", metavar="FILE", help="score listings from a CSV instead of searching")
    ap.add_argument("--only", choices=["craigslist", "email_alerts", "rentcast"], help="run just one source")
    ap.add_argument("--no-details", action="store_true", help="skip opening each Craigslist listing (faster, less accurate)")
    ap.add_argument("--email", action="store_true", help="email the report (see README for setup)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.no_details:
        cfg["sources"]["craigslist"]["fetch_details"] = False
    if args.only:
        for name, conf in cfg["sources"].items():
            conf["enabled"] = name == args.only
    out_dir = Path(cfg.get("output", {}).get("dir", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    db = open_db(out_dir / "seen.sqlite")
    seen = {row[0] for row in db.execute("SELECT id FROM seen")}
    seen_addresses = {row[0] for row in db.execute("SELECT address_key FROM seen WHERE address_key != ''")}

    listings = merge_duplicates(load_csv(args.import_csv) if args.import_csv else gather(cfg, seen, db))
    if not args.all:
        listings = [l for l in listings if l.id not in seen and l.address_key not in seen_addresses]
    for l in listings:
        if l.lat is None and l.address:
            if coords := geocode(f"{l.address} {l.unit}".strip(), db):
                l.lat, l.lon = coords
        evaluate(l, cfg)

    now = datetime.now().isoformat(timespec="seconds")
    db.executemany(
        "INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?,?,?,?,?)",
        [(l.id, l.url, l.title, l.price, l.neighborhood, l.score, l.rejected, now, l.address_key) for l in listings],
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
