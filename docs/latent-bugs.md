# Latent bugs found but not fixed in v0.6.0

> Items 1, 4 and 8 were fixed during the review rounds and are marked below.
> Items 12 to 16 came from rounds 5 and 6. v0.6.1 fixed items 2, 7 and 21.
> Everything else still stands.

Found by an adversarial and a cross-model review of the v0.6.0 branch on
2026-08-06. Every item here predates that branch, so none of them ship as a
v0.6.0 regression. They are logged rather than bundled, because widening a
release PR to cover unrelated code is how a release stops being reviewable.

Each one shares a single shape: a fail-soft path turns an upstream failure into
a normal-looking answer. That contradicts the failure-envelope contract in
CLAUDE.md, which v0.5.0 applied to list tools but never swept through the
single-value tools.

## 1. FIXED in v0.6.0. A malformed population cell becomes a population of zero

`sources/psa.py`, in `get_population_stats`. The parse handler assigns
`population = 0` on a `ValueError`, then follows the success path into the 24h
cache. A schema change or a garbled cell reports the Philippines as having zero
people, and repeats it from cache for a day.

Repro: return `{"data":[{"values":["not-a-number"]}]}` from the population POST.

Fix shape: use `_to_float`, and return an envelope when the cell will not
parse. Do not cache it.

Severity: high. This is a fabricated public figure, which the project's own
data-integrity rule forbids.

Fixed: `_first_cell` reads the cell with a type check at every level, and an
unreadable cell returns an envelope with `upstream_error` that never caches.

## 2. FIXED in v0.6.1. The volcano bulletin path fetches an absolute URL on the TLS-relaxed client

`sources/phivolcs.py`, in `_fetch_volcano_bulletin_list` and
`_fetch_volcano_alert`. `urljoin(WOVODAT_BASE, href)` keeps an absolute href
unchanged, so a WOVODAT page that links off-host sends `PHIVOLCS_CLIENT`, which
runs with `verify=False`, to that host.

`get_earthquake_bulletin` already guards this with `_is_phivolcs_url`. The
volcano path never got the same allowlist.

Fix shape: run every URL through `_is_phivolcs_url` before any
`PHIVOLCS_CLIENT` fetch, not only the one an agent supplies.

Severity: medium. It needs a compromised or changed upstream page to fire.

Fixed: every PHIVOLCS fetch goes through `_fetch_phivolcs`, which checks the
URL and every redirect hop against the https host allowlist. The client no
longer follows redirects on its own. `tests/test_v061_phivolcs_security.py`
holds the adversarial cases.

## 3. A per-volcano bulletin failure reads as a real alert with null fields

`sources/phivolcs.py`, in `get_volcano_status`. `_fetch_volcano_alert` returns
`(None, None)` on any failure, and the caller emits a normal alert record with
a null level. An agent cannot tell "alert level unknown" from "alert level not
published".

Severity: medium, and it sits on a hazard tool.

## 4. FIXED in v0.6.0. A subsistence-table outage becomes a null statistic

`sources/psa.py`, in `get_poverty_stats`. When subsistence discovery fails, the
result carries `subsistence_incidence_pct: null` and no `caveats` entry names
the failure, and the whole response caches for 24h. v0.6.0 logs the failure to
stderr but still does not surface it to the caller.

Severity: low. The poverty figure beside it is correct.

Fixed: a discovery or query failure now reaches the caller as a `caveats`
entry with `upstream_error`, and that partial answer never caches.

## 5. PSGC hierarchy turns an endpoint failure into "record not found"

`sources/psgc.py`, in the hierarchy lookup. A transport failure and a genuine
unknown code produce the same answer.

Severity: medium.

## 6. MODIS transport failures cache as an empty observation window

`sources/modis_ndvi.py`. An outage becomes a legitimate-looking empty result
and enters the cache.

Severity: medium. Same class as the v0.5.0 list-tool fix, one tool short.

## 7. FIXED in v0.6.1. `get_area_profile` drops a sibling's failure envelope

`sources/autostitch.py`. The composite catches a raised exception but passes a
returned failure envelope through as data. The block reads as present when its
source was down.

Severity: medium.

Fixed: `_unwrap` folds both shapes into `caveats`, and the profile carries
`blocks` with one status per block plus a top-level `upstream_error`.

## 8. FIXED in v0.6.0. An unparseable poverty reference year silently becomes 2023

