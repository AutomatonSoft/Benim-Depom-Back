from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.db import connections
from django.test import TransactionTestCase

from apps.ebay_bulk_end.inventory import _claim_snapshot
from apps.ebay_bulk_end.models import EbayInventorySnapshot


class SnapshotClaimConcurrencyTests(TransactionTestCase):
    def test_two_workers_cannot_claim_the_same_snapshot(self):
        snapshot = EbayInventorySnapshot.objects.create(
            account="jv",
            environment="sandbox",
            ebay_seller_id="seller-jv",
            marketplace_id="EBAY_DE",
            status="importing",
        )
        barrier = Barrier(2)

        def claim():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                _snapshot, token, generation, _owner = _claim_snapshot(
                    snapshot.pk, uuid4()
                )
                return token, generation
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _index: claim(), range(2)))

        self.assertEqual(sum(token is not None for token, _generation in results), 1)
        self.assertEqual([generation for token, generation in results if token], [1])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.ownership_generation, 1)
