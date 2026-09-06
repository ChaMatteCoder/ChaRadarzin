from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tenancy.models import BetaAccessGrant


class Command(BaseCommand):
    help = "Cadastra, atualiza ou desativa um participante do beta fechado."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--telegram-user-id", required=True, type=int)
        parser.add_argument("--username", default="")
        parser.add_argument("--name", default="")
        parser.add_argument("--disable", action="store_true")

    def handle(self, *args, **options) -> None:
        telegram_user_id = options["telegram_user_id"]
        if telegram_user_id <= 0:
            raise CommandError("--telegram-user-id deve ser positivo.")

        username = options["username"].strip().lstrip("@")[:64]
        display_name = options["name"].strip()[:160]
        grant, created = BetaAccessGrant.objects.get_or_create(
            telegram_user_id=telegram_user_id,
            defaults={
                "telegram_username": username,
                "display_name": display_name,
                "active": not options["disable"],
            },
        )
        changed: list[str] = []
        for field_name, value in (
            ("telegram_username", username or grant.telegram_username),
            ("display_name", display_name or grant.display_name),
            ("active", not options["disable"]),
        ):
            if getattr(grant, field_name) != value:
                setattr(grant, field_name, value)
                changed.append(field_name)
        if changed:
            grant.save(update_fields=changed + ["updated_at"])

        state = "desativado" if not grant.active else "ativo"
        action = "criado" if created else "atualizado"
        self.stdout.write(self.style.SUCCESS(f"Acesso beta {action} e {state}."))
