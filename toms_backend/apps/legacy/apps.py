from django.apps import AppConfig


class LegacyConfig(AppConfig):
    name = "apps.legacy"
    verbose_name = "Legacy MRMS tables (unmanaged)"

    def ready(self) -> None:
        # No signals: these tables are owned by the legacy application schema.
        pass
