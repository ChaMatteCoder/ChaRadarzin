from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import forms

from monitoring.models import MonitoredProduct, OfferSource
from tenancy.models import DeliveryProfile, PaymentMethod


def _clean_text(value: str, *, field_label: str, max_length: int) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise forms.ValidationError(f"Informe {field_label}.")
    if len(cleaned) > max_length:
        raise forms.ValidationError(f"{field_label.capitalize()} excede {max_length} caracteres.")
    return cleaned


def parse_target_price_to_cents(value: str) -> int | None:
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("R$", "").replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise forms.ValidationError("Informe um preço-alvo válido, como 899,90.") from exc
    if amount <= 0 or amount > Decimal("99999999.99"):
        raise forms.ValidationError("O preço-alvo deve ser maior que zero e menor que R$ 100 milhões.")
    return int(amount * 100)


def cents_as_input(value: int | None) -> str:
    if value is None:
        return ""
    return f"{Decimal(value) / 100:.2f}".replace(".", ",")


class ExactProductLinkForm(forms.Form):
    url = forms.URLField(
        label="Link exato do produto",
        max_length=2048,
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://www.amazon.com.br/dp/...",
                "autocomplete": "url",
                "inputmode": "url",
            }
        ),
    )


class ProductConfirmationForm(forms.Form):
    name = forms.CharField(label="Produto", max_length=255)
    exact_model = forms.CharField(label="Modelo exato", max_length=255)
    variant = forms.CharField(
        label="Variante, capacidade ou tamanho",
        max_length=255,
        help_text="Ex.: 1 TB / SATA, 27 polegadas / QHD ou 8 GB / Preto.",
    )
    expected_seller = forms.CharField(label="Vendedor esperado", max_length=255)
    preferred_payment_method = forms.ChoiceField(
        label="Forma de pagamento",
        choices=PaymentMethod.choices,
    )
    target_price = forms.CharField(
        label="Preço-alvo opcional",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "899,90", "inputmode": "decimal"}),
    )

    def clean_name(self):
        return _clean_text(self.cleaned_data["name"], field_label="o nome do produto", max_length=255)

    def clean_exact_model(self):
        return _clean_text(self.cleaned_data["exact_model"], field_label="o modelo exato", max_length=255)

    def clean_variant(self):
        return _clean_text(self.cleaned_data["variant"], field_label="a variante", max_length=255)

    def clean_expected_seller(self):
        return _clean_text(self.cleaned_data["expected_seller"], field_label="o vendedor", max_length=255)

    def clean_target_price(self):
        return parse_target_price_to_cents(self.cleaned_data["target_price"])


class ProductEditForm(forms.ModelForm):
    target_price = forms.CharField(
        label="Preço-alvo opcional",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "899,90", "inputmode": "decimal"}),
    )

    class Meta:
        model = MonitoredProduct
        fields = ("name", "exact_model", "variant", "preferred_payment_method")
        labels = {
            "name": "Produto",
            "exact_model": "Modelo exato",
            "variant": "Variante, capacidade ou tamanho",
            "preferred_payment_method": "Forma de pagamento",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["target_price"].initial = cents_as_input(self.instance.target_price_cents)

    def clean_target_price(self):
        return parse_target_price_to_cents(self.cleaned_data["target_price"])

    def save(self, commit=True):
        product = super().save(commit=False)
        product.target_price_cents = self.cleaned_data["target_price"]
        if commit:
            product.save()
        return product


class SourceEditForm(forms.ModelForm):
    class Meta:
        model = OfferSource
        fields = ("expected_seller", "expected_variant")
        labels = {
            "expected_seller": "Vendedor esperado",
            "expected_variant": "Variante esperada neste link",
        }


class DeliveryProfileForm(forms.ModelForm):
    postal_code = forms.CharField(
        label="CEP para cálculo de frete",
        max_length=12,
        widget=forms.TextInput(
            attrs={"placeholder": "01001-000", "inputmode": "numeric", "autocomplete": "postal-code"}
        ),
        help_text="Somente o CEP é armazenado; não guardamos endereço completo.",
    )

    class Meta:
        model = DeliveryProfile
        fields = (
            "postal_code",
            "preferred_payment_method",
            "minimum_price_drop_percent",
            "alert_price_increase",
        )
        labels = {
            "preferred_payment_method": "Forma de pagamento preferida",
            "minimum_price_drop_percent": "Queda mínima para alerta (%)",
            "alert_price_increase": "Avisar também sobre aumento de preço",
        }
        widgets = {
            "minimum_price_drop_percent": forms.NumberInput(
                attrs={"min": "0.10", "max": "100", "step": "0.10", "inputmode": "decimal"}
            ),
        }

    def clean_postal_code(self):
        postal_code = re.sub(r"\D", "", self.cleaned_data["postal_code"])
        if len(postal_code) != 8 or postal_code == "00000000":
            raise forms.ValidationError("Informe um CEP brasileiro válido com oito dígitos.")
        return postal_code

    def clean_minimum_price_drop_percent(self):
        value = self.cleaned_data["minimum_price_drop_percent"]
        if value < Decimal("0.10") or value > Decimal("100"):
            raise forms.ValidationError("Use um percentual entre 0,10% e 100%.")
        return value
