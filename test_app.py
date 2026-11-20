"""Tests for Bridge Up. The network is blocked (conftest.py); the live page is
stubbed through App._fetch, and the fallback uses the bundled lift_times.json."""

from __future__ import annotations

import urllib.error
from datetime import datetime, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner, OutputShapeViolation, Result

from app import App, CannotReadDay, CannotReadTime, parse_lift_page

# Friday 20 November 2026, 15:00 London.
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)

PAGE = """
<div class="time-table"><h3 class="time-table__heading">Tuesday 24 November 2026</h3>
  <div class="bridge-lift-row"><div class="inner">
    <p><strong>13:45</strong> Upriver</p><p><strong>Thames Barge Centaur</strong></p>
  </div></div></div>
<div class="time-table"><h3 class="time-table__heading">Wednesday 25 November 2026</h3>
  <div class="bridge-lift-row"><p><strong>18:00</strong> Upriver</p>
    <p><strong>Paddle Steamer Dixie Queen</strong></p></div>
  <div class="bridge-lift-row"><p><strong>18:45</strong> Downriver</p>
    <p><strong>Paddle Steamer Dixie Queen</strong></p></div></div>
<div class="time-table"><h3 class="time-table__heading">Saturday 21 November 2026</h3>
  <div class="bridge-lift-row"><p><strong>10:30</strong> Downriver</p>
    <p><strong>HMS Example</strong></p></div></div>
"""


def live(url, timeout=5.0):
    return PAGE.encode()


def blocked(url, timeout=5.0):
    raise urllib.error.URLError("blocked")


def timed_out(url, timeout=5.0):
    raise TimeoutError("slow")


def call(inputs, fetch=live, *, explicit=False):
    app = App()
    app._fetch = fetch
    return LocalRunner(app).call(
        "bridge.lifts", inputs, granted_secrets={}, now=NOW,
        request_id="t1", explicit=explicit,
    ).result


def test_manifest_is_valid():
    app = App()
    assert app.manifest.app_id == "hack.app_4a39"
    assert [o.id for o in app.manifest.outcomes] == ["bridge.lifts"]


def test_parser_reads_date_time_direction_and_vessel():
    lifts = parse_lift_page(PAGE)
    assert len(lifts) == 4
    first = lifts[1]
    assert first.when.strftime("%Y-%m-%d %H:%M") == "2026-11-24 13:45"
    assert first.direction == "Upriver"
    assert first.vessel == "Thames Barge Centaur"


def test_parser_ignores_a_page_with_no_lifts():
    assert parse_lift_page("<html><body>Maintenance</body></html>") == []


def test_next_lift_with_no_day():
    d = call({}).data
    assert d["next_lift_date"] == "2026-11-21"
    assert d["next_lift_time"] == "10:30"
    assert d["direction"] == "Downriver"
    assert d["vessel"] == "HMS Example"
    assert d["source"] == "towerbridge.org.uk lift list"


def test_lifts_on_a_named_weekday():
    r = call({"day": "Wednesday"})
    assert r.data["lifts_on_requested_day"] == [
        "Wed 25 Nov 18:00 Upriver · Paddle Steamer Dixie Queen",
        "Wed 25 Nov 18:45 Downriver · Paddle Steamer Dixie Queen",
    ]
    assert "2 lifts" in r.narrative


def test_this_weekend_covers_saturday_and_sunday():
    d = call({"day": "this weekend"}).data
    assert d["lifts_on_requested_day"] == ["Sat 21 Nov 10:30 Downriver · HMS Example"]


def test_a_date_in_words():
    d = call({"day": "24 November"}).data
    assert d["lifts_on_requested_day"][0].startswith("Tue 24 Nov 13:45")


def test_a_day_with_no_lifts_gives_the_next_one():
    r = call({"day": "Monday"})
    assert r.data["lifts_on_requested_day"] == []
    assert r.data["next_lift_date"] == "2026-11-24"
    assert "No lifts are listed for Monday" in r.narrative


def test_up_at_requested_time_true():
    d = call({"day": "Wednesday", "time": "6pm"}).data
    assert d["up_at_requested_time"] is True


def test_up_at_requested_time_false():
    d = call({"day": "Wednesday", "time": "3pm"}).data
    assert d["up_at_requested_time"] is False


def test_24_hour_time_is_read():
    d = call({"day": "Wednesday", "time": "18:45"}).data
    assert d["up_at_requested_time"] is True


def test_falls_back_to_bundled_list_when_site_blocked():
    d = call({}, fetch=blocked).data
    assert d["source"].startswith("published list as of")
    assert d["next_lift_date"] == "2026-11-27"
    assert "vessel" not in d


def test_falls_back_on_timeout():
    d = call({}, fetch=timed_out).data
    assert d["as_of"] == "2026-10-13"


def test_falls_back_when_live_page_has_no_lifts():
    d = call({}, fetch=lambda u, timeout=5.0: b"<html></html>").data
    assert d["source"].startswith("published list")


def test_unknown_day_is_a_friendly_error():
    with pytest.raises(CannotReadDay) as failure:
        call({"day": "Narnia day"})
    assert "which day" in failure.value.user_message


def test_bad_time_is_a_friendly_error():
    with pytest.raises(CannotReadTime) as failure:
        call({"time": "half past blah"})
    assert "6pm" in failure.value.user_message


def test_picked_by_name_answers_the_same():
    assert call({}, explicit=True).data == call({}).data


def test_runner_enforces_the_manifest(monkeypatch):
    monkeypatch.setattr(App, "handle", lambda self, c: Result(data={"surprise": 1}))
    with pytest.raises(OutputShapeViolation):
        call({})
