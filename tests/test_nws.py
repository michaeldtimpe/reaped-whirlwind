"""common.nws.fetch_active_alerts and the two callers that replaced their own
fetchers with it. Offline: `requests.get` is always mocked."""
import pytest
import requests

from common import nws


class FakeResponse:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc is not None:
            raise self._exc

    def json(self):
        return self._payload


class Recorder:
    """Stands in for requests.get / session.get."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def last(self):
        return self.calls[-1]


FEATURES = [{"properties": {"event": "Tornado Warning", "id": "urn:oid:1"}}]


@pytest.fixture
def get(monkeypatch):
    rec = Recorder(FakeResponse({"features": FEATURES}))
    monkeypatch.setattr(nws.requests, "get", rec)
    return rec


# ---- request shape ----------------------------------------------------------

def test_builds_the_alerts_active_url_with_an_unescaped_comma(get):
    nws.fetch_active_alerts(nws.KFWS_LAT, nws.KFWS_LON)
    url, kwargs = get.last
    assert url == "https://api.weather.gov/alerts/active?point=32.5728,-97.3031"
    # The point rides in the URL, exactly as the hand-built URLs it replaced did.
    assert "params" not in kwargs


def test_sends_the_user_agent_and_geojson_accept_headers(get):
    nws.fetch_active_alerts(32.0, -97.0)
    _, kwargs = get.last
    assert kwargs["headers"] == {
        "User-Agent": nws.NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }
    assert kwargs["timeout"] == nws.DEFAULT_TIMEOUT


def test_user_agent_and_timeout_are_overridable(get):
    nws.fetch_active_alerts(32.0, -97.0, user_agent="(rw test, me@example.com)", timeout=3)
    _, kwargs = get.last
    assert kwargs["headers"]["User-Agent"] == "(rw test, me@example.com)"
    assert kwargs["timeout"] == 3


def test_a_session_is_used_when_supplied(monkeypatch):
    module_get = Recorder(FakeResponse({"features": []}))
    monkeypatch.setattr(nws.requests, "get", module_get)
    session_get = Recorder(FakeResponse({"features": FEATURES}))
    session = type("S", (), {"get": session_get})()

    assert nws.fetch_active_alerts(32.0, -97.0, session=session) == FEATURES
    assert module_get.calls == []


# ---- return value -----------------------------------------------------------

def test_returns_the_features_list(get):
    assert nws.fetch_active_alerts(32.0, -97.0) == FEATURES


@pytest.mark.parametrize("payload", [{}, {"features": None}, {"features": []}, None])
def test_missing_or_empty_features_becomes_an_empty_list(monkeypatch, payload):
    monkeypatch.setattr(nws.requests, "get", Recorder(FakeResponse(payload)))
    assert nws.fetch_active_alerts(32.0, -97.0) == []


# ---- error propagation ------------------------------------------------------

def test_http_error_propagates_to_the_caller(monkeypatch):
    monkeypatch.setattr(nws.requests, "get",
                        Recorder(FakeResponse(exc=requests.HTTPError("500 Server Error"))))
    with pytest.raises(requests.HTTPError):
        nws.fetch_active_alerts(32.0, -97.0)


def test_connection_error_propagates_to_the_caller(monkeypatch):
    monkeypatch.setattr(nws.requests, "get", Recorder(requests.ConnectionError("no route")))
    with pytest.raises(requests.ConnectionError):
        nws.fetch_active_alerts(32.0, -97.0)


# ---- caller semantics -------------------------------------------------------
# The two fetchers this replaced had DIFFERENT error contracts. Both are preserved.

def test_alerting_fetcher_still_raises(monkeypatch):
    """services/alerting: run_cycle catches, records a 'nws' error, sets status
    'nws_error'. So the fetcher must keep raising."""
    from services.alerting import alert_service

    monkeypatch.setattr(nws.requests, "get",
                        Recorder(FakeResponse(exc=requests.HTTPError("503"))))
    with pytest.raises(requests.HTTPError):
        alert_service.fetch_nws_alerts()


def test_alerting_fetcher_returns_features(monkeypatch):
    from services.alerting import alert_service

    monkeypatch.setattr(nws.requests, "get", Recorder(FakeResponse({"features": FEATURES})))
    assert alert_service.fetch_nws_alerts() == FEATURES


def test_weather_fetcher_swallows_errors_and_returns_none(monkeypatch, capsys):
    """services/weather: Fetcher._get printed 'API error: ...' and returned None;
    alerts() must behave identically or the report loop changes shape."""
    from services.weather import weather_report_generator as wrg

    monkeypatch.setattr(nws.requests, "get", Recorder(requests.ConnectionError("no route")))
    assert wrg.Fetcher(wrg.Config()).alerts() is None
    assert "API error:" in capsys.readouterr().out


def test_weather_fetcher_returns_a_feature_collection(monkeypatch):
    """format_alerts() reads data["features"], so alerts() must keep that shape."""
    from services.weather import weather_report_generator as wrg

    monkeypatch.setattr(nws.requests, "get", Recorder(FakeResponse({"features": FEATURES})))
    data = wrg.Fetcher(wrg.Config()).alerts()
    assert data == {"features": FEATURES}
    assert "Tornado Warning" in wrg.format_alerts(data)
