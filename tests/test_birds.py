from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from trmnl_weekly_calendar import server
from trmnl_weekly_calendar.birds_data import load_snapshot, select_page
from trmnl_weekly_calendar.birds_render import render_bird_image
from trmnl_weekly_calendar.png_encode import encode_png_grayscale_4bit

SAMPLE = Path(__file__).resolve().parents[1] / "src" / "trmnl_weekly_calendar" / "birds_snapshot_sample.json"


def _snapshot():
    return load_snapshot(SAMPLE)


def test_selection_is_deterministic_and_strict():
    page = select_page(_snapshot(), date(2026, 10, 26))
    assert page.monthly.species == "House Finch"
    # Most recent, then nearest, then name: Ruby-crowned Kinglet (Oct 23),
    # not a random pick from the 7-day list.
    assert page.spotted is not None and page.spotted.species == "Ruby-crowned Kinglet"
    # Only Brown Creeper is inside the strict 14-day window; the
    # Oct 9 returns (Junco, Solitaire) are 17 days before Oct 26.
    assert page.back is not None and page.back.species == "Brown Creeper"
    assert page.absence_note is None


def test_no_seasonal_case_uses_absence_note():
    page = select_page(_snapshot(), date(2026, 10, 30))
    assert page.back is None
    assert page.absence_note is not None and "No seasonal arrivals" in page.absence_note


def test_render_is_byte_identical_for_same_input():
    page = select_page(_snapshot(), date(2026, 10, 26))
    first = encode_png_grayscale_4bit(render_bird_image(page))
    second = encode_png_grayscale_4bit(render_bird_image(page))
    assert first == second
    image = render_bird_image(page)
    assert image.size == (1872, 1404)
    assert set(image.getdata()) <= set(range(0, 256, 17))


def test_changed_snapshot_changes_fingerprint():
    snapshot = _snapshot()
    page_a = select_page(snapshot, date(2026, 10, 26))
    changed = json.loads(json.dumps(snapshot))
    changed["spotted_candidates"][0]["species"] = "Yellow-rumped Warbler"
    page_b = select_page(changed, date(2026, 10, 26))
    assert hashlib.sha256(
        encode_png_grayscale_4bit(render_bird_image(page_a))
    ).hexdigest() != hashlib.sha256(
        encode_png_grayscale_4bit(render_bird_image(page_b))
    ).hexdigest()


def test_birds_routes_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("TRMNL_BIRDS_SNAPSHOT", str(SAMPLE))
    assert "birds" in server.plugin_specs()
    assert server.resolve_route("/birds/trmnl.json") == server.Route(
        "json", "birds", "/birds/image.png"
    )
    assert server.resolve_route("/birds/image.png") == server.Route(
        "image", "birds", "/birds/image.png"
    )


def test_birds_plugin_is_opt_in(monkeypatch):
    monkeypatch.delenv("TRMNL_BIRDS_SNAPSHOT", raising=False)
    assert "birds" not in server.plugin_specs()


def test_disabled_bird_routes_return_404():
    from http.client import HTTPConnection
    from threading import Thread

    http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler({}))
    thread = Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", http_server.server_port, timeout=2)
    try:
        for path in ("/birds/trmnl.json", "/birds/image.png"):
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            assert response.status == 404
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
        http_server.shutdown()
        thread.join()
        http_server.server_close()


def test_invalid_snapshot_does_not_block_startup_and_recovers(monkeypatch, tmp_path):
    from PIL import Image

    snapshot = tmp_path / "birds.json"
    snapshot.write_text("{invalid", encoding="utf-8")
    monkeypatch.setenv("TRMNL_BIRDS_SNAPSHOT", str(snapshot))
    monkeypatch.setattr(server, "render_bird_image", lambda page: Image.new("L", (1, 1)))
    plugin = server.plugin_specs()["birds"]
    cache = server.CalendarCache(plugin, 900, cache_dir=tmp_path / "cache")
    server.initialize_caches({"birds": cache})
    assert cache._rendered is None

    snapshot.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert cache.get().body.startswith(b"\x89PNG\r\n\x1a\n")


def test_required_plugin_initial_failure_still_raises():
    import pytest
    from unittest.mock import Mock

    cache = Mock()
    cache.ensure_initial.side_effect = ValueError("failed")
    with pytest.raises(ValueError, match="failed"):
        server.initialize_caches({"weekly": cache})
