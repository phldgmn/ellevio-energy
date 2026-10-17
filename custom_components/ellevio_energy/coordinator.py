"""Fetch Ellevio readings and import them as external hourly statistics."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import (
    get_instance,  # type: ignore[attr-defined]  # HA re-exports without __all__
)
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, EllevioClient, EllevioError
from .const import (
    CONF_ANLAGE_IDS,
    CONF_CUSTOMER_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    COST_UNIT,
    DOMAIN,
    INTERVAL,
    MAX_SUBDAILY_DAYS,
    OPT_BACKFILL_DAYS,
    OPT_NAMES,
    OPT_POLL_HOURS,
    OPT_REFETCH_DAYS,
    OPT_SUPPLIER_PRICE,
    RESOLUTION,
    STATISTIC_SOURCE,
)
from .statistics import (
    HourlyRow,
    Reading,
    aggregate_hourly,
    build_rows,
    cost_rows,
    hour_cost,
    window_start,
)

_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    """Current UTC time. Single seam so tests can pin the clock."""
    return datetime.now(UTC)


def statistic_id(anlage_id: str) -> str:
    """Statistic ID must be [a-z0-9_] after the domain colon."""
    return f"{DOMAIN}:consumption_{anlage_id.lower()}"


def display_name(entry: ConfigEntry, anlage_id: str) -> str:
    """User-set alias for an installation, else `Ellevio <last 4 digits>`."""
    names: dict[str, str] = entry.options.get(OPT_NAMES, {})
    return names.get(anlage_id) or f"Ellevio {anlage_id[-4:]}"


def cost_statistic_id(anlage_id: str) -> str:
    """Cost statistic ID; same rule as consumption ID."""
    return f"{DOMAIN}:cost_{anlage_id.lower()}"


def _local_month(start: datetime) -> tuple[int, int]:
    local = start.astimezone(ZoneInfo("Europe/Stockholm"))
    return local.year, local.month


def _hours_per_local_month(rows: list[HourlyRow]) -> dict[tuple[int, int], int]:
    """Hours per local month in the imported window; drives the fixed-fee spread.

    Known limit: a window that covers only part of a month spreads the fee over
    those hours, not the whole month. Re-import of full months keeps totals exact.
    """
    counts: dict[tuple[int, int], int] = {}
    for row in rows:
        key = _local_month(row.start)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _cost_metadata(anlage_id: str, name: str) -> StatisticMetaData:
    return StatisticMetaData(
        source=STATISTIC_SOURCE,
        statistic_id=cost_statistic_id(anlage_id),
        name=f"{name} cost",
        unit_of_measurement=COST_UNIT,
        unit_class=None,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
    )


def _metadata(anlage_id: str, name: str) -> StatisticMetaData:
    return StatisticMetaData(
        source=STATISTIC_SOURCE,
        statistic_id=statistic_id(anlage_id),
        name=f"{name} consumption",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        unit_class="energy",
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
    )


class EllevioCoordinator(DataUpdateCoordinator[None]):
    """Polls Ellevio and imports hourly consumption into the recorder."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=entry.options[OPT_POLL_HOURS]),
        )
        self.client = EllevioClient(async_create_clientsession(hass))
        # Latest UTC hour written to statistics this session; None until first import.
        self.last_imported: datetime | None = None

    async def _async_update_data(self) -> None:
        await self.async_import(window=None)

    async def async_import(self, window: tuple[date, date] | None) -> None:
        """Import all installations. `window` = explicit (start, end), else auto."""
        entry = self.config_entry
        try:
            await self.client.async_login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EllevioError as err:
            raise UpdateFailed(f"Login failed: {err}") from err

        for anlage_id in entry.data[CONF_ANLAGE_IDS]:
            try:
                await self._import_installation(anlage_id, window)
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except EllevioError as err:
                raise UpdateFailed(f"{anlage_id}: {err}") from err

    async def _import_installation(self, anlage_id: str, window: tuple[date, date] | None) -> None:
        entry = self.config_entry
        name = display_name(entry, anlage_id)
        meta = _metadata(anlage_id, name)
        stat_id = meta["statistic_id"]
        recorder = get_instance(self.hass)

        last_rows = (
            await recorder.async_add_executor_job(
                get_last_statistics, self.hass, 1, stat_id, False, {"sum"}
            )
        ).get(stat_id)
        last_hour: datetime | None = None
        if last_rows:
            last_hour = datetime.fromtimestamp(float(last_rows[0]["start"]), UTC)

        now = _now()
        if window is not None:
            start, end = window
            start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        else:
            backfill_start = now - timedelta(days=entry.options[OPT_BACKFILL_DAYS])
            start_dt = window_start(last_hour, backfill_start, entry.options[OPT_REFETCH_DAYS])
            start = start_dt.date()
            end = (now - timedelta(days=1)).date()  # data lags ~1 day
        if start > end:
            return

        readings: list[Reading] = []
        for chunk_start, chunk_end in _chunks(start, end):
            consumptions = await self.client.async_get_consumption(
                anlage_id=anlage_id,
                customer_id=entry.data[CONF_CUSTOMER_ID],
                start=chunk_start,
                end=chunk_end,
                resolution=RESOLUTION,
                interval=INTERVAL,
            )
            readings.extend(Reading(_parse(c.start), c.total, c.status) for c in consumptions)

        hourly = aggregate_hourly(readings)
        if not hourly:
            return

        # Continuity: the running sum continues from the stored sum of the hour
        # before the first hour we (re)import. Nothing stored yet -> start at 0.
        base = await recorder.async_add_executor_job(_sum_before, self.hass, stat_id, min(hourly))
        base_sum = 0.0 if base is None else base

        kwh_rows = build_rows(hourly, base_sum=base_sum)
        stats = [StatisticData(start=r.start, state=r.state, sum=r.sum) for r in kwh_rows]
        async_add_external_statistics(self.hass, meta, stats)
        self.last_imported = max(row["start"] for row in stats)

        await self._import_cost(anlage_id, name, kwh_rows, recorder)

    async def _import_cost(
        self,
        anlage_id: str,
        name: str,
        kwh_rows: list[HourlyRow],
        recorder: Any,
    ) -> None:
        """Cost statistic: grid fees from Mina sidor plus supplier energy price from options."""
        entry = self.config_entry
        supplier = float(entry.options.get(OPT_SUPPLIER_PRICE, 0) or 0)
        if supplier < 0:
            raise UpdateFailed("Supplier price per kWh must not be negative")
        try:
            grid = await self.client.async_get_grid_prices(
                anlage_id=anlage_id, customer_id=entry.data[CONF_CUSTOMER_ID]
            )
        except EllevioError as err:
            # Cost is secondary; kWh import already written. Retry next run.
            _LOGGER.warning("Grid prices unavailable for %s, cost skipped: %s", anlage_id, err)
            return

        per_kwh = grid.transfer_per_kwh + grid.tax_per_kwh + supplier
        hours_per_month = _hours_per_local_month(kwh_rows)
        costs = [
            hour_cost(
                r.state,
                per_kwh,
                grid.fixed_per_month,
                hours_per_month[_local_month(r.start)],
            )
            for r in kwh_rows
        ]
        cost_id = cost_statistic_id(anlage_id)
        base = await recorder.async_add_executor_job(
            _sum_before, self.hass, cost_id, kwh_rows[0].start
        )
        rows = cost_rows(kwh_rows, costs, base_sum=0.0 if base is None else base)
        async_add_external_statistics(
            self.hass,
            _cost_metadata(anlage_id, name),
            [StatisticData(start=r.start, state=r.state, sum=r.sum) for r in rows],
        )


def _chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=MAX_SUBDAILY_DAYS - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sum_before(hass: HomeAssistant, stat_id: str, hour: datetime) -> float | None:
    """Running sum stored for the hour just before `hour`. Runs in recorder executor.

    Returns None when nothing is stored before `hour` at all (first import), so the
    series starts at 0. Raises when earlier rows exist but the predecessor is missing:
    writing a sum without its predecessor would silently corrupt every later total.
    """
    earlier: dict[str, list[Any]] = statistics_during_period(
        hass,
        datetime(1970, 1, 1, tzinfo=UTC),
        hour,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    earlier_rows = earlier.get(stat_id, [])
    if not earlier_rows:
        return None
    if earlier_rows[-1].get("sum") is None:
        raise UpdateFailed(
            f"No stored sum before {hour.isoformat()}; refusing to import a broken running total"
        )
    return float(earlier_rows[-1]["sum"])
