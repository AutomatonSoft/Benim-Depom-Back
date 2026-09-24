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


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", healthcheck, name="healthcheck"),
    path("api/v1/", include("apps.common.urls")),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/manager/users/", include("apps.accounts.manager_urls")),
    path("api/v1/catalog/", include("apps.catalog.urls")),
    path("api/v1/products/", include("apps.products.urls")),
    path("api/v1/", include("apps.moderation.urls")),
    path(
        "api/v1/notifications/",
        include("apps.notifications.urls"),
    ),
    path("api/v1/manager/eans/", include("apps.ean.urls")),
    path("api/v1/orchestrator/", include("apps.orchestrator.urls")),
    path(
        "api/demo/otto-form/",
        TemplateView.as_view(template_name="otto_form_demo.html"),
        name="otto-form-demo",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
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
