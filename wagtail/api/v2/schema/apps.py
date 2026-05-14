from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class WagtailAPIv2SchemaConfig(AppConfig):
    name = "wagtail.api.v2.schema"
    label = "wagtailapi_v2_schema"
    verbose_name = "Wagtail API v2 OpenAPI schema"

    def ready(self):
        try:
            import drf_spectacular  # noqa: F401
        except ImportError as exc:
            raise ImproperlyConfigured(
                "'wagtail.api.v2.schema' requires drf-spectacular to be "
                "installed. Either install it (`pip install drf-spectacular`) "
                "or remove 'wagtail.api.v2.schema' from INSTALLED_APPS."
            ) from exc

        # Importing these modules registers the OpenApi*Extension subclasses
        # with drf-spectacular's extension registry.
        from . import extensions, fields  # noqa: F401
