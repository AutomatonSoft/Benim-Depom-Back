from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views import defaults
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


@require_GET
def healthcheck(request):
    return JsonResponse({"status": "ok"})


def _api_error_response(request, status_code, detail):
    if request.path_info.startswith("/api/"):
        return JsonResponse({"detail": detail}, status=status_code)
    return None


def handler400(request, exception):
    return _api_error_response(request, 400, "Bad request.") or defaults.bad_request(
        request, exception
    )


def handler403(request, exception):
    return _api_error_response(
        request, 403, "You do not have permission to perform this action."
    ) or defaults.permission_denied(request, exception)


def handler404(request, exception):
    return _api_error_response(request, 404, "Not found.") or defaults.page_not_found(
        request, exception
    )


def handler500(request):
    return _api_error_response(
        request, 500, "Internal server error."
    ) or defaults.server_error(request)


def api_patterns(auth_urls):
    """Share unchanged endpoints while keeping each auth contract explicit."""
    return [
        path("health/", healthcheck, name="healthcheck"),
        path("", include("apps.common.urls")),
        path("auth/", include(auth_urls)),
        path("manager/users/", include("apps.accounts.manager_urls")),
        path("catalog/", include("apps.catalog.urls")),
        path("products/", include("apps.products.urls")),
        path("", include("apps.moderation.urls")),
        path("notifications/", include("apps.notifications.urls")),
        path("manager/eans/", include("apps.ean.urls")),
        path("orchestrator/", include("apps.orchestrator.urls")),
        path("ebay/", include("apps.marketplace.ebay.urls")),
    ]


v1_routes = [path("api/v1/", include(api_patterns("apps.accounts.v1_urls")))]
v2_routes = [
    path("api/v2/ebay-bulk-end/", include("apps.ebay_bulk_end.urls")),
    path(
        "api/v2/", include((api_patterns("apps.accounts.urls"), "api"), namespace="v2")
    )
]
schema_v1 = SpectacularAPIView.as_view(
    patterns=v1_routes, custom_settings={"VERSION": "1.0.0"}
)
schema_v2 = SpectacularAPIView.as_view(
    patterns=v2_routes, custom_settings={"VERSION": "2.0.0"}
)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/schema/", schema_v1, name="schema-v1"),
    path("api/v2/schema/", schema_v2, name="schema-v2"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="schema-v1"),
        name="swagger-v1",
    ),
    path(
        "api/v2/docs/",
        SpectacularSwaggerView.as_view(url_name="schema-v2"),
        name="swagger-v2",
    ),
    *v1_routes,
    *v2_routes,
    path(
        "api/demo/otto-form/",
        TemplateView.as_view(template_name="otto_form_demo.html"),
        name="otto-form-demo",
    ),
    path("api/schema/", schema_v1, name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/scalar/",
        TemplateView.as_view(template_name="scalar.html"),
        name="scalar-ui",
    ),
]

if settings.DEBUG and not settings.USE_FTP_MEDIA_STORAGE:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
