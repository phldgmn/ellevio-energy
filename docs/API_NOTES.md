# Ellevio API notes

Source: sibling project `../ellevio` (`ellevio.py`). Re-implemented from scratch;
no code copied. Anything not directly seen in that code is marked *(unverified)*.
Login, consumption fetch and recorder import were verified against a live account on 2026-10-17 (see §7).

## 1. Authentication

Ellevio "Mina sidor" uses form login ending in an OpenID Connect (OIDC) callback.
No API key, no BankID, no CSRF token seen in reference.

Flow (`login()` / `submit_oidc_callback()` in reference):

1. `GET https://www.ellevio.se/privat/mina-sidor/Login/` with query
   `LoginMethods=9&loginTemplate=4&returnUrl=https://www.ellevio.se/privat/mina-sidor/oversikt/`.
   Locate the `<form>` containing `name="username"`, take its `action`
   (may be relative) and the hidden `name="method"` value (default `password.2`).
2. `POST <form action>` with `username=<email>`, `password=<password>`, `method=<method>`.
3. If final URL still contains `/uas/authn/`, login failed; the page contains
   `<div id="loginerror">` with a Swedish message. Otherwise the response is an
   auto-submitting OIDC form (`<form id="form" action=... method=...>` with hidden
   inputs). Submit those fields (POST or GET per `method`) to finish the callback.
4. Session = cookie jar from the request chain. Nothing else is stored.

Credentials the user supplies: Mina sidor **email + password**, **customer ID**
(`customerId` query param), and one or more **Anläggnings-ID**.

Session lifetime: *(unverified)*. Reference does not document it.

Session expiry detection (reference behaviour):
- HTTP 401/403 => session dead.
- HTTP 200 with a non-JSON (`text/html`) body => session dead. This is the usual
  failure mode. Do not trust the status code alone.

Re-login: reference re-runs the full login when the session is dead. The
integration maps a dead session to `AuthError` and raises
`ConfigEntryAuthFailed`, which starts HA's reauth flow.

## 2. Endpoints

Only one endpoint is used.

### Consumption

```
GET https://www.ellevio.se/api/mypages/energy/consumption/{anlage_id}
Query:
  from        YYYY-MM-DD
  to          YYYY-MM-DD
  resolution  QuarterHourly | Hourly | Daily | Monthly | Yearly
  interval    Daily | Weekly | Monthly | Yearly  (API-side aggregation window)
  customerId  <customer id>
Headers:
  User-Agent: <browser-like UA>
  Accept: application/json, text/plain, */*
  Accept-Language: sv-SE,sv;q=0.9,en;q=0.8
  Referer: https://www.ellevio.se/
Cookies: session cookies from login
```

Response (JSON, values redacted):

```json
{
  "data": {
    "consumptions": [
      {
        "start": "2026-10-16T00:00:00+02:00",
        "end": "2026-10-16T00:15:00+02:00",
        "normal": 0.0,
        "high": 0.0,
        "low": 0.123,
        "total": 0.123,
        "reactive": 0.0,
        "maxPower": 0.5,
        "debMaxPower": {"value": 0.0, "date": "0001-01-01T00:00:00+00:00", "status": "Missing"},
        "status": "Estimated",
        "dayLightSavingsTime": false
      }
    ]
  }
}
```

Rows sit at `data.consumptions`. Reference reads `entry["data"]["data"]["consumptions"]`
on its wrapped result. Only `start`, `total`, `status` are used here.
`normal`, `high`, `low`, `reactive`, `maxPower`, `debMaxPower`, `dayLightSavingsTime`
are ignored.

### Installations

- `GET https://www.ellevio.se/api/mypages/deliverysites/?customerId=...` (JSON, session
  cookies) lists the account's installations: `id` (anläggnings-ID), `address`,
  `isConsumptionSite`. Verified 2026-10-17. Used by the config flow.
  *(production sites not checked)*

### Not known

- No production/export endpoint seen. Cost not seen. *(unverified)*

## 3. Data semantics

