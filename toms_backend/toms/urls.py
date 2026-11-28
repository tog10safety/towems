"""Root URL configuration for the TOWEMS API."""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.csp import csp_report

urlpatterns = [
    path("api/csp-report/", csp_report, name="csp-report"),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.ops.urls")),
    path("api/", include("apps.command.urls")),
    path("api/", include("apps.messaging.urls")),
    path("api/", include("apps.armory.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
