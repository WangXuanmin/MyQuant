from datetime import datetime
from tempfile import TemporaryDirectory
from pathlib import Path
import json
import unittest
from xquant_assistant.runtime.calendar import TradingCalendar, CalendarUnavailable


class CalendarAcceptance(unittest.TestCase):
    def setUp(self):
        self.calendar = TradingCalendar(('2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09'),
            'OFFICIAL_HOLIDAY_SAMPLE', '2026-09-25','2026-10-10')

    def test_A05_holiday_weekend_and_expiry(self):
        self.assertFalse(self.calendar.is_session('2026-10-02'))
        self.assertEqual(self.calendar.shift('2026-09-28',3),'2026-10-08')
        self.assertEqual(self.calendar.elapsed('2026-09-28','2026-10-08'),3)

    def test_A05_anchor_does_not_shift_after_serialization(self):
        with TemporaryDirectory() as root:
            path = Path(root)/'calendar.json'
            self.calendar.save(path)
            other = TradingCalendar.load(path)
            self.assertTrue(other.due('2026-09-28','2026-10-08',3))
            self.assertFalse(other.due('2026-09-28','2026-09-30',3))
            self.assertEqual(other.digest,self.calendar.digest)

    def test_calendar_corruption_and_missing_range_block(self):
        with self.assertRaises(CalendarUnavailable):
            self.calendar.shift('2026-10-09',1)
        with TemporaryDirectory() as root:
            path = Path(root)/'calendar.json'
            self.calendar.save(path)
            payload=json.loads(path.read_text(encoding='utf-8'))
            payload['sessions'].append('2026-10-10')
            path.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaises(CalendarUnavailable):
                TradingCalendar.load(path)

    def test_incomplete_session_and_holiday_use_shanghai_clock(self):
        before=datetime.fromisoformat('2026-09-30T07:00:00+00:00')
        after=datetime.fromisoformat('2026-09-30T08:00:00+00:00')
        self.assertEqual(self.calendar.latest_completed(before),'2026-09-29')
        self.assertEqual(self.calendar.latest_completed(after),'2026-09-30')
        self.assertEqual(self.calendar.latest_completed(datetime.fromisoformat('2026-10-02T12:00:00+08:00')),'2026-09-30')