`sources/psa.py`, in `get_poverty_stats`. `except ValueError: year_int = 2023`
hardcodes a vintage when the label does not parse.

Severity: low, but it is a fabricated reference period.

Fixed: `_year_from_label` returns None and the tool returns an envelope rather
than publish a year nobody measured.

## 9. PAGASA: the list-response fallback is unreachable

`sources/pagasa.py`. `.get()` runs before the branch meant to handle a list
response, so the fallback never executes.

Severity: low.

## 10. PAGASA: zero rainfall reads as absent

`sources/pagasa.py`. A truthiness check treats `0` rainfall as missing and
substitutes a different precipitation field, so a genuinely dry day reports
another number.

Severity: medium. It is a wrong published figure, not a missing one.

## 11. World Bank: a wrong-typed records object caches as an empty success

`sources/world_bank.py`. v0.6.0 added `upstream_error` to the two failure paths
it has, but a 200 carrying an unexpected type still falls through to a normal
empty result.

Severity: low.

## 12. A failed metadata fetch can let an older PSA table win discovery

`sources/psa.py`, in `_pick_latest_table`. When the metadata GET for a
candidate fails, that candidate is skipped, so an older backcast table can win
and cache as the current series. The tool then reports stale figures as latest.

Severity: high. It publishes a wrong vintage silently. Predates v0.4.0.

## 13. A failed CPI query for the newest year falls back to an older year

`sources/psa.py`, in `get_inflation_stats`. The year loop walks backwards and
returns the first year that answers, labelling it the latest available result.
A transient failure on the newest year therefore reports an older month as
current.

Severity: high, same class as 12.

## 14. The national lookup substitutes the first regional value

`sources/psa.py`, in `_find_geo_value`. With `region=None` and no PHILIPPINES
entry in the metadata, it returns `values[0]`, which is whatever sits first.

Severity: medium. Only metadata drift reaches it.

## 15. A year-filtered infra search keeps records with an unknown year

`sources/infra.py`. A record whose publication year cannot be parsed passes a
year filter instead of being excluded.

Severity: medium.

## 16. The rate limiter counts logical calls, not physical retries

`sources/psa.py`. A 429 or 503 retry inside `fetch_with_retry` re-sends without
taking a new token, so the achieved rate can exceed the bucket under retries.
The 429 backoff covers the practical case.

Severity: low.

## 17. NASA POWER accepts an empty or invalid date and uses the default window

`sources/nasa_power.py`. A present but unusable date argument silently selects
the default window rather than telling the caller the argument was wrong.

Severity: medium.

## 18. Open-Meteo air-quality timestamps are labelled UTC but are Manila-local

`sources/open_meteo_aq.py`. The API returns naive local timestamps and the
parser attaches UTC, so every reading is off by eight hours.

Severity: medium. It is a wrong published time on every air-quality result.

## 19. An empty health-query data array becomes a cached null indicator

`sources/psa.py`, in `_latest_health_value`. When a year's query returns an
empty `data` array the loop moves on, and if every year is empty the indicator
reports a null value on the success path and caches for 24 hours.

Severity: medium, same fail-soft class as the rest of this list.

## 20. Curated-table discovery has no single-flight guard

`sources/psa.py`, in `_pick_latest_table`. v0.6.0 added single-flight to
`_browse` and to the catalog metadata fetch, but concurrent cold calls to the
curated tools still queue duplicate metadata GETs.

Severity: low. It costs duplicate fetches, never a wrong answer.

## 21. FIXED in v0.6.1. A non-integral population value truncates

`sources/psa.py`. `int()` on a float population silently drops the fraction
rather than reporting the unexpected type.

Severity: low.

Fixed. The value rounds to the nearest whole person, and a `caveats` entry
names the non-integral cell.

## Suggested order

1, then 10, then 2, then 6 and 7 together, then 3 and 5, then 4, 8, 9 and 11.
Items 1, 8 and 10 are data-integrity defects and should lead. Item 2 is the
only one with a security shape.

Items 9, 10 and 11 came from the second cross-model pass on 2026-08-06.
Items 12 to 16 came from rounds 5 and 6, 17 and 18 from round 8, and 19 to
21 from round 9, the same day. Nine review rounds ran against this branch. 12 and 13 are the most
urgent of the whole list: both publish a wrong vintage without saying so.
