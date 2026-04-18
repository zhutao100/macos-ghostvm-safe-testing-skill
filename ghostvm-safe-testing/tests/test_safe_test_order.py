from __future__ import annotations

import unittest
from pathlib import Path

SAFE_TEST_SH = Path(__file__).resolve().parents[1] / "scripts" / "ghostvm_safe_test.sh"


class TestSafeTestOrdering(unittest.TestCase):
    def test_snapshot_revert_happens_before_config_edit(self) -> None:
        text = SAFE_TEST_SH.read_text(encoding="utf-8")
        i_revert = text.find("[runner] reverting snapshot")
        i_config = text.find("[runner] configuring shared folders")
        self.assertNotEqual(i_revert, -1, "expected revert log marker")
        self.assertNotEqual(i_config, -1, "expected configure log marker")
        self.assertLess(
            i_revert,
            i_config,
            "safe runner must revert snapshot before editing config.json (snapshots include config.json)",
        )


if __name__ == "__main__":
    unittest.main()
