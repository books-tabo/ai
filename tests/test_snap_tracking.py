"""Synthetic conversion tests; no external event is transmitted."""
import copy
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

import snap_tracking


class SnapTrackingTests(unittest.TestCase):
    def setUp(self):
        self.now = snap_tracking.PURCHASE_SINCE + timedelta(hours=1)
        self.order = {'id': 'TABO-0123456789ABCDEF', 'status': 'PAID',
                      'paymentMode': 'live', 'transactionId': 'provider-private',
                      'paidAt': (self.now - timedelta(minutes=1)).isoformat(),
                      'total': '829.27', 'currency': 'EGP',
                      'items': [{'id': 'book20'}], 'buyerEmail': 'private@example.invalid'}

    def result(self, **changes):
        return snap_tracking.purchase(dict(self.order, **changes), self.now)

    def test_value_is_actual_stored_charge_and_payload_is_minimal(self):
        data = self.result()
        self.assertEqual(data['value'], 829.27)
        self.assertEqual(data['currency'], 'EGP')
        self.assertEqual(data['content_ids'], ['book20'])
        self.assertEqual(set(data), {'transaction_id', 'value', 'currency', 'content_ids'})
        self.assertRegex(data['transaction_id'], r'^[a-f0-9]{64}$')
        self.assertNotIn('provider-private', str(data))
        self.assertNotIn(self.order['id'], str(data))
        self.assertNotIn('private@example.invalid', str(data))
        self.assertEqual(data['transaction_id'], self.result()['transaction_id'])
        self.assertNotEqual(data['transaction_id'], self.result(id='TABO-FEDCBA9876543210')['transaction_id'])
        self.assertEqual(self.result(total='15.90', currency='USD')['value'], 15.9)

    def test_unpaid_revoked_test_and_legacy_orders_do_not_count(self):
        for status in ('PENDING', 'FAILED', 'REVIEW', 'REFUNDED', 'VOIDED', ''):
            with self.subTest(status=status):
                self.assertIsNone(self.result(status=status))
        for changes in ({'paymentMode': 'test'}, {'paymentMode': ''}, {'transactionId': ''}):
            self.assertIsNone(self.result(**changes))

    def test_purchase_time_must_be_recent_and_after_installation(self):
        for paid in ('', 'not-a-date', '2026-10-26T00:00:00',
                     (snap_tracking.PURCHASE_SINCE - timedelta(seconds=1)).isoformat(),
                     (self.now + timedelta(seconds=1)).isoformat()):
            with self.subTest(paid=paid):
                self.assertIsNone(self.result(paidAt=paid))
        late = self.now + timedelta(hours=25)
        self.assertIsNone(snap_tracking.purchase(self.order, late))

    def test_invalid_charge_and_catalog_data_are_rejected(self):
        for value in ('0', '-1', 'NaN', 'Infinity', '1e9999', 'bad', None):
            with self.subTest(value=value):
                self.assertIsNone(self.result(total=value))
        for changes in ({'currency': 'KWD'}, {'id': 'private-order'}, {'items': []},
                        {'items': [{'id': 'invented'}]}, {'items': [{}]}):
            self.assertIsNone(self.result(**changes))

    def test_explicitly_disabled_or_invalid_pixel_never_emits(self):
        for value in ('', '<script>', '1371060818078535'):
            with patch.dict(os.environ, {'TABO_SNAP_PIXEL_ID': value}):
                self.assertEqual(snap_tracking.pixel_id(), '')
                self.assertIsNone(self.result())


if __name__ == '__main__':
    unittest.main()
