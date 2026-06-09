"""URL conf for the schemathesis QA prototype.

Wraps Wagtail's test URL conf and adds a ``/api/schema/`` route that
serves the generated OpenAPI document. schemathesis fetches the schema
from there before running its checks.
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

# Reuse the regular test URL conf, then append the schema endpoint.
from wagtail.test.urls import urlpatterns as base_urlpatterns

urlpatterns = list(base_urlpatterns) + [
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
]
