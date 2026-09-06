from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from monitoring.api import MeView, MonitoredProductViewSet


router = DefaultRouter()
router.register("produtos", MonitoredProductViewSet, basename="product")

urlpatterns = [
    path("me/", MeView.as_view(), name="api-me"),
    path("", include(router.urls)),
]
