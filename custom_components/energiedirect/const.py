"""Constants for the Energiedirect integration."""

from datetime import timedelta

DOMAIN = "energiedirect"
MANUFACTURER = "Energiedirect"

CONF_COOKIES = "cookies"

# The portal session lapses after ~10 hours without a refresh, so poll well within that.
UPDATE_INTERVAL = timedelta(hours=1)

ELECTRICITY = "electricity"
GAS = "gas"

# Two kinds of config entries: public dynamic prices (no login) and a Mijn Energiedirect account.
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_ACCOUNT = "account"
ENTRY_TYPE_PRICES = "dynamic_prices"
PRICES_UNIQUE_ID = "dynamic_prices"

# Prices are published once a day (tomorrow's in the afternoon); the current price is
# re-read from the cached data every quarter hour.
PRICES_UPDATE_INTERVAL = timedelta(hours=1)
PRICES_TIMEZONE = "Europe/Amsterdam"

ISSUE_TRACKER_URL = "https://github.com/daangruijters/ha-energiedirect/issues"
