"""Bridge Up: when Tower Bridge next lifts, and for which vessel.

Reads the official list at towerbridge.org.uk live (one request, 5 s timeout)
and falls back to lift_times.json, a bundled copy, if the site cannot be
reached or yields nothing.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")
SNAPSHOT_PATH = Path(__file__).with_name("lift_times.json")
LIVE_URL = "https://www.towerbridge.org.uk/lift-times"
USER_AGENT = (
    "Mozilla/5.0 (compatible; pippa-agent-app-bridge-up/0.1; "
    "+https://github.com/Pippa-Labs)"
)
LONDON = ZoneInfo("Europe/London")
WINDOW_MINUTES = 15

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]


class CannotReadDay(AgentAppError):
    user_message = ("I could not tell which day you meant. Try a weekday, "
                    "a date like 24 November, or 'this weekend'.")
    retryable = False


class CannotReadTime(AgentAppError):
    user_message = "I could not read that time. Try something like 6pm or 18:00."
    retryable = False


class NoLiftData(AgentAppError):
    user_message = "I could not get the Tower Bridge lift list just now."
    retryable = True


@dataclass(frozen=True)
class Lift:
    when: datetime          # London time, aware
    direction: str
    vessel: str

    def label(self) -> str:
        d = self.when
        text = f"{d.strftime('%a')} {d.day} {d.strftime('%b')} {d.strftime('%H:%M')}"
        if self.direction:
            text += f" {self.direction}"
        if self.vessel:
            text += f" · {self.vessel}"
        return text


# ── Parsing the official page ───────────────────────────────────────────────

class _LiftPageParser(HTMLParser):
    """Collects (heading, [row texts]) from the lift-times page.

    Each day is an <h3 class="time-table__heading">Tuesday 24 November 2026</h3>
    followed by <div class="bridge-lift-row"> blocks holding
    <p><strong>13:45</strong> Upriver</p><p><strong>Vessel</strong></p>.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.days: list[tuple[str, list[list[str]]]] = []
        self._in_heading = False
        self._heading = ""
        self._row_depth = 0          # div depth inside a bridge-lift-row
        self._row: list[str] | None = None
        self._in_p = False
        self._p = ""

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "h3" and "time-table__heading" in classes:
            self._in_heading, self._heading = True, ""
        elif tag == "div":
            if self._row is not None:
                self._row_depth += 1
            elif "bridge-lift-row" in classes:
                self._row, self._row_depth = [], 1
        elif tag == "p" and self._row is not None:
            self._in_p, self._p = True, ""

    def handle_endtag(self, tag):
        if tag == "h3" and self._in_heading:
            self._in_heading = False
            self.days.append((" ".join(self._heading.split()), []))
        elif tag == "p" and self._in_p:
            self._in_p = False
            text = " ".join(self._p.split())
            if text and self._row is not None:
                self._row.append(text)
        elif tag == "div" and self._row is not None:
            self._row_depth -= 1
            if self._row_depth == 0:
                if self.days and self._row:
                    self.days[-1][1].append(self._row)
                self._row = None

    def handle_data(self, data):
        if self._in_heading:
            self._heading += data
        elif self._in_p:
            self._p += data


_ROW = re.compile(r"(\d{1,2})[:.](\d{2})\s*(upriver|downriver)?", re.I)


def parse_lift_page(html: str) -> list[Lift]:
    parser = _LiftPageParser()
    parser.feed(html)
    lifts: list[Lift] = []
    for heading, rows in parser.days:
        day = _heading_date(heading)
        if day is None:
            continue
        for texts in rows:
            joined = " ".join(texts)
            match = _ROW.search(joined)
            if not match:
                continue
            hour, minute = int(match.group(1)), int(match.group(2))
            direction = (match.group(3) or "").capitalize()
            vessel = ""
            for text in texts:
                if not _ROW.search(text) and text.lower() not in ("upriver", "downriver"):
                    vessel = text
                    break
            when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=LONDON)
            lifts.append(Lift(when, direction, vessel))
    return sorted(lifts, key=lambda l: l.when)


