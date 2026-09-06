from __future__ import annotations

from django.db.models import Count
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet

from monitoring.models import MonitoredProduct
from monitoring.permissions import HasTenant, IsSameTenant
from monitoring.serializers import MonitoredProductSerializer


class MeView(APIView):
    permission_classes = (HasTenant,)

    def get(self, request):
        identity = getattr(request.user, "telegram_identity", None)
        return Response(
            {
                "username": request.user.username,
                "tenant": {
                    "id": str(request.user.tenant_id),
                    "name": request.user.tenant.name,
                },
                "telegram": {
                    "connected": identity is not None,
                    "display_name": identity.display_name if identity else "",
                },
            }
        )


class MonitoredProductViewSet(ReadOnlyModelViewSet):
    serializer_class = MonitoredProductSerializer
    permission_classes = (HasTenant, IsSameTenant)

    def get_queryset(self):
        return (
            MonitoredProduct.objects.for_user(self.request.user)
            .annotate(source_count=Count("sources"))
            .order_by("name", "id")
        )
