# DC apartment hunt

`apartment_hunt.py` collects 1BR/1BA rentals in DC from several sites. It removes listings you'd never take, scores the rest for fit, and writes a ranked report of anything new since the last run.

## Sources

Zillow, Apartments.com, HotPads, Redfin and Trulia have no public listings API. They also block automated scraping and forbid it in their terms, and scrapers that try get broken or banned within days. The script gets their listings in two supported ways instead:

| Source | How | Setup |
|---|---|---|
| **Craigslist** | Searched directly | None. It's on by default. |
| **Zillow, Apartments.com, HotPads, Redfin, Trulia, Zumper** (`email_alerts`) | You create a saved search with instant alerts on each site. The script reads those alert emails from your inbox over IMAP (read-only) and pulls out the address, price, size and link. Addresses are geocoded with the free US Census geocoder so neighborhood and Metro scoring still work. | [Email alerts](#email-alerts-zillow-apartmentscom-hotpads) |
| **RentCast API** (`rentcast`) | An aggregator of MLS and rental-site listings with a proper API. Its free plan allows 50 calls a month, so the script calls it at most once every 20 hours. | [RentCast](#rentcast-optional) |
| **Anything else** | `--import-csv file.csv` | Columns `url,title,price,location,description,lat,lon,address,unit,sqft` |

If the same unit shows up on several sites, the script merges it into one entry by address and lists the other sites under "also on".

## What it filters and scores

Hard filters (the listing is dropped):
- **Basements**, including DC euphemisms like *English basement, garden level, terrace level, lower level*, and unit numbers like `LL`, `B1` or `002`.
- **Anything outside DC.** Listings that mention Arlington, Alexandria, Bethesda and similar places are dropped, and so are map pins outside DC's boundary. That catches Rosslyn listings that claim to be in Georgetown.
- **Rent over $1,800.** That's the $1,700 budget plus your $100 stretch. Listings between $1,700 and $1,800 stay in but lose up to 10 points.
- **Listings that say there's no in-unit washer/dryer or no central air**, for example *shared laundry, laundry in building, window units*.
- **Studios, rooms, sublets and short-term rentals.**
- **Scam language**, such as *wire the deposit* or *I'll mail you the keys*.
- **Neighborhoods excluded as suburban or unsafe.**

Scoring (0–100):
- **Neighborhood.** Each neighborhood is rated prime, good, caution or excluded. Adams Morgan gets a +5 favorite bonus, and Kalorama, Dupont, Columbia Heights, Mount Pleasant and U St get +3.
- **Light.** Phrases like *sunny, south-facing, floor-to-ceiling, top floor* add points.
- **Privacy.**
  - The floor is read from the unit number or the description: `Apt 504` is the 5th floor, `PH` is the penthouse.
  - Ground floor costs **−12** points because people can see in from the street.
  - Floor 3 and up, or the top floor, adds **+6**.
- **Room for a desk.** 600 sq ft or more adds points, 750 or more adds more, and under 475 costs points. A den or office nook adds +5.
- **Move-in timing.** Listings available Jan 10 – Feb 10, 2027 get +8. Units available earlier are flagged so you can ask whether the landlord will hold them.
- **Metro.** Walking minutes to the nearest station.
- **Must-haves.** If a Craigslist ad doesn't mention in-unit washer/dryer or central air, it gets a "confirm" warning and −12. Email-alert and RentCast listings come without a description, so they just get a note to check the listing.
- **Gym, doorman, parking and pets are worth 0 points**, matching your answers.

## Timing for a Jan 31 move-in

Most DC landlords list 30–45 days before a unit is free, so the best Jan 31 listings will mostly appear from **mid-December to mid-January**. Running the script from late November still helps:
- You learn what $1,700–1,800 actually gets in each neighborhood.
- Some landlords and buildings pre-lease that early, and the script flags those with an "ask if they'll hold it" note.
- You'll be ready to move fast when the good listings appear. Good units in Adams Morgan and Columbia Heights often go within 2–3 days.

## Run it

Requires Python 3.11+. It uses only the standard library, so there's nothing to install.

```bash
python3 apartment_hunt.py                 # all enabled sources, new listings only
python3 apartment_hunt.py --only email_alerts
python3 apartment_hunt.py --all           # include listings already seen
python3 apartment_hunt.py --email         # email the report to [email] to
python3 -m unittest discover -s tests     # run the tests
```

The report goes to `reports/apartments_<date>.md`, with a `.csv` next to it. `reports/seen.sqlite` records what you've already seen.

### Email alerts (Zillow, Apartments.com, HotPads, …)

1. On each site, search **Washington, DC → 1 bed, 1+ bath, max $1,800**, then **Save search** and choose **instant** email alerts. Sites to set up:
   - Zillow
   - Apartments.com
   - HotPads
   - Redfin
   - Trulia (optional)
   - Zumper (optional)
2. Optional: create a Gmail filter that labels those emails *Apartments*, and set `folder = "Apartments"` in `config.toml`.
3. Create a Gmail **app password**: Google Account → Security → 2-Step Verification → App passwords.
4. Set the credentials and turn the source on:
   ```bash
   export IMAP_USER=you@gmail.com IMAP_PASSWORD="abcd efgh ijkl mnop"
   ```
   Then set `enabled = true` under `[sources.email_alerts]` in `config.toml`.

The same credentials are used to send the `--email` report. Set `[email] to = "you@gmail.com"`.

### RentCast (optional)

1. Sign up at <https://www.rentcast.io/api> (free plan) and create an API key.
2. Run `export RENTCAST_API_KEY=...`.
3. Set `enabled = true` under `[sources.rentcast]`.

### Run it automatically

Add a crontab entry (`crontab -e`) that runs at 8am, 1pm and 7pm:

```bash
0 8,13,19 * * * cd /path/to/repo && IMAP_USER=... IMAP_PASSWORD=... python3 apartment_hunt.py --email >> reports/cron.log 2>&1
```

## Tuning

Everything is in `config.toml`: budget and stretch, move-in date and window, must-haves, point weights, favorite neighborhoods and tier overrides.

## Limitations

- Alert emails don't include descriptions, so light, basement and W/D checks only work on Craigslist ads. For alert-email listings, the report tells you what to check when you open the listing.
- The parser works on any email layout that shows a DC street address (with its NW/NE/SW/SE quadrant) and a price. If a site changes its format and results stop appearing, check the run log for `[email_alerts] N emails` against listings found.
- Neighborhood tiers are opinions. Check the specific block at <https://crimecards.dc.gov>.
- Never pay a deposit before seeing the unit in person. You can confirm the landlord owns the property with a Real Property search at <https://mytax.dc.gov>.
