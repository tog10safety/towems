from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.command import views

router = DefaultRouter()
router.register("appointments", views.UnitAppointmentViewSet, basename="appointment")
router.register(
    "acting-appointments", views.ActingAppointmentViewSet, basename="acting-appointment"
)
router.register("offices", views.OfficeSectionViewSet, basename="office")
router.register("section-assignments", views.SectionAssignmentViewSet, basename="section-assignment")
router.register("instructions", views.InstructionViewSet, basename="instruction")
router.register("approval-rules", views.ApprovalRuleViewSet, basename="approval-rule")
router.register("hazard-locations", views.HazardLocationViewSet, basename="hazard-location")
router.register("vehicle-positions", views.VehiclePositionViewSet, basename="vehicle-position")
router.register("mission-routing", views.MissionRoutingViewSet, basename="mission-routing")

urlpatterns = [path("", include(router.urls))]
