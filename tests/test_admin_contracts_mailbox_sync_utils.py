"""Testy helperow endpointu synchronizacji mailboxa w module umow."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from app.api.routes.admin_contracts import _tail_text
from app.services.contracts_mailbox_sync_runtime import (
    parse_mailbox_sync_summary,
    run_mailbox_sync_subprocess,
)


class ContractsMailboxSyncUtilsTests(unittest.TestCase):
    """Weryfikuje parsowanie podsumowania i docinanie logow synchronizacji."""

    def test_parse_mailbox_sync_summary_extracts_all_counters(self) -> None:
        output = (
            "[INFO] Analizowane: 12, zaktualizowane: 7, pominięte (stan): 2, ostrzeżenia: 1, "
            "nierozpoznane: 1, niedopasowane: 0, wieloznaczne: 1, otwarte wyjątki: 4\n"
        )
        summary = parse_mailbox_sync_summary(output)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["analysed"], 12)
        self.assertEqual(summary["updated"], 7)
        self.assertEqual(summary["skipped_state"], 2)
        self.assertEqual(summary["warnings"], 1)
        self.assertEqual(summary["unknown_subjects"], 1)
        self.assertEqual(summary["unmatched_forms"], 0)
        self.assertEqual(summary["ambiguous_matches"], 1)
        self.assertEqual(summary["unresolved_open"], 4)

    def test_parse_mailbox_sync_summary_extracts_manual_hold(self) -> None:
        output = (
            "[INFO] Analizowane: 172, zaktualizowane: 172, pominięte (stan): 0, "
            "ostrzeżenia: 1, nierozpoznane: 40, niedopasowane: 51, "
            "wieloznaczne: 0, otwarte wyjątki: 0, wstrzymane ręcznie: 1\n"
        )

        summary = parse_mailbox_sync_summary(output)

        assert summary is not None
        self.assertEqual(summary["manual_hold"], 1)

    def test_parse_mailbox_sync_summary_returns_none_when_line_is_missing(self) -> None:
        output = "[INFO] Brak danych do przetworzenia\n"
        summary = parse_mailbox_sync_summary(output)
        self.assertIsNone(summary)

    def test_tail_text_limits_lines_and_chars(self) -> None:
        output = "\n".join([f"line-{idx:03d}" for idx in range(30)])
        tail = _tail_text(output, max_lines=5, max_chars=20)

        self.assertLessEqual(len(tail), 20)
        self.assertIn("line-029", tail)

    @patch("app.services.contracts_mailbox_sync_runtime.subprocess.run")
    def test_run_mailbox_sync_subprocess_builds_flags(self, mocked_run) -> None:
        mocked_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        run_mailbox_sync_subprocess(
            limit=15,
            folder="INBOX.Subfolder",
            reprocess=True,
            dry_run=True,
            timeout_seconds=120,
        )

        self.assertEqual(mocked_run.call_count, 1)
        command = mocked_run.call_args.args[0]
        self.assertIn("--limit", command)
        self.assertIn("15", command)
        self.assertIn("--folder", command)
        self.assertIn("INBOX.Subfolder", command)
        self.assertIn("--reprocess", command)
        self.assertIn("--dry-run", command)


if __name__ == "__main__":
    unittest.main()
