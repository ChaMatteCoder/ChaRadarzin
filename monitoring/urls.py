from django.urls import path

from monitoring import views


urlpatterns = [
    path("", views.landing, name="landing"),
    path("painel/", views.dashboard, name="dashboard"),
    path("painel/configuracoes/", views.delivery_settings, name="delivery-settings"),
    path("painel/produtos/novo/", views.product_link_preview_create, name="product-new"),
    path(
        "painel/produtos/previews/<uuid:preview_id>/confirmar/",
        views.product_preview_confirm,
        name="product-preview-confirm",
    ),
    path("painel/produtos/<uuid:product_id>/", views.product_detail, name="product-detail-page"),
    path("painel/produtos/<uuid:product_id>/editar/", views.product_edit, name="product-edit"),
    path(
        "painel/produtos/<uuid:product_id>/pausar/",
        views.product_toggle_pause,
        name="product-toggle-pause",
    ),
    path(
        "painel/produtos/<uuid:product_id>/excluir/",
        views.product_archive,
        name="product-archive",
    ),
    path(
        "painel/produtos/<uuid:product_id>/fontes/nova/",
        views.product_link_preview_create,
        name="source-new",
    ),
    path(
        "painel/produtos/<uuid:product_id>/fontes/<uuid:source_id>/editar/",
        views.source_edit,
        name="source-edit",
    ),
    path(
        "painel/produtos/<uuid:product_id>/fontes/<uuid:source_id>/alternar/",
        views.source_toggle,
        name="source-toggle",
    ),
]
