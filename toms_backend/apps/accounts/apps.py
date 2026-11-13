from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    verbose_name = "Personnel & Authentication"

    def ready(self) -> None:
        """Disable Django's ``last_login`` write.

        Django's ``user_logged_in`` receiver issues
        ``user.save(update_fields=['last_login'])``. The legacy ``users`` table
        has no ``last_login`` column, so the write would fail. Login auditing is
        handled by ``core_audit_log`` instead, which is additive.
        """
        from django.contrib.auth.models import update_last_login
        from django.contrib.auth.signals import user_logged_in

        user_logged_in.disconnect(update_last_login, dispatch_uid="update_last_login")
