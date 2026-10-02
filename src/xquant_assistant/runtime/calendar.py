"""Explicit, hashed exchange sessions; never inferred from security bars."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib

from ..storage import atomic_write_json, read_json


class CalendarUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class TradingCalendar:
    sessions: tuple[str, ...]
    source: str
    coverage_start: str
    coverage_end: str

    def __post_init__(self):
        if not self.sessions or tuple(sorted(set(self.sessions))) != self.sessions:
            raise CalendarUnavailable("calendar sessions must be unique and sorted")
        for value in (*self.sessions, self.coverage_start, self.coverage_end):
            datetime.strptime(value, "%Y-%m-%d")
        if self.sessions[0] < self.coverage_start or self.sessions[-1] > self.coverage_end:
            raise CalendarUnavailable("sessions outside declared coverage")

    @property
    def digest(self):
        return hashlib.sha256((self.source + '|' + self.coverage_start + '|' + self.coverage_end + '|' + ','.join(self.sessions)).encode()).hexdigest()

    def check_range(self, day):
        if not self.coverage_start <= day <= self.coverage_end:
            raise CalendarUnavailable(f"calendar does not cover {day}")

    def is_session(self, day):
        self.check_range(day)
        return day in self.sessions

    def index(self, day):
        self.check_range(day)
        try:
            return self.sessions.index(day)
        except ValueError as exc:
            raise CalendarUnavailable(f"not a trading session: {day}") from exc

    def shift(self, day, count):
        index = self.index(day) + count
        if not 0 <= index < len(self.sessions):
            raise CalendarUnavailable(f"calendar cannot shift {day} by {count}")
        return self.sessions[index]

    def elapsed(self, earlier, later):
        return self.index(later) - self.index(earlier)

    def due(self, anchor, day, frequency):
        return anchor is None or (self.elapsed(anchor, day) >= 0 and self.elapsed(anchor, day) % frequency == 0)

    def latest_completed(self, now=None):
        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        today = now.date().isoformat()
        self.check_range(today)
        available = [day for day in self.sessions if day < today or (day == today and now.hour >= 16)]
        if not available:
            raise CalendarUnavailable("no completed session in coverage")
        return available[-1]

    def save(self, path):
        atomic_write_json(path, {"sessions": self.sessions, "source": self.source,
            "coverage_start": self.coverage_start, "coverage_end": self.coverage_end,
            "sha256": self.digest, "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()})

    @classmethod
    def load(cls, path):
        data = read_json(path)
        calendar = cls(tuple(data["sessions"]), data["source"], data["coverage_start"], data["coverage_end"])
        if calendar.digest != data["sha256"]:
            raise CalendarUnavailable("calendar hash mismatch")
        return calendar


def load_calendar(root, *, allow_network=False):
    path = Path(root) / 'data/calendar/sessions.json'
    if path.exists():
        return TradingCalendar.load(path)
    if not allow_network:
        raise CalendarUnavailable("CALENDAR_UNAVAILABLE: run refresh-calendar or supply a verified cache")
    return refresh_calendar(root)


def refresh_calendar(root):
    """Read the same Sina payload as AkShare, with a bounded network timeout."""
    import requests
    import py_mini_racer
    from akshare.tool.trade_date_hist import hk_js_decode
    url = 'https://finance.sina.com.cn/realstock/company/klc_td_sh.txt'
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    decoder = py_mini_racer.MiniRacer()
    decoder.eval(hk_js_decode)
    dates = tuple(sorted(set(str(x)[:10] for x in decoder.call('d', response.text.split('=')[1].split(';')[0].replace('"', '')))))
    # AkShare records this specific missing historical Sina session.
    if dates[0] <= '1992-05-04' <= dates[-1] and '1992-05-04' not in dates:
        dates = tuple(sorted((*dates, '1992-05-04')))
    calendar = TradingCalendar(dates, url, dates[0], dates[-1])
    calendar.save(Path(root)/'data/calendar/sessions.json')
    return calendar
