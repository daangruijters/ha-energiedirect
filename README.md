# Energiedirect for Home Assistant

Unofficial custom integration for [Energiedirect](https://www.energiedirect.nl).
Not affiliated with Energiedirect or Essent.

> [!NOTE]
> This integration uses the same undocumented web APIs as the Energiedirect website. They can
> change without notice, which may break the integration until it is updated.

When you add it you choose one of two tiers (you can add both):

| Tier | Login | What you get |
| --- | --- | --- |
| **Dynamic prices** | none | Energiedirect's public hourly electricity, gas and feed-in prices for today and tomorrow |
| **My Energiedirect account** | browser session cookies | your **contracted tariffs**, **usage and costs** and **budget bill (termijnbedrag)** from [Mijn Energiedirect](https://mijn.energiedirect.nl) |

## Dynamic prices

No setup beyond picking the tier. One device, *Energiedirect dynamic prices*, with these sensors
(EUR incl. VAT):

- dynamic electricity price (current hour), next hour, lowest / average / highest today
- dynamic gas price
- dynamic feed-in price (what you get per kWh returned)

The current-price sensors carry `today` and `tomorrow` attributes with every hourly price
(`start`, `end`, `price`), for chart cards such as apexcharts-card, plus the price's
components (market price, purchasing fee, energy tax). Tomorrow's prices appear in the afternoon.

## My Energiedirect account

Energiedirect's login page always requires a captcha, so the integration can't log in with a
password. Instead you log in once in your browser and paste that session's cookies; the
integration keeps the session alive by refreshing it every hour and stores the renewed cookies.
If the session ever expires, Home Assistant asks you to paste fresh cookies.

### Copying the session cookies

1. Open a **private/incognito window** and log in at <https://mijn.energiedirect.nl>.
2. Press F12, open the **Network** tab and reload the page.
3. Click any request to `mijn.energiedirect.nl/api/...` and under **Request Headers** copy the
   full value of **cookie**.
4. Close the private window **without logging out** (logging out ends the session).

Instead of the whole header you can also build it from **Application → Cookies**; only these are
needed: `access_token-0`, `id_token-0`, `refresh_token-0` and `user_id-0`, each with its
`-length` companion (value `1`), e.g.
`access_token-0=U2F...; access_token-length=1; id_token-0=U2F...; id_token-length=1; refresh_token-0=U2F...; refresh_token-length=1; user_id-0=...; user_id-length=1`.
For the test script you can put the four values in `.env` as `ACCESS_TOKEN`, `ID_TOKEN`,
`REFRESH_TOKEN` and `USER_ID` instead of `COOKIE`.

Use a separate login from your everyday browser session: if both keep refreshing the same
session, one may invalidate the other.

### What you get

Per supply address, a device with these sensors (prices are incl. VAT, as shown in the portal):

| Group | Sensors |
| --- | --- |
| Electricity tariffs | price normal / low rate (all-in), supply price normal / low, energy tax, feed-in compensation, fixed supply cost per day, network cost per day, energy tax reduction per day |
| Gas tariffs | price (all-in), supply price, energy tax, fixed supply cost per day, network cost per day |
| Usage | electricity consumption / return / cost and gas consumption / cost for the last available day and for this month so far, plus the date usage data runs until |
| Budget bill | monthly payment, expected yearly costs, expected annual settlement (positive = you pay, negative = you get money back), terms left, next payment date |

The tariff sensors' attributes list every price line from the portal, including future-dated ones.

#### Energy dashboard

Energiedirect publishes usage per hour, but one to two days late. Because ordinary sensors can
only record "now", the hourly data is imported as long-term statistics instead:

- `energiedirect:electricity_consumption_<address>` (kWh)
- `energiedirect:electricity_return_<address>` (kWh)
- `energiedirect:electricity_cost_<address>` (EUR)
- `energiedirect:gas_consumption_<address>` (m³)
- `energiedirect:gas_cost_<address>` (EUR)

Pick these in **Settings → Dashboards → Energy**. On first setup about 60 days of history is imported.
For live usage, use a P1 meter reader as your consumption source and these statistics for costs.

## Test locally first (no Home Assistant needed)

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python scripts/test_login.py
```

Put the copied cookie header in `.env` in the repo root as `COOKIE=...` (git ignores `.env`).
The script checks the session, prints the tariffs, usage summary and budget bill it found, and
writes the refreshed cookies back to `.env`. Cookies are never printed. `--dump dump` also saves the raw JSON
responses into `dump/` (personal data, ignored by git).

Unit tests: `.venv\Scripts\python -m pytest`.

## Install in Home Assistant

Copy `custom_components/energiedirect` into the `custom_components` folder of your Home Assistant
config, restart, then add **Energiedirect** under **Settings → Devices & services**.

To see what it does, add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.energiedirect: debug
```

## How it works

- Session: the portal keeps the Auth0 tokens server-side and gives the browser session cookies on
  `mijn.energiedirect.nl`. `/auth/refresh/` renews the access token; without a refresh the
  session lapses after about 10 hours.
- Data (refreshed every hour):
  - `/api/contracts-middleware/supplyaddresses/v3`: addresses, contracts
  - `/api/billing/prices/v1`: contracted tariffs per contract
  - `/api/energy-insights/consumption-data/v2`: hourly / daily usage and cost
  - `/api/eci/getBudgetBillInsightsV1`: budget bill and expected yearly costs
- Dynamic prices: `https://www.energiedirect.nl/api/public/dynamicpricing/dynamic-prices/v1`
  (public; fetched every hour, the current price is re-evaluated every quarter hour)

## License

MIT, see [LICENSE](LICENSE).
