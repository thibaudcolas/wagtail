"""
Tests for the optional ``wagtail.api.v2.schema`` drf-spectacular extension.

The extension itself is only loaded when the user adds
``"wagtail.api.v2.schema"`` to ``INSTALLED_APPS``. These tests skip
silently if drf-spectacular isn't importable, so the project doesn't gain
a hard test-time dependency on it.
"""

import unittest

from django.test import TestCase, override_settings
from django.urls import clear_url_caches

try:
    from drf_spectacular.generators import SchemaGenerator

    HAS_SPECTACULAR = True
except ImportError:
    HAS_SPECTACULAR = False


@unittest.skipUnless(HAS_SPECTACULAR, "drf-spectacular is not installed")
@override_settings(
    REST_FRAMEWORK={
        "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    },
)
class TestSchemaGeneration(TestCase):
    """End-to-end smoke test: drf-spectacular generates a clean schema."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Ensure the extension classes are registered with drf-spectacular's
        # registry, even when "wagtail.api.v2.schema" isn't in INSTALLED_APPS
        # for the regular test suite.
        from wagtail.api.v2.schema import extensions, fields  # noqa: F401

        clear_url_caches()

    def _generate(self):
        generator = SchemaGenerator()
        return generator.get_schema(request=None, public=True)

    def test_schema_has_no_errors(self):
        # Issue #6209: schema generation used to throw AttributeError /
        # AssertionError on every Wagtail viewset. With the hardening +
        # extension in place, generation completes without raising.
        schema = self._generate()
        self.assertIn("openapi", schema)
        self.assertIn("paths", schema)

    def test_listing_endpoints_have_envelope_response(self):
        schema = self._generate()
        listing = schema["paths"]["/api/main/pages/"]["get"]
        self.assertEqual(listing["operationId"], "pages_list")

        response_schema = listing["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        # Response is the {meta, items} envelope, exposed as a $ref to a
        # named component so the schema stays compact.
        self.assertIn("$ref", response_schema)
        component_name = response_schema["$ref"].rsplit("/", 1)[-1]
        component = schema["components"]["schemas"][component_name]
        self.assertIn("meta", component["properties"])
        self.assertIn("items", component["properties"])
        self.assertEqual(component["properties"]["items"]["type"], "array")

    def test_detail_endpoints_have_single_object_response(self):
        schema = self._generate()
        detail = schema["paths"]["/api/main/pages/{id}/"]["get"]
        self.assertEqual(detail["operationId"], "pages_retrieve")
        self.assertNotIn("items", str(detail["responses"]["200"]))

    def test_listing_and_detail_have_distinct_operation_ids(self):
        # Before the extension, both endpoints produced "*_retrieve" IDs
        # and drf-spectacular had to append "_2" to resolve the clash.
        schema = self._generate()
        listing_id = schema["paths"]["/api/main/pages/"]["get"]["operationId"]
        detail_id = schema["paths"]["/api/main/pages/{id}/"]["get"]["operationId"]
        self.assertNotEqual(listing_id, detail_id)
        self.assertTrue(listing_id.endswith("_list"))
        self.assertTrue(detail_id.endswith("_retrieve"))

    def test_find_endpoint_documented_as_redirect(self):
        schema = self._generate()
        find = schema["paths"]["/api/main/pages/find/"]["get"]
        self.assertIn("302", find["responses"])

    def test_page_listing_documents_wagtail_query_parameters(self):
        schema = self._generate()
        listing = schema["paths"]["/api/main/pages/"]["get"]
        param_names = {p["name"] for p in listing.get("parameters", [])}
        # A few representative Wagtail-specific parameters should be documented.
        self.assertIn("fields", param_names)
        self.assertIn("type", param_names)
        self.assertIn("child_of", param_names)
        self.assertIn("search", param_names)

    def test_image_endpoint_does_not_have_page_parameters(self):
        # ``?child_of`` and ``?type`` are Pages-only — they should not appear
        # on the images listing.
        schema = self._generate()
        listing = schema["paths"]["/api/main/images/"]["get"]
        param_names = {p["name"] for p in listing.get("parameters", [])}
        self.assertNotIn("child_of", param_names)
        self.assertNotIn("type", param_names)
        # But the common ones should still be there.
        self.assertIn("fields", param_names)
        self.assertIn("limit", param_names)
