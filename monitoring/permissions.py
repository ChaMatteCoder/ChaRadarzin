from __future__ import annotations

from rest_framework.permissions import BasePermission


class HasTenant(BasePermission):
    message = "A conta autenticada ainda nao possui um tenant."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.tenant_id is not None
        )


class IsSameTenant(BasePermission):
    message = "Este recurso pertence a outra conta."

    def has_object_permission(self, request, view, obj) -> bool:
        return bool(
            request.user.is_authenticated
            and request.user.tenant_id is not None
            and getattr(obj, "tenant_id", None) == request.user.tenant_id
        )
