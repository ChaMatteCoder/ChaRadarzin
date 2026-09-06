from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.alerts import (
    BACK_IN_STOCK,
    NEW_HISTORICAL_LOW,
    PRICE_DROP,
    PRICE_INCREASE,
    TARGET_REACHED,
    AlertDecision,
    evaluate_alert,
)
from app.models import OfferObservation, Product, ProductSummary
from app.notifications import (
    TelegramClient,
    format_price_alert,
    format_run_summary,
    format_test_message,
)


PRICE_SCENARIOS = json.loads(
    (
        Path(__file__).resolve().parent
        / "fixtures"
        / "prices"
        / "alert_scenarios.json"
    ).read_text(encoding="utf-8")
)


def summary() -> ProductSummary:
    product = Product(
        product_id="TEST001",
        active=True,
        name="Monitor AOC & Gamer",
        exact_model="Q27G4F",
        target_price=Decimal("950"),
        payment_method="PIX",
    )
    offer = OfferObservation(
        observed_at=datetime.now().astimezone(),
        product_id="TEST001",
        store="Amazon",
        seller="Amazon.com.br",
        payment_method="PIX",
        product_price=Decimal("880"),
        shipping_price=Decimal("20"),
        delivery_min_days=None,
        delivery_max_days=3,
        in_stock=True,
        url="https://example.com/product?a=1&b=2",
        status="OK",
        parser_version="test",
    )
    return ProductSummary(
        product=product,
        best_offer=offer,
        previous_best_total=Decimal("1000"),
        historical_low=Decimal("900"),
        include_shipping=True,
    )


def summary_for_prices(current: str, previous: str | None) -> ProductSummary:
    item = summary()
    if item.best_offer is None:
        raise AssertionError("A fixture de notificacao precisa de uma oferta")
    return ProductSummary(
        product=replace(item.product, target_price=Decimal(PRICE_SCENARIOS["target"])),
        best_offer=replace(
            item.best_offer,
            product_price=Decimal(current),
            shipping_price=Decimal("0"),
        ),
        previous_best_total=Decimal(previous) if previous is not None else None,
        historical_low=Decimal(current),
        include_shipping=True,
    )


