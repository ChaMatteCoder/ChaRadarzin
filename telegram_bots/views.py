from __future__ import annotations

import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from chadaradzin.rate_limit import consume_rate_limit
from telegram_bots.configuration import (
    ManagedBotConflict,
    disconnect_bot,
    process_managed_bot_update,
)
from telegram_bots.crypto import webhook_secret_digest
from telegram_bots.models import (
    BotOnboardingSession,
    ManagedBot,
    TelegramUpdateReceipt,
    UpdateProcessingStatus,
)
from telegram_bots.onboarding import (
    OnboardingError,
    build_managed_bot_deep_link,
    expire_stale_onboarding,
    start_onboarding,
)
from telegram_bots.updates import (
    TelegramUpdateError,
    process_personal_bot_update,
    update_kind,
)


MAX_UPDATE_BODY_BYTES = 1_048_576
LOGGER = logging.getLogger(__name__)


def _json_update(request: HttpRequest) -> dict | None:
    raw_length = request.headers.get("Content-Length", "")
    if raw_length.isdigit() and int(raw_length) > MAX_UPDATE_BODY_BYTES:
        return None
    try:
        body = request.body
        if len(body) > MAX_UPDATE_BODY_BYTES:
            return None
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _claim_receipt(
    endpoint_key: str,
    update: dict,
    *,
    bot: ManagedBot | None = None,
) -> tuple[TelegramUpdateReceipt | None, bool]:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None, False
    defaults = {"bot": bot, "update_kind": update_kind(update)}
    try:
        with transaction.atomic():
            receipt, created = TelegramUpdateReceipt.objects.get_or_create(
                endpoint_key=endpoint_key,
                update_id=update_id,
                defaults=defaults,
            )
    except IntegrityError:
        receipt = TelegramUpdateReceipt.objects.get(
            endpoint_key=endpoint_key,
            update_id=update_id,
        )
        created = False
    if created:
        return receipt, True
    if receipt.status == UpdateProcessingStatus.FAILED:
        receipt.status = UpdateProcessingStatus.PROCESSING
        receipt.outcome_code = ""
        receipt.processed_at = None
        receipt.save(update_fields=["status", "outcome_code", "processed_at"])
        return receipt, True
    return receipt, False


def _finish_receipt(
    receipt: TelegramUpdateReceipt,
    status: str,
    outcome: str,
) -> None:
    receipt.status = status
    receipt.outcome_code = outcome[:80]
    receipt.processed_at = timezone.now()
    receipt.save(update_fields=["status", "outcome_code", "processed_at"])


def _secret_header(request: HttpRequest) -> str:
    return request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")


