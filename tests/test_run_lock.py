from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.run_lock import AlreadyRunningError, SingleInstanceLock


class RunLockTest(unittest.TestCase):
    def test_rejects_second_instance_and_releases_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "radar.lock"
            with SingleInstanceLock(lock_path):
                with self.assertRaises(AlreadyRunningError):
                    with SingleInstanceLock(lock_path):
                        self.fail("A segunda instancia nao poderia adquirir o bloqueio")

            with SingleInstanceLock(lock_path):
                self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
