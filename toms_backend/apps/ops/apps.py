from django.apps import AppConfig


class OpsConfig(AppConfig):
    name = "apps.ops"
    verbose_name = "Tactical Operations"

    def ready(self):
        # Registers the post-save hook that turns a new notification into one
        # FCM message. Imported here, not in models, so the hook is installed
        # exactly once per process.
        from apps.ops import signals  # noqa: F401
