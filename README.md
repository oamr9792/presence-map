# Presence map

Measures which inputs you control (a LinkedIn profile, a Companies House filing, a
team page, a Crunchbase entry, a ZoomInfo Lite connection, a press release) cause
which third-party sites to publish a page about a person, how long that takes,
and how well each of those pages ranks for the person's name. Then, for any
person, produces an ordered plan: do this, expect these pages, in this many weeks.

Everything is empirical. The rules come from checking thousands of real people,
not from vendor documentation, and they are recomputed every time new checks land.

## How it works

1. **Collect** a cohort of subjects whose inputs are known: UK directors from
   Companies House (free API), US officers from Florida's free bulk register
   files, clients and consenting controlled-study subjects from CSV.
   OpenCorporates is wired as an optional paid source for other US states.
2. **Check** each subject against ~40 target sites (`config/targets.yaml`)
   through Google SERP queries (DataForSEO). Records whether a page exists, its
   URL, richness, and its position for the bare-name query. Also discovers
   inputs it sees in the SERP (LinkedIn, team page) so observational subjects
   get their inputs recorded without a separate crawl.
3. **Analyse**: for every (input, target) pair, P(page | input) vs
   P(page | no input), lift, and median lag where inputs are dated. Per target,
   how often it reaches page one or two.
4. **Plan**: greedy over controllable inputs, weighted by expected new pages ×
   how well those pages rank, divided by noise. The Plan view does this for a
   hypothetical person ("client with nothing"); the Subjects view does it for a
   real one.

The Board view draws the whole thing: inputs on the left, target sites on the
right, line width = lift, circle size = page-one rate.

## Run locally

    pip install -r requirements.txt
    cp .env.example .env            # fill in keys
    python -m app.jobs init
    python -m app.jobs seed-sample  # synthetic data so the UI has something to show
    python -m app.jobs analyse
    uvicorn app.main:app --reload

Then open http://localhost:8000.

## Real data

    python -m app.jobs collect-ch 200          # Companies House, needs COMPANIES_HOUSE_API_KEY
    python -m app.jobs collect-fl cordata.zip 500   # Florida officers from the free Sunbiz bulk file
    python -m app.jobs collect-oc us_fl 100    # optional: OpenCorporates (paid API)
    python -m app.jobs import-csv clients.csv client
    python -m app.jobs check --limit 50        # DataForSEO; ~40 SERP calls per subject
    python -m app.jobs analyse

`check` only runs subjects not checked in the last 30 days (`--recheck-days`),
so it can be put on a schedule and left alone. `--no-per-target` runs only the
bare-name SERPs (two calls per subject) and treats anything not seen there as
absent; much cheaper, slightly less sensitive.

Delete the sample cohort once real data is in:
`DELETE FROM subjects WHERE cohort='sample'` (checks and inputs cascade).

### Florida bulk file

Florida publishes its register, officers included, at sftp.floridados.gov
(user `Public`, password `PubAccess1845!`, both published on
dos.fl.gov/sunbiz/other-services/data-downloads). The browser can open the host
directly. Quarterly: `doc/quarterly/cor/cordata.zip` (all active records, ten
files inside). Daily: `doc/daily/cor/`. Download, then:

    python -m app.jobs collect-fl cordata.zip --probe   # prints three parsed records; check names and dates read correctly
    python -m app.jobs collect-fl cordata.zip 500       # random 500 officers filed in the last year

## Controlled subjects

The lag numbers come from subjects with dated inputs. For the observational
cohort that is only the filing date. To measure lag for the other inputs,
import consenting subjects by CSV with `<input>_date` columns, re-run `check`
on a schedule, and the first-seen dates give the lag. Use real, consenting
people only; invented people pollute the brokers and the data.

## Render

`render.yaml` defines a web service and a Postgres database. There is no
scheduled job; checks run only from the Jobs tab. Set the API keys in the
Render dashboard.

## Adding a target or input

Edit `config/targets.yaml` or `config/inputs.yaml` and restart; the registry
is upserted on startup. A target needs a `serp_query` template and a
`url_pattern` regex that matches person pages but not company pages.

## Costs

DataForSEO live SERP is the only metered call. Per subject: 2 bare-name
queries + one per target not already seen (typically 30-35). Roughly 1,000
subjects fully checked ≈ 35,000 SERPs.
