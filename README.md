# Ellevio Energy

Imports your [Ellevio](https://www.ellevio.se) hourly electricity consumption into
Home Assistant's **Energy dashboard** as long-term statistics.

> **Lag caveat:** Ellevio publishes readings about **one day late**. Data for
> yesterday typically appears the next day. This is expected. The integration
> writes each reading at its real hour, so the Energy dashboard shows the correct
> historical day, not the time the poll ran.

Targets **Home Assistant 2026.10.1** (see `docs/DECISIONS.md`). Requires the
`recorder` integration (enabled by default).

## Install via HACS

1. HACS → Integrations → ⋮ → **Custom repositories** → add this repo, category *Integration*.
2. Install **Ellevio Energy**, restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Ellevio Energy*.

## Configure

Enter:

- **Email / password**: your Mina sidor login.
- **Customer ID**: your Ellevio customer number.
- **Installations**: picked from your account in a second step. Add or remove them
  later via Reconfigure.

Options (Settings → Devices & services → Ellevio Energy → Configure):

| Option | Default | Meaning |
|---|---|---|
| Backfill days | 365 | How far back the first import reaches. |
| Re-fetch window | 3 | Days re-imported each run, so utility corrections propagate. |
| Poll interval | 3 h | How often Ellevio is polled. Data is daily, so more is wasteful. |
| Supplier energy price | 0 | SEK/kWh from your electricity supplier. Ellevio does not provide it. 0 = grid fees only. |

### Names
Each installation gets a name, default `Ellevio <last 4 digits>`. Rename it in
**Settings → Devices & services → Ellevio Energy → Configure → Rename an installation**.
The name sets the statistic name (shown in the Energy dashboard) and the device name.
Entity IDs keep their original form after a rename.

Each installation has one diagnostic sensor, **Last imported hour**, with the latest
UTC hour written to statistics.

When the Ellevio session expires, Home Assistant shows a **reauth** notification.
Re-enter your password there.

## Add to the Energy dashboard

Settings → Dashboards → Energy → **Grid consumption** → *Add consumption* →
select the statistic `<name> consumption` (default `Ellevio <last 4 digits> consumption`).

Imported statistics are hourly kWh. Historical hours appear at their real times.

## Services

### `ellevio_energy.reimport`

Re-fetch and overwrite statistics for a date range. Use it to repair gaps or
apply utility corrections.

```yaml
service: ellevio_energy.reimport
data:
  start_date: "2026-10-01"
  end_date: "2026-10-16"   # optional, defaults to today
```

Re-importing is idempotent: rows with the same hour are overwritten, and later
running totals are rebuilt from the corrected value.

## Troubleshooting

- **Statistic missing in Developer tools → Statistics**: check the log for
  `ellevio_energy`. The first import may take a moment for large backfills.
- **"Ellevio rejected login"**: the password changed. Use the reauth notification.
- **"No stored sum before ..."**: an earlier hour is missing from statistics. Run
  `ellevio_energy.reimport` starting from the first day you have, which rebuilds
  the running total.
- **Values look wrong vs the portal**: check the unit. The integration assumes the
  API returns kWh. This is unverified for real data. Compare one day in the portal
  against the Statistics graph.

## Reset / delete imported statistics

Imported statistics live in the recorder. To remove them:

1. Remove the integration (Settings → Devices & services → Ellevio Energy → Delete).
2. Developer tools → Statistics → select `ellevio_energy:consumption_*` and delete
   the rows, or clear the recorder database to start from scratch.

## Development

```sh
docker compose up -d        # HA 2026.10.1 on http://localhost:8123 with the integration mounted
uv run ruff check custom_components tests
uv run --with mypy --with probatio mypy custom_components/ellevio_energy tests
uv run --with pytest-homeassistant-custom-component==0.13.372 --with pytest-asyncio pytest tests
```

Git hooks run through [hk](https://hk.jdx.dev) (`hk.pkl`).

### Known gaps

- No diagnostic sensors. Statistics carry the data; the spec's optional sensors
  were not built.
- No production/export statistics. The API was not observed to return them.
- Cost statistic `ellevio_energy:cost_<installation_id>` (unit = your HA currency) =
  kWh × (transfer fee + energy tax + supplier price) + fixed monthly fee spread over
  the hours of its month. Grid fees are read from Mina sidor. The supplier energy price
  is yours to set. Spreading the fixed fee uses only the hours in the imported window,
  so cost totals match the bill only when a whole month is imported.
- 7-day chunking for 15-minute data is an empirical limit from the reference
  project (`ponytail:` in `const.py`).
- Live Ellevio behaviour (login flow, units, `Estimated` status) is unverified
  against real account. See `docs/API_NOTES.md`.
- HA UI onboarding (config flow through a real token) not yet walked end to end.
- hassfest and HACS validation workflows not yet run on GitHub.

## Layout

```
custom_components/ellevio_energy/   the integration
  api.py          async Ellevio client (login + consumption endpoint)
  statistics.py   pure aggregation and running-sum logic
  coordinator.py  fetch -> aggregate -> import
  config_flow.py  setup, reauth, reconfigure, options
docs/API_NOTES.md   reverse-engineered API notes
docs/DECISIONS.md   design decisions
tests/              pytest (pure + HA-backed)
```

## License

MIT. See `LICENSE`.
