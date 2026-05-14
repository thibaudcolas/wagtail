"""
Tests for schema-generation resilience.

Schema generators such as drf-spectacular, drf-yasg, and DRF's built-in
SchemaGenerator probe viewsets without going through
``WagtailAPIRouter.wrap_view``. As a result the request never receives the
``wagtailapi_router`` attribute, and the viewset is invoked with the default
DRF action names (``list``/``retrieve``) rather than Wagtail's
``listing_view``/``detail_view``. Historically this caused several
exceptions during schema generation (see #6209). These tests pin the
hardening that lets ``get_serializer_class`` and ``get_serializer_context``
return a usable serializer in that context.
"""

from django.test import RequestFactory, TestCase
from rest_framework.request import Request

from wagtail.api.v2.views import PagesAPIViewSet
from wagtail.documents.api.v2.views import DocumentsAPIViewSet
from wagtail.images.api.v2.views import ImagesAPIViewSet


def _bare_drf_request(path="/"):
    """A DRF Request whose underlying WSGIRequest has no wagtailapi_router."""
    return Request(RequestFactory().get(path))


class TestListingActionAlias(TestCase):
    """The "list" action (used by schema generators) must behave like "listing_view"."""

    def _make_view(self, action):
        view = PagesAPIViewSet()
        view.request = _bare_drf_request()
        view.action = action
        view.kwargs = {}
        view.format_kwarg = None
        return view

    def test_listing_view_action_is_a_listing(self):
        view = self._make_view("listing_view")
        self.assertTrue(view._is_listing_action())

    def test_list_action_is_treated_as_listing(self):
        # drf-spectacular and DRF's built-in generator call this "list".
        view = self._make_view("list")
        self.assertTrue(view._is_listing_action())

    def test_detail_view_action_is_not_a_listing(self):
        view = self._make_view("detail_view")
        self.assertFalse(view._is_listing_action())


class TestSerializerClassWithoutRouter(TestCase):
    """get_serializer_class must work when wagtailapi_router is absent."""

    def _make_view(self, viewset_cls, action="list", kwargs=None):
        view = viewset_cls()
        view.request = _bare_drf_request()
        view.action = action
        view.kwargs = kwargs or {}
        view.format_kwarg = None
        return view

    def test_pages_listing_serializer(self):
        view = self._make_view(PagesAPIViewSet, action="list")
        # Should not raise AttributeError("'Request' object has no attribute
        # 'wagtailapi_router'") or AssertionError about a missing pk kwarg.
        serializer_class = view.get_serializer_class()
        self.assertIsNotNone(serializer_class)
        # The listing serializer omits detail-only fields (eg "parent").
        self.assertNotIn("parent", serializer_class().fields)

    def test_pages_detail_serializer_without_pk(self):
        # Schema generators probe the detail view without a pk kwarg.
        view = self._make_view(PagesAPIViewSet, action="retrieve")
        serializer_class = view.get_serializer_class()
        self.assertIsNotNone(serializer_class)
        # The detail serializer should include detail-only fields.
        self.assertIn("parent", serializer_class().fields)

    def test_images_listing_serializer(self):
        view = self._make_view(ImagesAPIViewSet, action="list")
        self.assertIsNotNone(view.get_serializer_class())

    def test_documents_listing_serializer(self):
        view = self._make_view(DocumentsAPIViewSet, action="list")
        self.assertIsNotNone(view.get_serializer_class())

    def test_serializer_context_router_is_none(self):
        view = self._make_view(PagesAPIViewSet, action="list")
        context = view.get_serializer_context()
        # The context must always include the "router" key. Downstream fields
        # such as DetailUrlField rely on it being present even if it's None.
        self.assertIn("router", context)
        self.assertIsNone(context["router"])