def _webhook_rate_limited(endpoint: str, identity: str) -> HttpResponse | None:
    result = consume_rate_limit(
        f"telegram-webhook:{endpoint}",
        identity,
        limit=settings.TELEGRAM_WEBHOOK_RATE_LIMIT,
        window_seconds=settings.TELEGRAM_WEBHOOK_RATE_WINDOW_SECONDS,
    )
    if result.allowed:
        return None
    LOGGER.warning(
        "telegram_webhook_rate_limited",
        extra={"event_code": "WEBHOOK_RATE_LIMITED", "endpoint": endpoint},
    )
    return HttpResponse(
        status=429,
        headers={
            "Retry-After": str(result.retry_after_seconds),
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


@csrf_exempt
@require_POST
def manager_webhook(request: HttpRequest, endpoint_id) -> HttpResponse:
    if not settings.TELEGRAM_MANAGER_ENABLED:
        raise Http404
    if not hmac.compare_digest(str(endpoint_id), settings.TELEGRAM_MANAGER_WEBHOOK_ID):
        raise Http404
    limited = _webhook_rate_limited("manager", str(endpoint_id))
    if limited is not None:
        return limited
    if not hmac.compare_digest(_secret_header(request), settings.TELEGRAM_MANAGER_WEBHOOK_SECRET):
        LOGGER.warning(
            "telegram_webhook_secret_rejected",
            extra={"event_code": "WEBHOOK_SECRET_REJECTED", "endpoint": "manager"},
        )
        return HttpResponse(status=403)
    update = _json_update(request)
    if update is None:
        return HttpResponse(status=400)
    receipt, should_process = _claim_receipt("manager", update)
    if receipt is None:
        return HttpResponse(status=400)
    if not should_process:
        return JsonResponse({"ok": True, "duplicate": True})
    try:
        result = process_managed_bot_update(update)
    except ManagedBotConflict as exc:
        _finish_receipt(receipt, UpdateProcessingStatus.REJECTED, exc.code)
        LOGGER.info(
            "telegram_manager_update_rejected",
            extra={"event_code": exc.code, "endpoint": "manager"},
        )
        return JsonResponse({"ok": True})
    except Exception:
        _finish_receipt(receipt, UpdateProcessingStatus.FAILED, "MANAGER_PROCESSING_FAILED")
        LOGGER.exception(
            "telegram_manager_update_failed",
            extra={"event_code": "MANAGER_PROCESSING_FAILED", "endpoint": "manager"},
        )
        return JsonResponse({"ok": False}, status=503)
    receipt.bot = result.bot
    receipt.save(update_fields=["bot"])
    _finish_receipt(receipt, UpdateProcessingStatus.PROCESSED, result.outcome_code)
    LOGGER.info(
        "telegram_manager_update_processed",
        extra={"event_code": result.outcome_code, "endpoint": "manager"},
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def personal_bot_webhook(request: HttpRequest, webhook_id) -> HttpResponse:
    limited = _webhook_rate_limited("personal", str(webhook_id))
    if limited is not None:
        return limited
    bot = get_object_or_404(ManagedBot, webhook_public_id=webhook_id)
    supplied_digest = webhook_secret_digest(_secret_header(request))
    if not bot.webhook_secret_digest or not hmac.compare_digest(
        supplied_digest,
        bot.webhook_secret_digest,
    ):
        LOGGER.warning(
            "telegram_webhook_secret_rejected",
            extra={"event_code": "WEBHOOK_SECRET_REJECTED", "endpoint": "personal"},
        )
        return HttpResponse(status=403)
    update = _json_update(request)
    if update is None:
        return HttpResponse(status=400)
    endpoint_key = f"bot:{bot.webhook_public_id.hex}"
    receipt, should_process = _claim_receipt(endpoint_key, update, bot=bot)
    if receipt is None:
        return HttpResponse(status=400)
    if not should_process:
        return JsonResponse({"ok": True, "duplicate": True})
    try:
        outcome = process_personal_bot_update(bot, update)
    except TelegramUpdateError as exc:
        _finish_receipt(receipt, UpdateProcessingStatus.FAILED, str(exc))
        LOGGER.warning(
            "telegram_personal_update_retryable_failure",
            extra={"event_code": str(exc)[:80], "endpoint": "personal"},
        )
        return JsonResponse({"ok": False}, status=503)
    except Exception:
        _finish_receipt(receipt, UpdateProcessingStatus.FAILED, "BOT_PROCESSING_FAILED")
        LOGGER.exception(
            "telegram_personal_update_failed",
            extra={"event_code": "BOT_PROCESSING_FAILED", "endpoint": "personal"},
        )
        return JsonResponse({"ok": False}, status=503)
    status = (
        UpdateProcessingStatus.REJECTED
        if outcome in {"NON_OWNER_REJECTED", "INVALID_MESSAGE", "INVALID_MEMBERSHIP", "INVALID_CALLBACK"}
        else UpdateProcessingStatus.PROCESSED
    )
    _finish_receipt(receipt, status, outcome)
    LOGGER.info(
        "telegram_personal_update_processed",
        extra={"event_code": outcome, "status": status, "endpoint": "personal"},
    )
    return JsonResponse({"ok": True})


@login_required
def bot_status(request: HttpRequest) -> HttpResponse:
    if request.user.tenant_id is None:
        raise Http404
    expire_stale_onboarding()
    bot = ManagedBot.objects.filter(tenant_id=request.user.tenant_id).first()
    onboarding = (
        BotOnboardingSession.objects.filter(tenant_id=request.user.tenant_id)
        .order_by("-created_at")
        .first()
    )
    deep_link = ""
    if onboarding is not None and onboarding.is_open and settings.TELEGRAM_MANAGER_ENABLED:
        try:
            deep_link = build_managed_bot_deep_link(onboarding)
        except OnboardingError:
            pass
    return render(
        request,
        "telegram_bots/status.html",
        {
            "bot": bot,
            "onboarding": onboarding,
            "deep_link": deep_link,
            "manager_enabled": settings.TELEGRAM_MANAGER_ENABLED,
        },
    )


@login_required
@require_POST
def create_personal_bot(request: HttpRequest) -> HttpResponse:
    if not settings.TELEGRAM_MANAGER_ENABLED:
        messages.error(request, "A criacao de bots pessoais ainda nao esta habilitada.")
        return redirect("bot-status")
    try:
        session = start_onboarding(
            request.user,
            suggested_name=request.POST.get("name", ""),
            suggested_username=request.POST.get("username", ""),
        )
        deep_link = build_managed_bot_deep_link(session)
    except OnboardingError as exc:
        messages.error(request, str(exc))
        return redirect("bot-status")
    return redirect(deep_link)


@login_required
@require_POST
def disconnect_personal_bot(request: HttpRequest) -> HttpResponse:
    bot = ManagedBot.objects.filter(tenant_id=request.user.tenant_id).first()
    if bot is None:
        messages.error(request, "Nenhum bot pessoal foi encontrado.")
        return redirect("bot-status")
    try:
        disconnect_bot(bot)
    except Exception:
        messages.error(request, "Nao foi possivel desconectar o webhook agora.")
        return redirect("bot-status")
    messages.success(request, "Bot desconectado e credencial local removida.")
    return redirect("bot-status")