| Aspect | Finding |
|---|---|
| Quantities | Consumption (`total`, `low`, `normal`, `high`, `reactive`). Only `total` is used. Production/export: not seen. |
| Resolution | Requested as `QuarterHourly` (15 min). `Hourly`/`Daily` also accepted by the query parameter. Reference uses 15 min for stored readings. |
| Units | Values treated as **kWh** per interval. Live daily sums 0.03–0.66 kWh, plausible. Not yet compared with the portal. |
| Interval vs cumulative | Per-interval deltas (`total` per 15 min). Not cumulative meter readings. |
| Timestamps | ISO 8601 with local offset, e.g. `2026-10-16T00:00:00+02:00`. Live: `+02:00` on all rows (CEST). Winter `+01:00` not yet observed. |
| DST | Fall-back repeated hour: how it is represented is *(unverified)*. Reference stores `dayLightSavingsTime` but never disambiguates. Integration converts each timestamp to UTC before bucketing, so the offset is authoritative. |
| Data lag | About 1 day. Live: data through yesterday present. Exact publication time *(unverified)*. |
| Preliminary / restated | Live: `status` = `OK` (settled days) or `Temporary` (most recent days). `Estimated` (reference example) not seen live. Reference overwrites rows on re-fetch. Whether the utility restates old values *(unverified)*. |
| Missing data | Reference: `status = "Missing"` with `total = 0` (placeholder). Live week (2026-10-10..16): 96 rows every day, none `Missing`. |

Rules the integration applies (derived from the above):

- Rows with `status == "Missing"` or `total` null are treated as no data.
- A UTC hour is imported only if all of its sub-hour readings are present and usable.
  Partial hours are dropped, never zero-filled.

## 4. Identifiers

- **Customer ID** (`customerId`): Mina sidor account. Used as the config entry unique ID.
- **Anläggnings-ID** (`anlage_id`, 18-character string, e.g. `735999...`): one per
  metering point / installation. Used in the URL path. Treated as a stable opaque
  string (reference: `str(item).strip()`).
- Display names: reference uses a user-supplied `[installation_names]` table. Not
  from the API.

## 5. Limits and etiquette

- **Sub-daily range limit:** HTTP 500 on too-long `QuarterHourly` ranges.
  Reference: 7 days succeeds at `QuarterHourly`, 10 days fails. Integration chunks
  sub-daily requests into 7-day windows (`ponytail:` in `coordinator.py`, empirical).
- **Rate limit:** none observed. HTTP 429 never observed; mapped to `RateLimited`
  defensively.
- **Pagination:** none; one response holds the whole range.
- **Polling:** reference polls on a schedule. Integration default 3 h. Data is
  daily-granular, so more frequent polling is wasteful.
- **Errors observed:**
  - HTTP 401/403: session dead.
  - HTTP 200 `text/html`: session dead.
  - HTTP 500: range too long for sub-daily resolution.
  - `<div id="loginerror">` text on bad credentials.

## 6. Quirks and reference workarounds

1. **Login is HTML scraping.** Form field names, form `action`, and OIDC hidden fields
   are parsed from HTML. Any change to the Mina sidor login page breaks it. Reference
   failure: `RuntimeError("Could not find Ellevio login form...")`.
2. **Auth failure is signalled in HTML, not status code.** Check `Content-Type`
   is JSON; do not trust HTTP 200.
3. **`maxPower` / `debMaxPower` may be an object** with `status: "Missing"` instead
   of a number. Not used by the integration.
4. **Sub-daily range limit** (see §5).
5. **Placeholder rows.** Unpublished quarter-hours come back `Missing` with zero.
   Reference writes them and treats the day as missing until re-fetch replaces them.
   Integration never writes them to statistics.
6. **Timezone.** Reference compares dates by slicing the first 10 characters of
   `start` (local date). Integration converts to UTC and buckets by UTC hour,
   which is correct across DST.

## 7. Live verification (2026-10-17)

Run against a real account through the integration's own client and a local HA 2026.10.1.

- **Login:** form login succeeded with the account email and password.
- **Consumption:** `QuarterHourly`, `Daily` interval, returns 96 rows/day, 15-minute rows, `+02:00` offsets.
- **Status:** `OK` and `Temporary` observed. Not `Estimated`.
- **Recorder:** two statistics, `ellevio_energy:consumption_<anlage_id>`, unit `kWh`, unit class `energy`, `has_sum: true`. About 1127 hourly rows from 2026-08-31 to 2026-10-17 local. No gaps, no duplicate starts, `sum` never decreases.
- **Sensors:** `sensor.last_imported_hour` (and `_2`) report `2026-10-16T21:00:00+00:00`.

Still open: portal comparison of values, DST (fall-back) behaviour, winter offset.