def _heading_date(heading: str) -> date | None:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", heading.strip())
    for fmt in ("%A %d %B %Y", "%d %B %Y", "%A %d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


# ── Understanding the question ──────────────────────────────────────────────

def parse_day(text: str, today: date) -> tuple[date, date, str] | None:
    """(first day, last day, label) for what the person asked, or None."""
    t = " ".join(text.lower().replace(",", " ").split())
    if not t:
        return None
    t = re.sub(r"^(on|this coming|coming|this|next)\s+(?=\w)", "", t) if t not in (
        "this weekend", "next weekend") else t
    if t in ("today", "tonight", "now"):
        return today, today, "today"
    if t == "tomorrow":
        d = today + timedelta(days=1)
        return d, d, d.strftime("%A")
    if t in ("this weekend", "weekend", "the weekend", "next weekend"):
        if today.weekday() >= 5:
            start = today
        else:
            start = today + timedelta(days=5 - today.weekday())
        sunday = start + timedelta(days=6 - start.weekday())
        return start, sunday, "the weekend"
    for i, name in enumerate(WEEKDAYS):
        if t in (name, name[:3], name + "s"):
            d = today + timedelta(days=(i - today.weekday()) % 7)
            return d, d, name.capitalize()
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    cleaned = re.sub(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+", "", cleaned)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            d = datetime.strptime(cleaned, fmt).date()
            return d, d, _pretty(d)
        except ValueError:
            pass
    for fmt in ("%d %B", "%B %d", "%d %b", "%b %d", "%d/%m"):
        try:
            p = datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
        d = p.replace(year=today.year)
        if d < today - timedelta(days=31):
            d = d.replace(year=today.year + 1)
        return d, d, _pretty(d)
    raise CannotReadDay(f"could not read day {text!r}")


def parse_time(text: str) -> int | None:
    """Minutes after midnight, or None if no time was given."""
    t = text.strip().lower().replace(" ", "").replace(".", ":")
    if not t:
        return None
    if t == "noon" or t == "midday":
        return 12 * 60
    if t == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", t) or re.fullmatch(r"(\d{2})(\d{2})()", t)
    if not m:
        raise CannotReadTime(f"could not read time {text!r}")
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise CannotReadTime(f"time out of range {text!r}")
    return hour * 60 + minute


def _pretty(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


# ── The app ────────────────────────────────────────────────────────────────

class App:
    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    # The one network seam; tests replace it.
    def _fetch(self, url: str, timeout: float = 5.0) -> bytes:
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(2_000_000)

    def _load_lifts(self, now: datetime) -> tuple[list[Lift], str, str]:
        try:
            html = self._fetch(LIVE_URL).decode("utf-8", errors="replace")
            lifts = parse_lift_page(html)
            if lifts:
                return lifts, "towerbridge.org.uk lift list", now.date().isoformat()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        try:
            snap = json.loads(SNAPSHOT_PATH.read_text("utf-8"))
            lifts = [
                Lift(datetime.fromisoformat(f"{x['date']}T{x['time']}").replace(tzinfo=LONDON),
                     x.get("direction", ""), x.get("vessel", ""))
                for x in snap["lifts"]
            ]
        except (OSError, ValueError, KeyError) as exc:
            raise NoLiftData(f"live page and snapshot both failed: {exc}")
        return (sorted(lifts, key=lambda l: l.when),
                f"published list as of {snap['as_of']}", snap["as_of"])

    def handle(self, call: Call) -> Result:
        if call.outcome_id != "bridge.lifts":
            raise AgentAppError(f"this app cannot do {call.outcome_id!r}")

        now = call.context.now.astimezone(LONDON)
        today = now.date()
        asked_day = parse_day(call.inputs.get("day") or "", today)
        asked_minutes = parse_time(call.inputs.get("time") or "")
        if asked_day is None and asked_minutes is not None:
            asked_day = (today, today, "today")

        lifts, source, as_of = self._load_lifts(now)
        upcoming = [l for l in lifts if l.when >= now - timedelta(minutes=5)]

        data: dict = {"lifts_on_requested_day": [], "source": source, "as_of": as_of}
        on_day: list[Lift] = []
        if asked_day:
            first, last, _ = asked_day
            on_day = [l for l in upcoming if first <= l.when.date() <= last]
            data["lifts_on_requested_day"] = [l.label() for l in on_day]
            start = max(now, datetime(first.year, first.month, first.day, tzinfo=LONDON))
            after = [l for l in upcoming if l.when >= start]
        else:
            after = upcoming
        nxt = after[0] if after else None
        if nxt:
            data["next_lift_date"] = nxt.when.date().isoformat()
            data["next_lift_time"] = nxt.when.strftime("%H:%M")
            if nxt.direction:
                data["direction"] = nxt.direction
            if nxt.vessel:
                data["vessel"] = nxt.vessel

        up = None
        if asked_minutes is not None and asked_day:
            day = asked_day[0]
            target = datetime(day.year, day.month, day.day, asked_minutes // 60,
                              asked_minutes % 60, tzinfo=LONDON)
            up = any(abs((l.when - target).total_seconds()) <= WINDOW_MINUTES * 60
                     for l in lifts)
            data["up_at_requested_time"] = up

        return Result(data=data, narrative=self._sentence(nxt, asked_day, on_day,
                                                         asked_minutes, up, source))

    @staticmethod
    def _sentence(nxt, asked_day, on_day, minutes, up, source) -> str:
        parts = []
        if minutes is not None and asked_day:
            clock = f"{minutes // 60:02d}:{minutes % 60:02d}"
            parts.append(
                f"A lift is listed within {WINDOW_MINUTES} minutes of {clock} {asked_day[2]}, so expect the road to close briefly."
                if up else f"No lift is listed near {clock} {asked_day[2]}.")
        elif asked_day:
            if on_day:
                times = ", ".join(l.when.strftime("%H:%M") for l in on_day)
                n = len(on_day)
                parts.append(f"{asked_day[2]} has {n} lift{'s' if n != 1 else ''}: {times}.")
            else:
                parts.append(f"No lifts are listed for {asked_day[2]}.")
        if nxt:
            what = f"Tower Bridge next lifts {_pretty(nxt.when.date())} at {nxt.when.strftime('%H:%M')}"
            if nxt.direction:
                what += f", {nxt.direction.lower()}"
            if nxt.vessel:
                what += f", for {nxt.vessel}"
            parts.append(what + ".")
        else:
            parts.append("No further lifts are on the list yet.")
        parts.append(f"(Source: {source}; lifts can be cancelled at short notice.)")
        return " ".join(parts)
