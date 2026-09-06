from __future__ import annotations

import unittest

from app.config import PROJECT_ROOT, Settings


class MvpContractTest(unittest.TestCase):
    def test_settings_repr_redacts_telegram_credentials(self) -> None:
        settings = Settings(
            telegram_bot_token="123456789:" + "A" * 30,
            telegram_chat_id="987654321",
        )

        rendered = repr(settings)

        self.assertNotIn("123456789", rendered)
        self.assertNotIn("987654321", rendered)

    def test_scheduled_runner_uses_web_queue_without_operational_summary(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "run_scheduled.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"schedule_daily_collection"', script)
        self.assertIn('"run_collection_worker" "--once"', script)
        self.assertNotIn("-m app.main", script)
        self.assertNotIn("--notify-summary", script)

    def test_destination_log_does_not_include_address_or_postal_code(self) -> None:
        source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("address.formatted", source)
        self.assertIn(
            'LOGGER.info("Destino de entrega confirmado para cotacao")', source
        )

    def test_example_environment_contains_no_credentials(self) -> None:
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("TELEGRAM_BOT_TOKEN=\n", env_example)
        self.assertIn("TELEGRAM_CHAT_ID=\n", env_example)
        self.assertNotIn("FORMA_PAGAMENTO", env_example)

    def test_deployment_examples_use_only_placeholders(self) -> None:
        compose_env = (PROJECT_ROOT / ".env.compose.example").read_text(
            encoding="utf-8"
        )
        staging_env = (PROJECT_ROOT / ".env.staging.example").read_text(
            encoding="utf-8"
        )

        self.assertIn("POSTGRES_PASSWORD=troque-por-", compose_env)
        self.assertIn("DJANGO_SECRET_KEY=SUBSTITUA_", staging_env)
        self.assertIn("TELEGRAM_MANAGER_BOT_TOKEN=\n", staging_env)
        self.assertNotIn("trycloudflare.com", compose_env + staging_env)

    def test_compose_and_ci_cover_all_operational_processes(self) -> None:
        compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        for service in ("db:", "web:", "worker:", "scheduler:", "monitor:"):
            self.assertIn(service, compose)
        self.assertIn("/health/ready/", compose)
        self.assertIn("backup_database", workflow)
        self.assertIn("restore_database", workflow)
        self.assertIn("docker build", workflow)

if __name__ == "__main__":
    unittest.main()
