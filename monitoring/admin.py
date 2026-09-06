from __future__ import annotations

from django.contrib import admin

from monitoring.models import (
    CollectorError,
    CollectionBatch,
    AlertEvent,
    AlertRule,
    CollectionJob,
    CollectionJobAttempt,
    CollectionWorkerLease,
    LegacyImportBatch,
    MonitoredProduct,
    MonitoringRun,
    NotificationDelivery,
    OfferSource,
    PriceObservation,
    ProductLinkPreview,
    SharedOffer,
    SharedOfferObservation,
)


admin.site.register(MonitoredProduct)
admin.site.register(OfferSource)
admin.site.register(MonitoringRun)
admin.site.register(PriceObservation)
admin.site.register(CollectorError)
admin.site.register(CollectionBatch)
admin.site.register(AlertRule)
admin.site.register(AlertEvent)
admin.site.register(CollectionJob)
admin.site.register(CollectionJobAttempt)
admin.site.register(CollectionWorkerLease)
admin.site.register(SharedOffer)
admin.site.register(SharedOfferObservation)
admin.site.register(NotificationDelivery)
admin.site.register(LegacyImportBatch)


@admin.register(ProductLinkPreview)
class ProductLinkPreviewAdmin(admin.ModelAdmin):
    list_display = ("store", "tenant", "status", "created_at", "expires_at")
    list_filter = ("store", "status")
    search_fields = ("canonical_url", "extracted_title", "tenant__name")
    readonly_fields = [field.name for field in ProductLinkPreview._meta.fields]
