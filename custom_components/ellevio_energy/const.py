"""Constants."""

from datetime import timedelta

DOMAIN = "ellevio_energy"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_CUSTOMER_ID = "customer_id"
CONF_ANLAGE_IDS = "anlage_ids"

OPT_BACKFILL_DAYS = "backfill_days"
OPT_REFETCH_DAYS = "refetch_days"
OPT_POLL_HOURS = "poll_hours"
OPT_NAMES = "names"  # {anlage_id: display name}
OPT_SUPPLIER_PRICE = (
    "supplier_price_per_kwh"  # SEK/kWh energy price from supplier; 0 = grid fees only
)

DEFAULT_BACKFILL_DAYS = 365
DEFAULT_REFETCH_DAYS = 3
DEFAULT_POLL_HOURS = 3

# Ellevio rejects sub-daily ranges longer than 7 days (HTTP 500).
# ponytail: empirical limit from the reference project; revisit if API changes.
MAX_SUBDAILY_DAYS = 7

RESOLUTION = "QuarterHourly"
INTERVAL = "Daily"

STATISTIC_SOURCE = DOMAIN
# Ellevio fees and tax are in SEK. Fixed, not HA currency: HA may be set to another
# currency, which would relabel kronor as euros.
COST_UNIT = "SEK"
POLL_INTERVAL = timedelta(hours=DEFAULT_POLL_HOURS)
