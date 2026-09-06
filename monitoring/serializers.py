from __future__ import annotations

from rest_framework import serializers

from monitoring.models import MonitoredProduct


class MonitoredProductSerializer(serializers.ModelSerializer):
    source_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = MonitoredProduct
        fields = (
            "id",
            "name",
            "exact_model",
            "variant",
            "target_price_cents",
            "baseline_total_cents",
            "baseline_observed_at",
            "preferred_payment_method",
            "status",
            "source_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
