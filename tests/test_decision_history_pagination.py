from __future__ import annotations

import unittest

from pairs_trading.platform import SQLiteMetadataStore
from pairs_trading.research.decision_history import CommitteeDecision, DecisionHistoryStore
from tests.common import fresh_test_dir


class DecisionHistoryPaginationTests(unittest.TestCase):
    def test_history_operations_can_access_records_older_than_fifty_jobs(self) -> None:
        workspace = fresh_test_dir("artifacts/test_decision_history_pagination")
        history = DecisionHistoryStore()
        history._store = SQLiteMetadataStore(workspace / "metadata.sqlite3", enable_demo_accounts=False)
        oldest = CommitteeDecision(
            ticker="OLD",
            timestamp="2020-01-01T00:00:00Z",
            decision="hold",
            confidence=42,
            organization_id="org-a",
        )
        history.add(oldest)
        for index in range(50):
            history.add(
                CommitteeDecision(
                    ticker="NEW",
                    timestamp=f"2026-01-01T00:{index:02d}:00Z",
                    decision="buy",
                    confidence=80,
                    organization_id="org-a",
                )
            )

        loaded = history.get(oldest.id)
        filtered = history.list(ticker="OLD", organization_id="org-a")
        summary = history.summary(organization_id="org-a")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, oldest.id)
        self.assertEqual([decision.id for decision in filtered], [oldest.id])
        self.assertEqual(summary["total_decisions"], 51)
        self.assertEqual(summary["unique_tickers"], 2)


if __name__ == "__main__":
    unittest.main()
