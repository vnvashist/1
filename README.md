# DC apartment hunt

`apartment_hunt.py` searches Craigslist for 1BR/1BA apartments in DC proper under $1,700. It filters out listings you don't want, scores the rest, and writes a ranked report of anything new since the last run.

## What it does

| Step | Details |
|---|---|
| **Search** | Craigslist `washingtondc` → DC proper (`doc`), 1BR/1BA, ≤ $1,700, newest first. |
| **Open each new listing** | Pulls the full description, the amenity tags and the map pin. |
| **Reject** | Basements, including DC euphemisms: *English basement, garden level, terrace level, lower level, below grade*. Also over-budget listings, studios, rooms or shares, and scam language (*wire the deposit, I'll mail you the keys, out of the country…*). |
| **Neighborhood** | Uses the map pin first, since posters often claim the trendier neighborhood next door. Falls back to the listing text. Each neighborhood has a tier: **prime** (Logan, Dupont, U St, Shaw, 14th St, Adams Morgan, Columbia Heights, Navy Yard, Capitol Hill, H St, NoMa, Wharf, Georgetown…), **good** (Petworth, Mt Pleasant, Woodley Park, Brookland…), **caution** (mixed or transitional areas, flagged) or **excluded** (suburban-feeling Upper NW, or areas with high reported violent crime). |
| **Score (0–100)** | Neighborhood tier, light keywords (*sunny, south-facing, floor-to-ceiling, top floor…*), floor number, walking minutes to Metro, money left under budget, and amenities you weight (in-unit W/D, dishwasher, central air…). |
| **Report** | `reports/apartments_<date>.md` (ranked, with ✅ reasons and ⚠️ warnings) plus a `.csv` for a spreadsheet. It can also email the report. |
| **Remember** | `reports/seen.sqlite` records listings you've already seen, so each run shows only new ones. |

## Run it

Needs Python 3.11 or later. It uses only the standard library, so there's nothing to install.

```bash
python3 apartment_hunt.py              # search and report new listings
python3 apartment_hunt.py --all        # include listings from earlier runs
python3 apartment_hunt.py --no-details # faster, but can't see light, amenities or map pin
python3 apartment_hunt.py --import-csv found.csv   # score listings you found on Zillow etc.
python3 -m unittest discover -s tests  # tests
```

The CSV for `--import-csv` uses these columns: `url,title,price,location,description,lat,lon`. Only `url`, `title` and `price` are required. Paste the listing description into `description` so the basement, light and amenity checks have something to work with.

### Run it automatically and get emailed

```bash
export SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=you@gmail.com SMTP_PASSWORD=<gmail app password>
# set [email] to = "you@gmail.com" in config.toml, then add to crontab -e:
0 8,12,18 * * * cd /path/to/repo && python3 apartment_hunt.py --email >> reports/cron.log 2>&1
```

Good DC apartments rent within days, so run it at least 2–3 times a day. Keep the delay between requests (`request_delay_seconds`) so Craigslist doesn't block you.

## Tuning (config.toml)

These questions each map to a setting. Answer them and change the value:

| Question | Setting |
|---|---|
| Is $1,700 a hard cap, or would you stretch $50–100 for a great place? Does it include utilities? | `stretch`, `max_rent` |
| When do you need to move in? Is your lease end fixed? | `extra_params.availabilityMode` |
| Is in-unit washer/dryer a must-have, nice-to-have, or don't care? | `must_have`, `weights.in_unit_laundry` |
| Do you have or plan to get a pet? Cat or dog? | `must_have = ["pets_allowed"]`, `extra_params.pets_cat` / `pets_dog` |
| Where do you work, and how often do you go in? Max commute? | `[location] work` |
| Do you have a car (need parking)? | `weights.parking` |
| Central air vs. window units? DC summers are brutal. | `weights.central_air` |
| Big managed building (gym, doorman, rooftop) or rowhouse/condo from an individual landlord? | `weights.gym`, `weights.doorman` |
| Are you open to Arlington (Clarendon/Ballston/Rosslyn)? It's lively, on the Orange/Silver line, and often cheaper per square foot. | `subarea = ""` |
| Any neighborhoods you love or hate that don't match my tiers? | `tier_overrides` |
| Is a ground-floor unit okay as long as it's not a basement? | scoring in `evaluate()` |
| Minimum size (sq ft)? Need room for a desk if you work from home? | not yet; easy to add |

## Limitations

- Craigslist is the only source it searches automatically. Zillow, Apartments.com and HotPads block scrapers and forbid scraping in their terms, so set up their saved-search email alerts and feed promising listings through `--import-csv`.
- "Natural light" is judged from listing text. Always check the photos and visit during the day.
- Neighborhood tiers are opinions. Check the specific block at <https://crimecards.dc.gov> before signing anything.
- Never send a deposit before seeing the unit in person and confirming the landlord owns it. You can check ownership at <https://mytax.dc.gov> (Real Property search).
