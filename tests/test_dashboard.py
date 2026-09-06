"""services/dashboard — status-file reading and the screenshot synthesis path.

The Docker socket is never touched: `_docker_api` is stubbed, so `_docker_ps_all`
sees an empty container list exactly as it would on a host with no daemon.
"""
import json
from datetime import datetime, timezone

import pytest

from services.dashboard import dashboard_server as dash


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    status, shots, processed = (tmp_path / "status", tmp_path / "screenshots",
                                tmp_path / "processed")
    for d in (status, shots, processed):
        d.mkdir()
    monkeypatch.setattr(dash, "STATUS_DIR", status)
    monkeypatch.setattr(dash, "SCREENSHOTS_DIR", shots)
    monkeypatch.setattr(dash, "PROCESSED_DIR", processed)
    return status, shots, processed


@pytest.fixture
def no_docker(monkeypatch):
    """Stub the Docker socket: every container query comes back empty."""
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path))
        return 200, []

    monkeypatch.setattr(dash, "_docker_api", fake_api)
    return calls


@pytest.fixture
def client(dirs, no_docker):
    dash.app.config["TESTING"] = True
    return dash.app.test_client()


# ---- _read_status -----------------------------------------------------------

def test_read_status_parses_a_status_file(dirs):
    status_dir, _, _ = dirs
    payload = {"status": "running", "total_captured": 12}
    (status_dir / "screenshot_status.json").write_text(json.dumps(payload))
    assert dash._read_status("screenshot_status.json") == payload


def test_read_status_returns_none_when_absent(dirs):
    assert dash._read_status("nope.json") is None


def test_read_status_returns_none_on_malformed_json(dirs):
    status_dir, _, _ = dirs
    (status_dir / "weather_status.json").write_text('{"status": "run')   # truncated
    assert dash._read_status("weather_status.json") is None


def test_read_status_returns_none_on_empty_file(dirs):
    status_dir, _, _ = dirs
    (status_dir / "weather_status.json").write_text("")
    assert dash._read_status("weather_status.json") is None


# ---- screenshot synthesis ---------------------------------------------------

def test_no_docker_and_no_status_file_reports_unknown(client, dirs):
    body = client.get("/api/status").get_json()
    ss = body["services"]["screenshot-service"]
    assert ss["status_data"]["_synthetic"] is True
    assert ss["status_data"]["status"] == "unknown"
    assert ss["docker_state"] == "not_found"


def test_synthesis_counts_pngs_on_disk(client, dirs):
    _, shots, _ = dirs
    for n in range(3):
        (shots / f"radar_base_reflectivity_2026052718000{n}_UTC.png").write_bytes(b"\x89PNG")

    ss = client.get("/api/status").get_json()["services"]["screenshot-service"]
    assert ss["status_data"]["total_captured"] == 3
    assert ss["status_data"]["last_capture_file"].startswith("radar_base_reflectivity_")
    assert ss["status_data"]["last_capture_time"]


def test_processor_progress_implies_the_screenshot_service_is_producing(client, dirs):
    """No screenshot status file and no docker — but the processor has consumed
    images, so the dashboard infers the capture side is alive."""
    status_dir, _, _ = dirs
    (status_dir / "processor_status.json").write_text(json.dumps({
        "status": "running",
        "total_processed": 42,
        "last_processed": "radar_base_reflectivity_20260527_180000_UTC.png",
        "last_processed_time": "2026-05-27T18:05:00Z",
    }))

    ss = client.get("/api/status").get_json()["services"]["screenshot-service"]
    assert ss["status_data"]["status"] == "running"
    assert ss["docker_state"] == "running"
    assert ss["status_data"]["total_captured"] == 42
    assert ss["status_data"]["last_capture_file"].endswith("_UTC.png")
    assert ss["status_data"]["last_capture_time"] == "2026-05-27T18:05:00Z"


def test_a_real_status_file_is_not_synthesised(client, dirs):
    status_dir, _, _ = dirs
    (status_dir / "screenshot_status.json").write_text(json.dumps({
        "status": "running", "total_captured": 7, "total_errors": 1,
    }))

    ss = client.get("/api/status").get_json()["services"]["screenshot-service"]
    assert "_synthetic" not in ss["status_data"]
    assert ss["status_data"]["total_captured"] == 7
    # A status file saying "running" is trusted when docker can't see the container.
    assert ss["docker_state"] == "running"


def test_status_payload_lists_every_service(client, dirs):
    body = client.get("/api/status").get_json()
    assert set(body["services"]) == set(dash.SERVICES)
    assert body["folders"]["screenshots"] == {"count": 0, "size_mb": 0}
    assert body["latest_screenshot"] is None
    assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None


def test_inference_and_alerting_status_flow_through(client, dirs):
    status_dir, _, _ = dirs
    (status_dir / "inference_status.json").write_text(json.dumps(
        {"status": "running", "last_score": 0.42, "preprocess_version": "1.0"}))
    (status_dir / "alerting_status.json").write_text(json.dumps(
        {"status": "running", "active_warnings": 0, "emails_sent_total": 3}))

    services = client.get("/api/status").get_json()["services"]
    assert services["inference-service"]["status_data"]["last_score"] == 0.42
    assert services["alerting-service"]["status_data"]["emails_sent_total"] == 3
