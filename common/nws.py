"""NWS API constants + the single active-alerts fetcher.

Canonical home for values that were duplicated across the weather, inference and
alerting services (and their env/compose defaults):

  KFWS_LAT / KFWS_LON     KFWS WSR-88D site — the radar the model is trained on,
                          and the point used for the /alerts/active query.
  DEFAULT_ALLOWED_EVENTS  the eight severe-weather Warnings the alerting service
                          notifies on by default. `ALLOWED_EVENTS` in .env /
                          docker-compose.yml overrides it; this tuple is the
                          default of record.
  NWS_USER_AGENT          fallback User-Agent. NWS asks for a real contact; each
                          service still overrides via the NWS_UA env var.
"""
import requests

KFWS_LAT = 32.5728
KFWS_LON = -97.3031

NWS_API_BASE = "https://api.weather.gov"
ALERTS_ACTIVE_URL = f"{NWS_API_BASE}/alerts/active"

# The eight standard severe-weather Warnings most relevant in DFW. Each entry is
# the literal NWS `properties.event` string. Watches and advisories are excluded
# by default — opt in by extending ALLOWED_EVENTS in .env.
DEFAULT_ALLOWED_EVENTS = (
    "Tornado Warning",
    "Severe Thunderstorm Warning",
    "Flash Flood Warning",
    "Flood Warning",
    "High Wind Warning",
    "Winter Storm Warning",
    "Ice Storm Warning",
    "Extreme Wind Warning",
)

NWS_USER_AGENT = "(reaped-whirlwind, weather@example.com)"
DEFAULT_TIMEOUT = 10


def alerts_active_url(lat, lon) -> str:
    """The /alerts/active URL for a point. The comma is left unescaped, exactly
    as the hand-built URLs this replaced did."""
    return f"{ALERTS_ACTIVE_URL}?point={lat},{lon}"


def fetch_active_alerts(lat, lon, *, session=None, timeout=DEFAULT_TIMEOUT,
                        user_agent=NWS_USER_AGENT):
    """Active NWS alerts for a point, as the GeoJSON `features` list.

    Raises whatever `requests` raises (connection/timeout) and
    `requests.HTTPError` on a non-2xx — callers own the error accounting.
    Returns [] when the response carries no features.
    """
    get = session.get if session is not None else requests.get
    r = get(
        alerts_active_url(lat, lon),
        headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json() or {}
    return payload.get("features") or []
