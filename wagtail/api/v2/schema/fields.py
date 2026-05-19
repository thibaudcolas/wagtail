"""
drf-spectacular OpenAPI mappings for Wagtail's custom serializer fields.

Without these, drf-spectacular defaults each custom Field to ``"string"``
and emits a warning per field. The mappings below silence the warnings
and (where possible) describe the actual response shape.
"""

from drf_spectacular.extensions import OpenApiSerializerFieldExtension


class _StringFieldExtension(OpenApiSerializerFieldExtension):
    """Common base for fields that always serialize as plain strings."""

    def map_serializer_field(self, auto_schema, direction):
        return {"type": "string", "readOnly": True}


class TypeFieldExtension(_StringFieldExtension):
    target_class = "wagtail.api.v2.serializers.TypeField"


class PageTypeFieldExtension(_StringFieldExtension):
    target_class = "wagtail.api.v2.serializers.PageTypeField"


class DetailUrlFieldExtension(_StringFieldExtension):
    target_class = "wagtail.api.v2.serializers.DetailUrlField"

    def map_serializer_field(self, auto_schema, direction):
        return {"type": "string", "format": "uri", "readOnly": True}


class PageHtmlUrlFieldExtension(DetailUrlFieldExtension):
    target_class = "wagtail.api.v2.serializers.PageHtmlUrlField"


class ImageDownloadUrlFieldExtension(DetailUrlFieldExtension):
    target_class = "wagtail.images.api.v2.serializers.ImageDownloadUrlField"


class DocumentDownloadUrlFieldExtension(DetailUrlFieldExtension):
    target_class = "wagtail.documents.api.v2.serializers.DocumentDownloadUrlField"


class TagsFieldExtension(OpenApiSerializerFieldExtension):
    target_class = "wagtail.api.v2.serializers.TagsField"

    def map_serializer_field(self, auto_schema, direction):
        return {
            "type": "array",
            "items": {"type": "string"},
            "readOnly": True,
        }


class PageChildrenFieldExtension(OpenApiSerializerFieldExtension):
    target_class = "wagtail.admin.api.serializers.PageChildrenField"

    def map_serializer_field(self, auto_schema, direction):
        return {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "listing_url": {"type": "string", "format": "uri"},
            },
            "readOnly": True,
        }


class PageStatusFieldExtension(OpenApiSerializerFieldExtension):
    target_class = "wagtail.admin.api.serializers.PageStatusField"

    def map_serializer_field(self, auto_schema, direction):
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "live": {"type": "boolean"},
                "has_unpublished_changes": {"type": "boolean"},
            },
            "readOnly": True,
        }


class _PageRefFieldExtension(OpenApiSerializerFieldExtension):
    """Common shape for fields that return a nested page reference."""

    def map_serializer_field(self, auto_schema, direction):
        return {
            "type": "object",
            "nullable": True,
            "properties": {
                "id": {"type": "integer"},
                "meta": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "detail_url": {"type": "string", "format": "uri"},
                        "html_url": {"type": "string", "format": "uri"},
                    },
                },
                "title": {"type": "string"},
            },
            "readOnly": True,
        }


class PageParentFieldExtension(_PageRefFieldExtension):
    target_class = "wagtail.api.v2.serializers.PageParentField"


class PageAliasOfFieldExtension(_PageRefFieldExtension):
    target_class = "wagtail.api.v2.serializers.PageAliasOfField"


class PageAncestorsFieldExtension(OpenApiSerializerFieldExtension):
    target_class = "wagtail.admin.api.serializers.PageAncestorsField"

    def map_serializer_field(self, auto_schema, direction):
        return {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "object"}},
            },
            "readOnly": True,
        }


class PageDescendantsFieldExtension(PageAncestorsFieldExtension):
    target_class = "wagtail.admin.api.serializers.PageDescendantsField"


class ImageRenditionFieldExtension(OpenApiSerializerFieldExtension):
    target_class = "wagtail.images.api.fields.ImageRenditionField"

    def map_serializer_field(self, auto_schema, direction):
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri"},
                "full_url": {"type": "string", "format": "uri"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "alt": {"type": "string"},
            },
            "readOnly": True,
        }
