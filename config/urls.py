from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_GET
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from django.conf import settings
from django.conf.urls.static import static


@require_GET
def healthcheck(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", healthcheck, name="healthcheck"),
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
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    
]

if settings.DEBUG and not settings.USE_FTP_MEDIA_STORAGE:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
