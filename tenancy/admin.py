from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from tenancy.models import BetaAccessGrant, DeliveryProfile, TelegramIdentity, Tenant, User


@admin.register(User)
class ChaRadarzinUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("ChaRadarzin", {"fields": ("tenant",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("ChaRadarzin", {"fields": ("tenant",)}),
    )
    list_select_related = ("tenant",)


admin.site.register(Tenant)
admin.site.register(TelegramIdentity)
admin.site.register(DeliveryProfile)
admin.site.register(BetaAccessGrant)
