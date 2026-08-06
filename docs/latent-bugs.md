# Latent bugs found but not fixed in v0.6.0

Found by an adversarial and a cross-model review of the v0.6.0 branch on
2026-08-06. Every item here predates that branch, so none of them ship as a
v0.6.0 regression. They are logged rather than bundled, because widening a
release PR to cover unrelated code is how a release stops being reviewable.

Each one shares a single shape: a fail-soft path turns an upstream failure into
a normal-looking answer. That contradicts the failure-envelope contract in
CLAUDE.md, which v0.5.0 applied to list tools but never swept through the
single-value tools.

## 1. A malformed population cell becomes a population of zero

`sources/psa.py`, in `get_population_stats`. The parse handler assigns
`population = 0` on a `ValueError`, then follows the success path into the 24h
cache. A schema change or a garbled cell reports the Philippines as having zero
people, and repeats it from cache for a day.

Repro: return `{"data":[{"values":["not-a-number"]}]}` from the population POST.

Fix shape: use `_to_float`, and return an envelope when the cell will not
parse. Do not cache it.

Severity: high. This is a fabricated public figure, which the project's own
data-integrity rule forbids.

## 2. The volcano bulletin path fetches an absolute URL on the TLS-relaxed client

`sources/phivolcs.py`, in `_fetch_volcano_bulletin_list` and
`_fetch_volcano_alert`. `urljoin(WOVODAT_BASE, href)` keeps an absolute href
unchanged, so a WOVODAT page that links off-host sends `PHIVOLCS_CLIENT`, which
runs with `verify=False`, to that host.

`get_earthquake_bulletin` already guards this with `_is_phivolcs_url`. The
volcano path never got the same allowlist.

Fix shape: run every URL through `_is_phivolcs_url` before any
`PHIVOLCS_CLIENT` fetch, not only the one an agent supplies.

Severity: medium. It needs a compromised or changed upstream page to fire.

## 3. A per-volcano bulletin failure reads as a real alert with null fields

`sources/phivolcs.py`, in `get_volcano_status`. `_fetch_volcano_alert` returns
`(None, None)` on any failure, and the caller emits a normal alert record with
a null level. An agent cannot tell "alert level unknown" from "alert level not
published".

Severity: medium, and it sits on a hazard tool.

## 4. A subsistence-table outage becomes a null statistic

`sources/psa.py`, in `get_poverty_stats`. When subsistence discovery fails, the
result carries `subsistence_incidence_pct: null` and no `caveats` entry names
the failure, and the whole response caches for 24h. v0.6.0 logs the failure to
stderr but still does not surface it to the caller.

Severity: low. The poverty figure beside it is correct.

## 5. PSGC hierarchy turns an endpoint failure into "record not found"

`sources/psgc.py`, in the hierarchy lookup. A transport failure and a genuine
unknown code produce the same answer.

Severity: medium.

## 6. MODIS transport failures cache as an empty observation window

`sources/modis_ndvi.py`. An outage becomes a legitimate-looking empty result
and enters the cache.

Severity: medium. Same class as the v0.5.0 list-tool fix, one tool short.

## 7. `get_area_profile` drops a sibling's failure envelope

`sources/autostitch.py`. The composite catches a raised exception but passes a
returned failure envelope through as data. The block reads as present when its
source was down.

Severity: medium.

## 8. An unparseable poverty reference year silently becomes 2023

`sources/psa.py`, in `get_poverty_stats`. `except ValueError: year_int = 2023`
hardcodes a vintage when the label does not parse.

Severity: low, but it is a fabricated reference period.

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

## Suggested order

1, then 10, then 2, then 6 and 7 together, then 3 and 5, then 4, 8, 9 and 11.
Items 1, 8 and 10 are data-integrity defects and should lead. Item 2 is the
only one with a security shape.

Items 9, 10 and 11 came from the second cross-model pass on 2026-08-06.
