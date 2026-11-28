"""Armory API routing."""

from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("armory/firearms", views.FirearmViewSet, basename="armory-firearm")
router.register("armory/ammunition", views.AmmunitionViewSet, basename="armory-ammunition")
router.register("armory/assignments", views.AssignmentViewSet, basename="armory-assignment")
router.register("armory/transactions", views.TransactionViewSet, basename="armory-transaction")

urlpatterns = [
    path("armory/dashboard/", views.DashboardView.as_view(), name="armory-dashboard"),
    path("armory/my/", views.MyArmoryView.as_view(), name="armory-my"),
    path("armory/personnel/", views.PersonnelLookupView.as_view(), name="armory-personnel"),
    path(
        "armory/sync/snapshot/",
        views.ArmorySyncSnapshotView.as_view(),
        name="armory-sync-snapshot",
    ),
    path("armory/missions/", views.ApprovedMissionView.as_view(), name="armory-missions"),
    path(
        "armory/mission-eligibility/",
        views.MissionEligibilityView.as_view(),
        name="armory-mission-eligibility",
    ),
    path("armory/issuances/", views.IssueView.as_view(), name="armory-issue"),
    path("armory/returns/", views.ReturnView.as_view(), name="armory-return"),
    path("armory/receipts/<int:pk>/", views.ReceiptView.as_view(), name="armory-receipt"),
    path(
        "armory/reports/<slug:report_slug>/",
        views.ReportView.as_view(),
        name="armory-report",
    ),
]
urlpatterns += router.urls
