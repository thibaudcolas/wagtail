"""
Optional drf-spectacular extension for Wagtail's REST API v2.

Enable by adding ``"wagtail.api.v2.schema"`` to ``INSTALLED_APPS`` alongside
``"drf_spectacular"``. The extension auto-registers and improves the
generated OpenAPI schema for any viewset that inherits from
:class:`wagtail.api.v2.views.BaseAPIViewSet`. It does not affect runtime
behaviour of the API itself.

See :ref:`api_v2_openapi_schema` for setup instructions and the list of
schema improvements this extension provides.
"""

default_app_config = "wagtail.api.v2.schema.apps.WagtailAPIv2SchemaConfig"
