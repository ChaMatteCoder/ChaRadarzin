from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.address import resolve_postal_code
from app.analytics import analyze_product
from app.collectors import collect_real_observations
from app.comparison import choose_best_offer
from app.config import Settings, get_settings
from app.database import RadarDatabase
from app.models import ProductSummary
from app.notifications import (
    AlertDecision,
    TelegramClient,
    TelegramError,
    evaluate_alert,
    format_price_alert,
    format_run_summary,
    format_test_message,
)
from app.report import write_report
from app.run_lock import AlreadyRunningError, SingleInstanceLock
from app.simulation import generate_simulated_observations
from app.spreadsheet import load_catalog


LOGGER = logging.getLogger("radar_precos")


class SummaryNotificationError(RuntimeError):
    pass


def _configure_logging(log_file: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def _telegram_client(settings: Settings) -> TelegramClient:
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise ValueError(
            "Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no arquivo .env"
        )
    return TelegramClient(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        timeout=settings.request_timeout_seconds,
        retries=settings.request_retries,
    )


def _notify_relevant_changes(
    database: RadarDatabase,
    run_id: int,
    summaries: tuple[ProductSummary, ...],
    previous_lows: dict[str, Decimal | None],
    previous_stock_states: dict[str, bool | None],
    settings: Settings,
) -> None:
    client = _telegram_client(settings)
    for summary in summaries:
        current_price = summary.best_price
        if current_price is None:
            continue

        product_id = summary.product.product_id
        decision = evaluate_alert(
            summary,
            previous_historical_low=previous_lows.get(product_id),
            previous_in_stock=previous_stock_states.get(product_id),
            alert_price_increase=settings.telegram_alert_price_increase,
        )
        if decision is not None and database.notification_attempt_exists(
            product_id,
            current_price,
            status="SENT",
            event_type=decision.event_type,
        ):
            continue
        if decision is None:
            pending = database.pending_failed_notification(product_id, current_price)
            if pending is None:
                continue
            event_type, failed_previous = pending
            decision = AlertDecision.from_event_type(event_type, failed_previous)

        stored_previous = decision.previous_price or current_price

        try:
            client.send_message(format_price_alert(summary, decision))
            database.record_notification_attempt(
                run_id,
                product_id,
                current_price,
                stored_previous,
                status="SENT",
                event_type=decision.event_type,
            )
            LOGGER.info("Notificacao Telegram enviada para %s", product_id)
        except (TelegramError, ValueError) as exc:
            message = str(exc)
            database.record_notification_attempt(
                run_id,
                product_id,
                current_price,
                stored_previous,
                status="FAILED",
                event_type=decision.event_type,
                error_message=message,
            )
            database.record_error(
                run_id,
                f"telegram/{product_id}",
                message,
            )
            LOGGER.warning(
                "Falha ao enviar notificacao Telegram para %s: %s",
                product_id,
                message,
            )


def _notify_run_summary(
    database: RadarDatabase,
    run_id: int,
    summaries: tuple[ProductSummary, ...],
    settings: Settings,
) -> str | None:
    try:
        _telegram_client(settings).send_message(
            format_run_summary(summaries, datetime.now().astimezone())
        )
        LOGGER.info("Resumo da execucao enviado ao Telegram")
        return None
    except (TelegramError, ValueError) as exc:
        message = str(exc)
        database.record_error(run_id, "telegram/resumo", message)
        LOGGER.warning("Falha ao enviar resumo da execucao ao Telegram: %s", message)
        return message


def run(
    input_file: str | Path | None = None,
    *,
    simulate: bool = False,
    with_shipping: bool = False,
    notify_summary: bool = False,
    product_ids: set[str] | None = None,
) -> Path:
    settings = get_settings(input_file)
    if simulate and with_shipping:
        raise ValueError("--simulate e --with-shipping nao podem ser usados juntos")
    if with_shipping and settings.destination_postal_code is None:
        raise ValueError("--with-shipping exige CEP_ENTREGA valido no arquivo .env")
    settings.ensure_directories()
    _configure_logging(settings.logs_dir / "radar.log")
    database = RadarDatabase(settings.database_file)
    database.initialize()
    mode = "SIMULATION" if simulate else ("REAL_SHIPPING" if with_shipping else "REAL")
    run_id = database.start_run(mode, settings.input_file)

    try:
        LOGGER.info("Lendo e validando a planilha: %s", settings.input_file)
        catalog = load_catalog(settings.input_file)
        selected_ids = {value.upper() for value in product_ids} if product_ids else None
        known_ids = {product.product_id for product in catalog.active_products}
        if selected_ids:
            unknown_ids = selected_ids - known_ids
            if unknown_ids:
                raise ValueError(
                    "Produto(s) ativo(s) nao encontrado(s): " + ", ".join(sorted(unknown_ids))
                )

        selected_products = tuple(
            product
            for product in catalog.active_products
            if selected_ids is None or product.product_id in selected_ids
        )
        selected_catalog = type(catalog)(
            products=selected_products,
            links=tuple(
                link
                for link in catalog.links
                if any(product.product_id == link.product_id for product in selected_products)
            ),
        )

        include_shipping = simulate or with_shipping
        previous_historical_lows = {
            product.product_id: database.historical_low(
                product.product_id,
                include_shipping=include_shipping,
                mode=mode,
            )
            for product in selected_products
        }
        previous_stock_states = {
            product.product_id: database.previous_product_stock_state(
                product.product_id,
                run_id,
                mode=mode,
            )
            for product in selected_products
        }

        if simulate:
            observations = generate_simulated_observations(selected_catalog)
        else:
            if with_shipping:
                address = resolve_postal_code(
                    settings.destination_postal_code or "",
                    timeout=settings.request_timeout_seconds,
                    retries=settings.request_retries,
                )
                LOGGER.info("Destino de entrega confirmado: %s", address.formatted)
            observations = collect_real_observations(
                selected_catalog,
                timeout=settings.request_timeout_seconds,
                retries=settings.request_retries,
                destination_postal_code=(
                    settings.destination_postal_code if with_shipping else None
                ),
            )
        saved_count = database.save_observations(run_id, observations)

        for observation in observations:
            if observation.status != "OK":
                message = observation.error_message or observation.status
                LOGGER.warning(
                    "%s / %s: %s", observation.product_id, observation.store, message
                )
                database.record_error(
                    run_id,
                    f"{observation.product_id}/{observation.store}",
                    f"{observation.status}: {message}",
                )

        summaries = tuple(
            ProductSummary(
                product=product,
                best_offer=choose_best_offer(
                    observations,
                    product.product_id,
                    require_shipping=include_shipping,
                ),
                previous_best_total=database.previous_best_total(
                    product.product_id,
                    run_id,
                    include_shipping=include_shipping,
                    mode=mode,
                ),
                historical_low=database.historical_low(
                    product.product_id,
                    include_shipping=include_shipping,
                    mode=mode,
                ),
                include_shipping=include_shipping,
            )
            for product in selected_products
        )
        analytics_by_product = {
            product.product_id: analyze_product(
                product,
                database.price_history(
                    product.product_id,
                    include_shipping=include_shipping,
                    mode=mode,
                    current_run_id=run_id,
                ),
                database.price_history(
                    product.product_id,
                    include_shipping=include_shipping,
                    mode=mode,
                    current_run_id=run_id,
                    by_store=True,
                ),
            )
            for product in selected_products
        }
        report_path = write_report(
            settings.reports_dir,
            summaries,
            observations=observations,
            mode=mode,
            include_shipping=include_shipping,
            analytics_by_product=analytics_by_product,
        )
        summary_error = None
        if notify_summary and settings.telegram_notifications_enabled:
            summary_error = _notify_run_summary(database, run_id, summaries, settings)
        if (
            not simulate
            and with_shipping
            and settings.telegram_notifications_enabled
        ):
            _notify_relevant_changes(
                database,
                run_id,
                summaries,
                previous_historical_lows,
                previous_stock_states,
                settings,
            )
        database.finish_run(
            run_id,
            status="SUCCESS",
            products_count=len(selected_products),
            links_count=len(selected_catalog.links),
            observations_count=saved_count,
        )
        LOGGER.info("Execucao concluida. Relatorio: %s", report_path)
        if summary_error is not None:
            raise SummaryNotificationError(
                "Coleta concluida, mas o resumo do Telegram nao foi entregue"
            )
        return report_path
    except SummaryNotificationError:
        raise
    except Exception as exc:
        LOGGER.exception("Falha na execucao")
        database.record_error(run_id, "pipeline", str(exc))
        database.finish_run(run_id, status="FAILED", error_message=str(exc))
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Radar de precos local")
    parser.add_argument(
        "--input",
        default=None,
        help="Caminho da planilha (padrao: produtos.xlsx)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Usa ofertas simuladas para validar a Etapa 1",
    )
    parser.add_argument(
        "--product",
        action="append",
        default=None,
        help="Coleta apenas o produto_id informado; pode ser repetido",
    )
    parser.add_argument(
        "--with-shipping",
        action="store_true",
        help="Consulta o CEP do .env e compara produto + frete",
    )
    parser.add_argument(
        "--notify-summary",
        action="store_true",
        help="Envia ao Telegram um resumo ao concluir a coleta",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Envia uma mensagem de teste usando as credenciais do .env",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = get_settings(args.input)
        lock_path = settings.database_file.parent / "radar.lock"
        with SingleInstanceLock(lock_path):
            if args.test_telegram:
                settings.ensure_directories()
                _configure_logging(settings.logs_dir / "radar.log")
                message_id = _telegram_client(settings).send_message(
                    format_test_message()
                )
                suffix = f" (mensagem {message_id})" if message_id is not None else ""
                print(f"Mensagem de teste enviada ao Telegram{suffix}.")
                return 0
            report_path = run(
                args.input,
                simulate=args.simulate,
                with_shipping=args.with_shipping,
                notify_summary=args.notify_summary,
                product_ids=set(args.product) if args.product else None,
            )
    except AlreadyRunningError as exc:
        print(f"IGNORADO: {exc}")
        return 0
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    print(f"Relatorio gerado: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