class TelegramNotificationTest(unittest.TestCase):
    def test_first_valid_offer_creates_baseline_without_price_alert(self) -> None:
        item = summary_for_prices(PRICE_SCENARIOS["target"], None)

        decision = evaluate_alert(
            item,
            previous_historical_low=None,
            previous_in_stock=None,
        )

        self.assertIsNone(decision)

    def test_price_fixture_protects_one_percent_threshold(self) -> None:
        baseline = PRICE_SCENARIOS["baseline"]
        irrelevant = evaluate_alert(
            summary_for_prices(PRICE_SCENARIOS["irrelevant_oscillation"], baseline),
            previous_historical_low=Decimal("900"),
            previous_in_stock=True,
        )
        relevant = evaluate_alert(
            summary_for_prices(PRICE_SCENARIOS["relevant_drop"], baseline),
            previous_historical_low=Decimal("900"),
            previous_in_stock=True,
        )

        self.assertIsNone(irrelevant)
        self.assertEqual(relevant.events, (PRICE_DROP,))

    def test_formats_new_low_alert_with_escaped_html(self) -> None:
        decision = AlertDecision(
            events=(NEW_HISTORICAL_LOW, PRICE_DROP, TARGET_REACHED),
            previous_price=Decimal("1000"),
        )
        message = format_price_alert(summary(), decision)
        self.assertIn("NOVO MENOR PREÇO", message)
        self.assertIn("Monitor AOC &amp; Gamer", message)
        self.assertIn("R$ 900,00", message)
        self.assertIn("Queda: 10,0%", message)
        self.assertIn("Preço-alvo: R$ 950,00", message)
        self.assertIn("Menor histórico: R$ 900,00", message)
        self.assertIn("Motivos:", message)
        self.assertIn("até 3 dias", message)
        self.assertIn("a=1&amp;b=2", message)

    def test_posts_utf8_json_to_send_message(self) -> None:
        captured: dict[str, object] = {}

        def requester(request: object, **kwargs: object) -> dict:
            captured.update(
                {
                    "url": request.full_url,
                    "method": request.get_method(),
                    "payload": json.loads(request.data.decode("utf-8")),
                    **kwargs,
                }
            )
            return {"ok": True, "result": {"message_id": 42}}

        client = TelegramClient(
            "123456789:" + "A" * 30,
            "987654321",
            requester=requester,
        )
        message_id = client.send_message(format_test_message())

        self.assertEqual(message_id, 42)
        self.assertTrue(str(captured["url"]).endswith("/sendMessage"))
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["payload"]["chat_id"], "987654321")
        self.assertEqual(captured["payload"]["parse_mode"], "HTML")

    def test_formats_successful_run_summary(self) -> None:
        message = format_run_summary(
            (summary(),),
            datetime(2026, 8, 27, 21, 5).astimezone(),
        )

        self.assertIn("RADAR ATUALIZADO", message)
        self.assertIn("Monitor AOC &amp; Gamer", message)
        self.assertIn("Total: <b>R$ 900,00</b>", message)
        self.assertIn("Abrir melhor oferta", message)
        self.assertIn("a=1&amp;b=2", message)

    def test_run_summary_reports_product_without_comparable_offer(self) -> None:
        item = summary()
        unavailable = ProductSummary(
            product=item.product,
            best_offer=None,
            previous_best_total=item.previous_best_total,
            historical_low=item.historical_low,
            include_shipping=True,
        )

        message = format_run_summary((unavailable,), datetime.now().astimezone())

        self.assertIn("Sem oferta comparável", message)

    def test_ignores_price_changes_below_one_percent(self) -> None:
        item = summary()
        item = ProductSummary(
            product=Product(
                product_id=item.product.product_id,
                active=True,
                name=item.product.name,
                exact_model=item.product.exact_model,
                target_price=Decimal("800"),
                payment_method="PIX",
            ),
            best_offer=item.best_offer,
            previous_best_total=Decimal("908"),
            historical_low=Decimal("850"),
            include_shipping=True,
        )

        decision = evaluate_alert(
            item,
            previous_historical_low=Decimal("850"),
            previous_in_stock=True,
        )

        self.assertIsNone(decision)

    def test_combines_relevant_rules_into_one_decision(self) -> None:
        decision = evaluate_alert(
            summary(),
            previous_historical_low=Decimal("950"),
            previous_in_stock=True,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(
            decision.events,
            (NEW_HISTORICAL_LOW, TARGET_REACHED, PRICE_DROP),
        )

    def test_alerts_at_exactly_one_percent_drop(self) -> None:
        item = summary()
        item = ProductSummary(
            product=Product(
                product_id=item.product.product_id,
                active=True,
                name=item.product.name,
                exact_model=item.product.exact_model,
                target_price=Decimal("800"),
                payment_method="PIX",
            ),
            best_offer=item.best_offer,
            previous_best_total=Decimal("909.0909090909090909090909091"),
            historical_low=Decimal("850"),
            include_shipping=True,
        )

        decision = evaluate_alert(
            item,
            previous_historical_low=Decimal("850"),
            previous_in_stock=True,
        )

        self.assertEqual(decision.events, (PRICE_DROP,))

    def test_detects_return_to_stock(self) -> None:
        item = summary()
        item = ProductSummary(
            product=item.product,
            best_offer=item.best_offer,
            previous_best_total=None,
            historical_low=Decimal("800"),
            include_shipping=True,
        )

        decision = evaluate_alert(
            item,
            previous_historical_low=Decimal("800"),
            previous_in_stock=False,
        )

        self.assertIsNotNone(decision)
        self.assertIn(BACK_IN_STOCK, decision.events)

    def test_significant_increase_is_optional(self) -> None:
        item = summary()
        item = ProductSummary(
            product=Product(
                product_id=item.product.product_id,
                active=True,
                name=item.product.name,
                exact_model=item.product.exact_model,
                target_price=Decimal("500"),
                payment_method="PIX",
            ),
            best_offer=item.best_offer,
            previous_best_total=Decimal("850"),
            historical_low=Decimal("800"),
            include_shipping=True,
        )

        disabled = evaluate_alert(
            item,
            previous_historical_low=Decimal("800"),
            previous_in_stock=True,
            alert_price_increase=False,
        )
        enabled = evaluate_alert(
            item,
            previous_historical_low=Decimal("800"),
            previous_in_stock=True,
            alert_price_increase=True,
        )

        self.assertIsNone(disabled)
        self.assertEqual(enabled.events, (PRICE_INCREASE,))


if __name__ == "__main__":
    unittest.main()
