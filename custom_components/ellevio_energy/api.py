"""Async Ellevio Mina sidor client. See docs/API_NOTES.md."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urljoin

import aiohttp

BASE_URL = "https://www.ellevio.se"
CONSUMPTION_URL = BASE_URL + "/api/mypages/energy/consumption/{anlage_id}"
AGREEMENT_URL = BASE_URL + "/api/mypages/myagreementsprices/agreementdetails/{anlage_id}"
DELIVERY_SITES_URL = BASE_URL + "/api/mypages/deliverysites/"
LOGIN_URL = BASE_URL + "/privat/mina-sidor/Login/"
LOGIN_RETURN_URL = BASE_URL + "/privat/mina-sidor/oversikt/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class EllevioError(Exception):
    """Base error."""


class AuthError(EllevioError):
    """Credentials rejected or session expired."""


class RateLimitedError(EllevioError):
    """HTTP 429."""


class ApiError(EllevioError):
    """Any other API failure."""


@dataclass(frozen=True)
class GridPrices:
    """Ellevio network prices for one installation, as shown on Mina sidor."""

    fixed_per_month: float  # SEK per month
    transfer_per_kwh: float  # SEK per kWh
    tax_per_kwh: float  # SEK per kWh


_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _amount(value: str) -> float:
    """Parse '450,00 kr/mån' or '26,00 öre/kWh' into the base unit (SEK or SEK/kWh)."""
    match = _NUMBER_RE.search(value)
    if not match:
        raise ApiError(f"No amount in price value: {value!r}")
    number = float(match.group(1).replace(",", "."))
    return number / 100 if "öre" in value.lower() else number


@dataclass(frozen=True)
class Consumption:
    start: str
    end: str
    total: float | None
    status: str | None


@dataclass(frozen=True)
class DeliverySite:
    """Installation on the Mina sidor account (anläggning)."""

    anlage_id: str
    address: str


_FORM_RE = re.compile(r"<form[^>]*>.*?</form>", re.S | re.I)


def _find_form(page: str, marker: str) -> str | None:
    for match in _FORM_RE.finditer(page):
        if marker in match.group(0):
            return match.group(0)
    return None


def _attr(tag: str, name: str) -> str | None:
    match = re.search(rf'{name}="([^"]*)"', tag)
    return html.unescape(match.group(1)) if match else None


def _login_error(page: str) -> str | None:
    match = re.search(r'<div id="loginerror">(.*?)</div>', page, re.S)
    if not match:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
    return re.sub(r"\s+", " ", text).strip() or None


class EllevioClient:
    """Stateful client holding the Mina sidor session cookies in the aiohttp jar."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_login(self, email: str, password: str) -> None:
        """Log in via the form + OIDC callback. Raises AuthError on rejection."""
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"}
        async with self._session.get(
            LOGIN_URL,
            params={"LoginMethods": "9", "loginTemplate": "4", "returnUrl": LOGIN_RETURN_URL},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            page = await resp.text()
            page_url = str(resp.url)

        form = _find_form(page, 'name="username"')
        if form is None:
            raise ApiError("Login form not found; the login page may have changed")
        action = _attr(form[: form.index('name="username"')], "action")
        if not action:
            raise ApiError("Login form action not found")
        method_match = re.search(r'name="method"[^>]*value="([^"]*)"', form)
        method = method_match.group(1) if method_match else "password.2"

        async with self._session.post(
            urljoin(page_url, html.unescape(action)),
            data={"username": email, "password": password, "method": method},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            result = await resp.text()
            result_url = str(resp.url)

        if "/uas/authn/" not in result_url:
            return

        if detail := _login_error(result):
            raise AuthError(f"Ellevio rejected login: {detail}")

        oidc = _find_form(result, 'id="form"')
        if oidc is None:
            raise ApiError("Unexpected login response; the login flow may have changed")
        action = _attr(oidc, "action")
        if not action:
            raise ApiError("OIDC form action not found")
        fields = {
            name: html.unescape(value)
            for name, value in re.findall(
                r'<input[^>]*type="hidden"[^>]*name="([^"]*)"[^>]*value="([^"]*)"', oidc
            )
        }
        method_attr = (_attr(oidc, "method") or "GET").upper()
        if method_attr == "POST":
            async with self._session.post(
                html.unescape(action), data=fields, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp.raise_for_status()
        else:
            async with self._session.get(
                html.unescape(action), params=fields, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp.raise_for_status()

    async def async_get_grid_prices(self, *, anlage_id: str, customer_id: str) -> GridPrices:
        """Network fees (fixed monthly, transfer and tax per kWh) for one installation."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
            "Referer": BASE_URL + "/privat/mina-sidor/avtal-och-priser/",
        }
        async with self._session.get(
            AGREEMENT_URL.format(anlage_id=anlage_id),
            params={"customerId": customer_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status in (401, 403):
                raise AuthError(f"Authentication failed (HTTP {resp.status})")
            if resp.status >= 400:
                raise ApiError(f"HTTP {resp.status}")
            if "json" not in resp.headers.get("content-type", "").lower():
                raise AuthError("Ellevio did not return JSON (session expired)")
            payload: dict[str, Any] = await resp.json()

        info = (payload.get("data", {}).get("priceInformation") or {}).get("information") or []
        found: dict[str, float] = {}
        for item in info:
            label = item.get("label")
            value = item.get("value")
            if label and value:
                found[label] = _amount(value)
        try:
            return GridPrices(
                fixed_per_month=found["Fast avgift"],
                transfer_per_kwh=found["Överföringsavgift"],
                tax_per_kwh=found["Statlig energiskatt"],
            )
        except KeyError as err:
            raise ApiError(f"Grid price field missing: {err}") from err

    async def async_get_consumption(
        self,
        *,
        anlage_id: str,
        customer_id: str,
        start: date,
        end: date,
        resolution: str = "QuarterHourly",
        interval: str = "Daily",
    ) -> list[Consumption]:
        """Fetch readings for one installation. Raises AuthError on expired session."""
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "resolution": resolution,
            "interval": interval,
            "customerId": customer_id,
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
            "Referer": BASE_URL + "/",
        }
        async with self._session.get(
            CONSUMPTION_URL.format(anlage_id=anlage_id),
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status in (401, 403):
                raise AuthError(f"Authentication failed (HTTP {resp.status})")
            if resp.status == 429:
                raise RateLimitedError("Ellevio rate limit reached")
            if resp.status >= 400:
                raise ApiError(f"HTTP {resp.status}")
            if "json" not in resp.headers.get("content-type", "").lower():
                # Expired sessions return HTTP 200 with an HTML login page.
                raise AuthError("Ellevio did not return JSON (session expired)")
            payload: dict[str, Any] = await resp.json()

        rows = payload.get("data", {}).get("consumptions")
        if not isinstance(rows, list):
            raise ApiError("Unexpected response shape: data.consumptions missing")
        return [
            Consumption(
                start=row["start"],
                end=row["end"],
                total=row.get("total"),
                status=row.get("status"),
            )
            for row in rows
        ]

    async def async_get_delivery_sites(self, *, customer_id: str) -> list[DeliverySite]:
        """Consumption installations on the account. Needs a session from async_login()."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
            "Referer": BASE_URL + "/privat/mina-sidor/elanlaggning/",
        }
        async with self._session.get(
            DELIVERY_SITES_URL,
            params={"customerId": customer_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status in (401, 403):
                raise AuthError(f"Authentication failed (HTTP {resp.status})")
            if resp.status >= 400:
                raise ApiError(f"HTTP {resp.status}")
            if "json" not in resp.headers.get("content-type", "").lower():
                raise AuthError("Ellevio did not return JSON (session expired)")
            payload: dict[str, Any] = await resp.json()

        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ApiError("Unexpected response shape: data missing")
        return [
            DeliverySite(anlage_id=str(row["id"]), address=(row.get("address") or "").strip())
            for row in rows
            if row.get("isConsumptionSite")
        ]
