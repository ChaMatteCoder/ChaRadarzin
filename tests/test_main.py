from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import PROJECT_ROOT, Settings
from app.main import run


class PipelineSmokeTest(unittest.TestCase):
    def test_simulated_pipeline_runs_in_isolated_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                project_root=root,
                input_file=PROJECT_ROOT / "produtos.xlsx",
                database_file=root / "data" / "radar_precos.db",
                reports_dir=root / "reports",
                logs_dir=root / "logs",
            )

            with (
                patch("app.main.get_settings", return_value=settings),
                patch("app.main._configure_logging"),
            ):
                report_path = run(simulate=True)

            self.assertTrue(report_path.is_file())
            self.assertTrue((settings.reports_dir / "ultimo_relatorio.md").is_file())
            connection = sqlite3.connect(settings.database_file)
            try:
                run_row = connection.execute(
                    "SELECT status, mode, observations_count FROM runs"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(run_row, ("SUCCESS", "SIMULATION", 4))


if __name__ == "__main__":
    unittest.main()
