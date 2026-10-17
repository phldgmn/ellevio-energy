# Decisions

## Target versions
- Home Assistant **2026.10.1**. Test harness pinned to `pytest-homeassistant-custom-component==0.13.372`.
  Recorder API was checked against the HA 2026.10.2 core source (the nearest tag
  available at the time of the check); it is assumed identical on 2026.10.1 and
  the test suite runs against 2026.10.1.
- Python 3.14 (matches sibling project).

## Statistics, not sensors
Ellevio data lags about one day. A `total_increasing` sensor would attribute
consumption to the poll time. The integration writes **external long-term
statistics** (`async_add_external_statistics`) with real hour timestamps.

## Recorder API
- `StatisticMetaData` requires `mean_type` (`StatisticMeanType.NONE`) and
  `unit_class` (`"energy"`). Omitting them triggers a deprecation report.
- `source` must equal the statistic ID domain (`ellevio_energy`).
- `get_last_statistics` and `statistics_during_period` are synchronous
  (`session_scope`). Call them via `get_instance(hass).async_add_executor_job`.
- Row `start` values are timezone-aware UTC datetimes at the top of the hour.

## Aggregation
- Pure functions in `statistics.py`, no `hass` import. Unit-testable.
- 15-minute rows are converted to UTC first, then bucketed by UTC hour.
- An hour is imported only when all four quarter-hours are present and not
  `Missing`. Partial hours are dropped, never zero-filled.

## Running sum continuity
- No prior statistic: backfill from the configured start, `sum` starts at 0.
- Prior statistic: re-fetch from `last_hour - refetch_days` so corrections propagate.
- Base sum = `sum` of the hour immediately before the re-fetch window, queried via
  `statistics_during_period`, not the last row. Same `start` overwrites, so
  re-import is idempotent.

## Units
- Values are treated as **kWh per quarter-hour** and summed to kWh per hour.
  *(unverified against the Ellevio portal; confirm on a real account.)*

## Simplifications (marked `ponytail:`)
- Sub-daily requests are chunked into 7-day windows. Empirical limit from the
  reference project (API returns HTTP 500 beyond it).
- No production/export statistic: not observed in the API.
- No diagnostic sensors: statistics carry the data.
