from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import presence, profile, views

router = DefaultRouter()
router.register("personnel", views.PersonnelViewSet, basename="personnel")
router.register("admin/users", views.UserAdminViewSet, basename="admin-users")
router.register(
    "admin/mobile-devices", views.MobileDeviceViewSet, basename="admin-mobile-devices"
)
router.register(
    "unit-changes", views.UnitChangeRequestViewSet, basename="unit-change"
)

urlpatterns = [
    path("auth/csrf/", views.CsrfTokenView.as_view(), name="auth-csrf"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/mfa/", views.MFAStatusView.as_view(), name="auth-mfa-status"),
    path("auth/mfa/setup/", views.MFASetupView.as_view(), name="auth-mfa-setup"),
    path(
        "auth/mfa/setup/verify/",
        views.MFAVerifySetupView.as_view(),
        name="auth-mfa-setup-verify",
    ),
    path(
        "auth/mfa/challenge/",
        views.MFAChallengeView.as_view(),
        name="auth-mfa-challenge",
    ),
    # A single-device account confirms here that the other session may end.
    path(
        "auth/login/switch-device/",
        views.DeviceSwitchView.as_view(),
        name="auth-login-switch-device",
    ),
    path(
        "auth/devices/",
        views.DeviceSessionListView.as_view(),
        name="auth-devices",
    ),
    # Settings: sign out one of the account's other devices.
    path(
        "auth/devices/end/",
        views.DeviceSessionEndView.as_view(),
        name="auth-device-end",
    ),
    # The Android shell's alert watcher exchanges its live session for one
    # narrow, long-lived token that only reads the pulse and message summary.
    path(
        "auth/devices/token/",
        views.DeviceTokenView.as_view(),
        name="auth-device-token",
    ),
    # The FCM registration token of this install: what lets the server wake the
    # app with a notification instead of waiting for its next poll.
    path(
        "auth/devices/push/",
        views.DevicePushTokenView.as_view(),
        name="auth-device-push",
    ),
    # The phone's foreground location service: which run to record, and the
    # breadcrumbs for it, without a browser session that dies after an hour.
    path(
        "auth/devices/tracking/",
        views.DeviceTrackingView.as_view(),
        name="auth-device-tracking",
    ),
    # Android biometric session: enrol from a live MFA session, then rotate the
    # Keystore-held refresh token for a new 15-minute access token.
    path(
        "auth/mobile/enroll/",
        views.MobileEnrollView.as_view(),
        name="auth-mobile-enroll",
    ),
    path(
        "auth/mobile/refresh/",
        views.MobileRefreshView.as_view(),
        name="auth-mobile-refresh",
    ),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/password/", views.ChangePasswordView.as_view(), name="auth-password"),
    path(
        "auth/password-reset/",
        views.PasswordResetView.as_view(),
        name="auth-password-reset",
    ),
    # Self-service profile (spec §3). No user id in the path on purpose.
    path("auth/profile/", profile.ProfileView.as_view(), name="auth-profile"),
    # A person may withdraw their own waiting unit change request.
    path(
        "auth/profile/unit-change/cancel/",
        views.UnitChangeCancelView.as_view(),
        name="auth-profile-unit-change-cancel",
    ),
    path(
        "auth/onboarding/complete/",
        profile.OnboardingCompleteView.as_view(),
        name="auth-onboarding-complete",
    ),
    path("auth/preferences/", profile.PreferencesView.as_view(), name="auth-preferences"),
    path("auth/profile/avatar/", profile.ProfileAvatarView.as_view(), name="auth-profile-avatar"),
    path(
        "auth/profile/signature/",
        profile.ProfileSignatureView.as_view(),
        name="auth-profile-signature",
    ),
    path("personnel/<int:pk>/avatar/", profile.UserAvatarView.as_view(), name="personnel-avatar"),
    # Who has TOWEMS open right now (dashboard rail). Unit-scoped like personnel.
    path("presence/", presence.OnlineNowView.as_view(), name="presence"),
    # Authoritative role catalogue for the admin interface.
    path("roles/", views.RoleCatalogueView.as_view(), name="role-catalogue"),
    # Stateless JWT for API clients / integrations. Refused while a first-sign-in
    # gate is open, because token clients bypass the session middleware.
    path(
        "auth/token/",
        views.SetupAwareTokenObtainPairView.as_view(),
        name="token-obtain",
    ),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("", include(router.urls)),
]
