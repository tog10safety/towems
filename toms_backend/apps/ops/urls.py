from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.ops import feedback_views, hazard_views, inventory_views, verification_views, views

router = DefaultRouter()
router.register("missions", views.MissionViewSet, basename="mission")
router.register("risk-register", views.RiskRegisterViewSet, basename="risk-register")
router.register("units", views.UnitViewSet, basename="unit")
router.register("airfields", views.AirfieldViewSet, basename="airfield")
router.register("inventory/categories", views.InventoryCategoryViewSet, basename="inventory-category")
router.register("inventory/items", views.InventoryItemViewSet, basename="inventory-item")
router.register("inventory/air-assets", views.AirAssetViewSet, basename="air-asset")
router.register(
    "inventory/transactions", views.InventoryTransactionViewSet, basename="inventory-transaction"
)
router.register(
    "inventory/requests", views.InventoryRequestViewSet, basename="inventory-request"
)
router.register("vehicles", views.VehicleViewSet, basename="vehicle")
router.register("trips", views.VehicleTripViewSet, basename="trip")
router.register("aor", views.AreaOfResponsibilityViewSet, basename="aor")
router.register(
    "operational-mapping",
    views.OperationalMapFeatureViewSet,
    basename="operational-mapping",
)
router.register(
    "admin-areas", views.AdministrativeAreaViewSet, basename="admin-area"
)
router.register("notifications", views.NotificationViewSet, basename="notification")
router.register("audit", views.AuditLogViewSet, basename="audit")

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("gis/mgrs/", views.MGRSConvertView.as_view(), name="gis-mgrs"),
    path("gis/air-eta/", views.AirEtaEstimateView.as_view(), name="gis-air-eta"),
    path(
        "gis/operational-picture/",
        views.OperationalPictureView.as_view(),
        name="gis-operational-picture",
    ),
    path("gis/search/", views.GeocodeSearchView.as_view(), name="gis-search"),
    path("gis/places/", views.PlaceSearchView.as_view(), name="gis-places"),
    path("gis/pin/", views.PinLocationView.as_view(), name="gis-pin"),
    path("map/config/", views.MapConfigView.as_view(), name="map-config"),
    path("reports/<str:report>/", views.ReportView.as_view(), name="report"),
    path("health/", views.HealthView.as_view(), name="health"),
    path(
        "mission-orders/verify/<str:token>/",
        verification_views.MissionOrderVerifyView.as_view(),
        name="mission-order-verify",
    ),
    # Hazard library (legacy hazard_registry): maintained by the Unit Safety
    # Officer, read-only for command and operations posts, hidden from others.
    # Inventory: the Logistics Officer's spreadsheet workflow and the
    # statistics block above the register. Reads use the same unit and
    # controlled-item scope as the register itself.
    path("inventory/stats/", inventory_views.InventoryStatsView.as_view(), name="inventory-stats"),
    path(
        "inventory/fuel/transfer/",
        inventory_views.FuelTransferView.as_view(),
        name="inventory-fuel-transfer",
    ),
    # Where drums may be sent: the mother Wing's AOR coverage only.
    path(
        "inventory/fuel/destinations/",
        inventory_views.FuelDestinationView.as_view(),
        name="inventory-fuel-destinations",
    ),
    path(
        "inventory/template/",
        inventory_views.InventoryTemplateView.as_view(),
        name="inventory-template",
    ),
    path(
        "inventory/export/",
        inventory_views.InventoryExportView.as_view(),
        name="inventory-export",
    ),
    path(
        "inventory/import/",
        inventory_views.InventoryImportView.as_view(),
        name="inventory-import",
    ),
    # The airports a bulk Jet A1 store may sit on: the caller's own AOR only.
    path(
        "inventory/fuel-locations/",
        inventory_views.FuelLocationView.as_view(),
        name="inventory-fuel-locations",
    ),
    path("hazards/", hazard_views.HazardListView.as_view(), name="hazard-list"),
    path(
        "hazards/template/",
        hazard_views.HazardTemplateView.as_view(),
        name="hazard-template",
    ),
    path(
        "hazards/export/",
        hazard_views.HazardExportView.as_view(),
        name="hazard-export",
    ),
    path(
        "hazards/import/",
        hazard_views.HazardImportView.as_view(),
        name="hazard-import",
    ),
    path(
        "hazards/umrr/",
        hazard_views.HazardUMRRView.as_view(),
        name="hazard-umrr",
    ),
    path(
        "hazards/<int:pk>/",
        hazard_views.HazardDetailView.as_view(),
        name="hazard-detail",
    ),
    # Post-mission feedback: tagged personnel submit, the safety office audits.
    path(
        "feedback/requests/",
        feedback_views.FeedbackRequestsView.as_view(),
        name="feedback-requests",
    ),
    path(
        "feedback/requests/<int:mrm_id>/",
        feedback_views.FeedbackSubmitView.as_view(),
        name="feedback-submit",
    ),
    path(
        "feedback/audit/",
        feedback_views.FeedbackAuditView.as_view(),
        name="feedback-audit",
    ),
    path(
        "feedback/audit/<int:pk>/review/",
        feedback_views.FeedbackReviewView.as_view(),
        name="feedback-review",
    ),
    path(
        "feedback/audit/<int:pk>/adopt/",
        feedback_views.FeedbackAdoptView.as_view(),
        name="feedback-adopt",
    ),
    path("", include(router.urls)),
]
