from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re

from app.analytics import ProductAnalytics, render_history_svg
from app.models import OfferObservation, ProductSummary


def _money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _percentage(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%".replace(".", ",")


def _frequency(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%".replace(".", ",")


def _cell(value: str | None) -> str:
    return (value or "—").replace("|", "\\|").replace("\n", " ")


def build_markdown_report(
    summaries: tuple[ProductSummary, ...],
    generated_at: datetime,
    *,
    observations: tuple[OfferObservation, ...] = (),
    mode: str = "SIMULATION",
    include_shipping: bool = True,
    analytics_by_product: dict[str, ProductAnalytics] | None = None,
    chart_files: dict[str, str] | None = None,
) -> str:
    is_simulation = mode == "SIMULATION"
    lines = [
        f"# ChaRadarzin — {'Relatorio simulado' if is_simulation else 'Coleta real'}",
        "",
        f"Gerado em: {generated_at.astimezone().strftime('%d/%m/%Y %H:%M:%S %Z')}",
        "",
        (
            "> Os valores abaixo sao simulados e servem apenas para validar a estrutura da Etapa 1."
            if is_simulation
            else (
                "> Precos e fretes coletados das lojas para o CEP configurado."
                if include_shipping
                else "> Precos coletados das paginas reais. O frete nao foi solicitado nesta execucao."
            )
        ),
        "",
        "| Produto | Melhor loja | Produto | Frete | Total | Entrega | Menor historico | Variacao | Situacao |",
        "|---|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for summary in summaries:
        offer = summary.best_offer
        if offer is None:
            lines.append(
                f"| {summary.product.name} ({summary.product.exact_model}) | — | — | — | — | — | "
                f"{_money(summary.historical_low)} | — | {summary.situation} |"
            )
            continue
        if offer.delivery_min_days is not None and offer.delivery_max_days is not None:
            delivery = f"{offer.delivery_min_days} a {offer.delivery_max_days} dias"
        elif offer.delivery_max_days is not None:
            delivery = f"ate {offer.delivery_max_days} dias"
        elif offer.delivery_min_days is not None:
            delivery = f"a partir de {offer.delivery_min_days} dias"
        else:
            delivery = "—"
        lines.append(
            f"| {summary.product.name} ({summary.product.exact_model}) "
            f"| [{offer.store}]({offer.url}) | {_money(offer.product_price)} "
            f"| {_money(offer.shipping_price)} | {_money(offer.total_price)} | {delivery} "
            f"| {_money(summary.historical_low)} | {_percentage(summary.daily_change)} "
            f"| {summary.situation} |"
        )
    if analytics_by_product:
        lines.extend(["", "## Analises historicas", ""])
        for summary in summaries:
            analysis = analytics_by_product.get(summary.product.product_id)
            if analysis is None:
                continue
            lines.extend(
                [
                    f"### {summary.product.name} ({summary.product.exact_model})",
                    "",
                ]
            )
            chart_file = (chart_files or {}).get(summary.product.product_id)
            if chart_file:
                lines.extend(
                    [
                        f"![Historico de preco total de {summary.product.name}]({chart_file})",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        "> Grafico ainda nao exibido: sao necessarios pelo menos 8 dias com preco valido.",
                        "",
                    ]
                )
            if analysis.sample_count == 0:
                lines.extend(["Nenhum preco historico valido para analisar.", ""])
                continue
            period = (
                f"{analysis.period_start.strftime('%d/%m/%Y')} a "
                f"{analysis.period_end.strftime('%d/%m/%Y')}"
            )
            atypical = (
                "Dados insuficientes (exige 7 dias anteriores)"
                if analysis.atypical_promotion is None
                else (
                    f"Sim — desconto de {_frequency(analysis.atypical_discount)} contra a mediana recente"
                    if analysis.atypical_promotion
                    else (
                        f"Nao — preco atual {_frequency(abs(analysis.atypical_discount or Decimal('0')))} "
                        + (
                            "abaixo da mediana recente"
                            if (analysis.atypical_discount or Decimal('0')) >= 0
                            else "acima da mediana recente"
                        )
                    )
                )
            )
            trend = analysis.trend
            if analysis.trend_per_day is not None:
                trend += f" ({_percentage(analysis.trend_per_day)} ao dia)"
            lines.extend(
                [
                    f"Periodo analisado: **{period}** ({analysis.sample_count} dias com oferta valida).",
                    "",
                    "| Metrica | Resultado |",
                    "|---|---:|",
                    f"| Menor preco | {_money(analysis.minimum)} |",
                    f"| Maior preco | {_money(analysis.maximum)} |",
                    f"| Media | {_money(analysis.average)} |",
                    f"| Mediana | {_money(analysis.median)} |",
                    f"| Media movel de 7 dias | {_money(analysis.moving_average_7)} |",
                    f"| Media movel de 30 dias | {_money(analysis.moving_average_30)} |",
                    f"| Frequencia abaixo do preco-alvo | {_frequency(analysis.below_target_frequency)} |",
                    f"| Distancia atual do menor preco | {_percentage(analysis.distance_from_low)} |",
                    f"| Tendencia | {trend} |",
                    f"| Promocao atipica | {atypical} |",
                    "",
                    f"**Previsao:** {analysis.forecast_status}"
                    + (
                        f". Valor estimado: {_money(analysis.forecast_7_days)}."
                        if analysis.forecast_7_days is not None
                        else "."
                    ),
                    "",
                ]
            )
            if analysis.store_volatility:
                lines.extend(
                    [
                        "#### Volatilidade por loja",
                        "",
                        "| Loja | Dias | Media | Desvio-padrao | Volatilidade |",
                        "|---|---:|---:|---:|---:|",
                    ]
                )
                for store in analysis.store_volatility:
                    lines.append(
                        f"| {_cell(store.store)} | {store.samples} | {_money(store.average)} "
                        f"| {_money(store.standard_deviation)} | {_frequency(store.coefficient)} |"
                    )
                lines.append("")
            if len(analysis.payment_stats) >= 2:
                lines.extend(
                    [
                        "#### Comparacao por pagamento",
                        "",
                        "| Pagamento | Dias | Media da melhor oferta |",
                        "|---|---:|---:|",
                    ]
                )
                for payment in analysis.payment_stats:
                    lines.append(
                        f"| {_cell(payment.payment_method)} | {payment.samples} | {_money(payment.average)} |"
                    )
                lines.append("")
            else:
                available = (
                    analysis.payment_stats[0].payment_method
                    if analysis.payment_stats
                    else "nenhuma"
                )
                lines.extend(
                    [
                        f"**PIX versus cartao:** indisponivel; o historico possui somente {available}.",
                        "",
                    ]
                )
            if analysis.black_friday_discount is None:
                lines.extend(
                    [
                        "**Black Friday:** dados insuficientes; sao necessarias pelo menos duas temporadas com amostras antes e durante o evento.",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"**Black Friday:** desconto mediano estimado em {_frequency(analysis.black_friday_discount)}, com base em {analysis.black_friday_seasons} temporadas.",
                        "",
                    ]
                )
    lines.extend(
        [
            "",
            "## Criterio de comparacao",
            "",
            (
                "Somente ofertas disponiveis, com status OK e frete conhecido entram na comparacao. "
                "O menor custo total vence; em caso de empate, vence o menor prazo maximo de entrega."
                if include_shipping
                else "Como o frete nao foi solicitado, somente ofertas disponiveis e com status OK "
                "entram na comparacao pelo preco do produto."
            ),
            "",
        ]
    )
    if observations:
        lines.extend(
            [
                "## Diagnostico dos links",
                "",
                "| Produto | Loja | Vendedor encontrado | Preco | Status | Detalhe |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for observation in observations:
            lines.append(
                f"| {observation.product_id} | [{_cell(observation.store)}]({observation.url}) "
                f"| {_cell(observation.seller)} | {_money(observation.product_price)} "
                f"| {observation.status} | {_cell(observation.error_message)} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_report(
    reports_dir: str | Path,
    summaries: tuple[ProductSummary, ...],
    generated_at: datetime | None = None,
    *,
    observations: tuple[OfferObservation, ...] = (),
    mode: str = "SIMULATION",
    include_shipping: bool = True,
    analytics_by_product: dict[str, ProductAnalytics] | None = None,
) -> Path:
    timestamp = generated_at or datetime.now().astimezone()
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp_label = timestamp.strftime('%Y%m%d_%H%M%S')
    report_path = directory / f"relatorio_{timestamp_label}.md"
    chart_files: dict[str, str] = {}
    if analytics_by_product:
        products = {summary.product.product_id: summary.product for summary in summaries}
        for product_id, analysis in analytics_by_product.items():
            product = products.get(product_id)
            if product is None or not analysis.chart_ready:
                continue
            safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", product_id)
            chart_name = f"historico_{safe_id}_{timestamp_label}.svg"
            (directory / chart_name).write_text(
                render_history_svg(analysis, product), encoding="utf-8"
            )
            chart_files[product_id] = chart_name
    content = build_markdown_report(
        summaries,
        timestamp,
        observations=observations,
        mode=mode,
        include_shipping=include_shipping,
        analytics_by_product=analytics_by_product,
        chart_files=chart_files,
    )
    report_path.write_text(content, encoding="utf-8")
    (directory / "ultimo_relatorio.md").write_text(content, encoding="utf-8")
    return report_path
