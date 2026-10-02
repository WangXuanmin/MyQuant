from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xquant_assistant.data.opening_status import normalize_opening, capture_opening_status
from .helpers import fixture


class OpeningAcceptance(unittest.TestCase):
    def setUp(self):
        self.config, self.calendar, _, _ = fixture()
        self.raw = {'HqDate': '20260901', 'TPFlag': '0', 'ZTPrice': '11.0', 'DTPrice': '9.0'}
        self.now = datetime.fromisoformat('2026-09-01T09:29:59+08:00')

    def test_live_fields_dates_and_unknown_values(self):
        state, reason = normalize_opening('sh600000', self.raw, self.now, self.calendar)
        self.assertIsNone(reason)
        self.assertFalse(state.is_suspended)
        self.assertEqual(state.limit_up, 11)
        for raw in ({**self.raw, 'HqDate': '20260831'}, {**self.raw, 'TPFlag': ''}, {**self.raw, 'ZTPrice': 'nan'}):
            self.assertIsNone(normalize_opening('sh600000', raw, self.now, self.calendar)[0])
        self.assertIsNone(normalize_opening('sh600000', self.raw, self.now.replace(hour=16), self.calendar)[0])
        self.assertIsNone(normalize_opening('sh600000', self.raw, datetime.fromisoformat('2026-09-06T09:29:59+08:00'), self.calendar)[0])

    def test_capture_is_readonly_and_preserves_original_archive(self):
        class Client:
            def get_more_info(client, **kwargs):
                return self.raw
            def get_stock_info(client, **kwargs):
                return {'Name':'工程股票','IsSTGP':'0','IsQuitGP':'0'}
        with TemporaryDirectory() as directory:
            result = capture_opening_status(directory, self.config, self.calendar, ['sh600000'], tq_client=Client(), clock=lambda: self.now)
            self.assertEqual(result['status'], 'CAPTURED')
            path = Path(directory)/'data/opening_status/2026-09-01.json'
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'ALREADY_EXISTS'):
                capture_opening_status(directory, self.config, self.calendar, ['sh600000'], tq_client=Client(), clock=lambda: self.now)
            self.assertEqual(original, path.read_bytes())
            self.assertFalse((Path(directory)/'data/state').exists())

    def test_stale_holiday_probe_does_not_create_opening_archive(self):
        class Client:
            def get_more_info(client, **kwargs):
                return self.raw
            def get_stock_info(client, **kwargs):
                return {'Name':'工程股票','IsSTGP':'0','IsQuitGP':'0'}
        with TemporaryDirectory() as directory:
            result = capture_opening_status(directory, self.config, self.calendar, ['sh600000'], tq_client=Client(), clock=lambda: self.now.replace(hour=16))
            self.assertEqual(result['status'], 'PROBE_ONLY')
            self.assertFalse((Path(directory)/'data/opening_status').exists())

    def test_current_st_delisting_and_unknown_identity_do_not_allow_buy(self):
        missing,_=normalize_opening('sh600000',self.raw,self.now,self.calendar)
        self.assertIsNone(missing.buy_allowed)
        for identity in ({'Name':'工程ST股票','IsSTGP':'1','IsQuitGP':'0'}, {'Name':'工程退市股票','IsSTGP':'0','IsQuitGP':'1'}):
            state,_=normalize_opening('sh600000',self.raw,self.now,self.calendar,identity)
            self.assertFalse(state.buy_allowed)
        normal,_=normalize_opening('sh600000',self.raw,self.now,self.calendar,{'Name':'工程股票','IsSTGP':'0','IsQuitGP':'0'})
        self.assertTrue(normal.buy_allowed)
