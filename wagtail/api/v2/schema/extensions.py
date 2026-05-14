"""
drf-spectacular extensions for Wagtail's API v2 viewsets.

This module is imported lazily by the app config so it isn't a hard runtime
dependency: drf-spectacular only needs to be importable when the user opts
in by adding ``"wagtail.api.v2.schema"`` to ``INSTALLED_APPS``.

The extension produces a schema that:

-   Splits the listing and detail operations: listing IDs end in ``_list``,
    detail IDs end in ``_retrieve`` (no more colliding ``_retrieve_2`` names).
-   Models the listing envelope (``{"meta": {"total_count": int}, "items": [...]}``)
    as its own response component.
-   Documents Wagtail-specific query parameters such as ``?fields``, ``?type``,
    ``?child_of``, ``?search``, etc.
-   Treats the ``find/`` endpoint as a redirect (302) rather than an
    object response.

It does not model per-page-type field variations: those depend on the
``?type`` query parameter at request time and would require generating one
schema component per page model. That's left as a follow-up.
"""

from drf_spectacular.extensions import OpenApiViewExtension
from drf_spectacular.plumbing import get_class
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from wagtail.api.v2.views import PagesAPIViewSet

from .parameters import (
    COMMON_LISTING_PARAMETERS,
    FIND_PARAMETERS,
    PAGE_FIND_PARAMETERS,
    PAGE_LISTING_PARAMETERS,
)


def _rename_serializer(serializer_class, new_name):
    """
    Return a subclass of *serializer_class* with a different ``__name__``.

    Wagtail's API builds serializer classes dynamically and names them after
    the underlying model (eg. "PageSerializer"). When the same model is
    exposed by several viewsets (v2 API, admin API, user subclasses), the
    dynamic classes share a name and drf-spectacular emits a "duplicate
    component" warning. Giving each one a viewset-scoped name fixes this
    without changing Wagtail's runtime behaviour.
    """
    return type(new_name, (serializer_class,), {})


def _build_listing_serializer(target_class):
    """
    Build a serializer that describes the listing response for a viewset:
    ``{"meta": {"total_count": int}, "items": [item_serializer, ...]}``.
    """
    item_serializer = target_class._get_serializer_class(
        router=None,
        model=target_class.model,
        fields_config=[],
        show_details=False,
    )
    op_base = _operation_id_base(target_class)
    component_name = f"Wagtail{op_base.capitalize()}List"
    item_serializer = _rename_serializer(
        item_serializer, f"Wagtail{op_base.capitalize()}ListItem"
    )
    return inline_serializer(
        name=component_name,
        fields={
            "meta": inline_serializer(
                name=f"{component_name}Meta",
                fields={"total_count": serializers.IntegerField()},
            ),
            "items": item_serializer(many=True),
        },
    )


def _build_detail_serializer(target_class):
    """Serializer for the detail endpoint of a viewset."""
    detail_serializer = target_class._get_serializer_class(
        router=None,
        model=target_class.model,
        fields_config=[],
        show_details=True,
    )
    op_base = _operation_id_base(target_class)
    return _rename_serializer(detail_serializer, f"Wagtail{op_base.capitalize()}Detail")


def _operation_id_base(view_class):
    # Prefer the viewset's declared ``name`` ("pages", "images", …) so the
    # operation IDs match the URL segment users see.
    name = getattr(view_class, "name", None)
    if name:
        return name
    return view_class.__name__.removesuffix("APIViewSet").lower() or "endpoint"


class _WagtailViewSetExtensionMixin:
    """
    Shared ``view_replacement`` logic for all Wagtail API viewset extensions.

    Subclasses declare the right ``target_class``, ``listing_parameters``
    and ``find_parameters`` for the kind of endpoint they cover.
    """

    listing_parameters = COMMON_LISTING_PARAMETERS
    find_parameters = FIND_PARAMETERS

    def view_replacement(self):
        # ``self.target`` is the *actual* matched class (e.g. PagesAPIViewSet
        # or a user subclass) when ``match_subclasses = True``;
        # ``self.target_class`` is the declared base, which would lose the
        # subclass-specific ``model`` and field config.
        target_class = self.target
        op_base = _operation_id_base(target_class)
        tag = op_base.capitalize()

        listing_response = _build_listing_serializer(target_class)
        detail_response = _build_detail_serializer(target_class)
        listing_params = self.listing_parameters
        find_params = self.find_parameters

        class WagtailAPIViewSetWithSchema(target_class):
            @extend_schema(
                operation_id=f"{op_base}_list",
                tags=[tag],
                parameters=listing_params,
                responses={200: listing_response},
            )
            def listing_view(self, request):
                return super().listing_view(request)

            @extend_schema(
                operation_id=f"{op_base}_retrieve",
                tags=[tag],
                responses={200: detail_response},
            )
            def detail_view(self, request, pk):
                return super().detail_view(request, pk)

            @extend_schema(
                operation_id=f"{op_base}_find",
                tags=[tag],
                parameters=find_params,
                responses={
                    302: OpenApiResponse(
                        description=(
                            "Redirects to the detail endpoint for the matched "
                            "object."
                        ),
                    ),
                    404: OpenApiResponse(description="No object matched the query."),
                },
            )
            def find_view(self, request):
                return super().find_view(request)

        WagtailAPIViewSetWithSchema.__name__ = target_class.__name__
        WagtailAPIViewSetWithSchema.__qualname__ = target_class.__qualname__
        return WagtailAPIViewSetWithSchema


class WagtailPagesAPIViewSetExtension(
    _WagtailViewSetExtensionMixin, OpenApiViewExtension
):
    """Schema for ``PagesAPIViewSet`` and its subclasses."""

    target_class = "wagtail.api.v2.views.PagesAPIViewSet"
    match_subclasses = True
    priority = 1  # win over the base extension for Pages subclasses

    listing_parameters = COMMON_LISTING_PARAMETERS + PAGE_LISTING_PARAMETERS
    find_parameters = PAGE_FIND_PARAMETERS

    @classmethod
    def _matches(cls, target):
        try:
            return issubclass(get_class(target), PagesAPIViewSet)
        except TypeError:
            return False


class WagtailBaseAPIViewSetExtension(
    _WagtailViewSetExtensionMixin, OpenApiViewExtension
):
    """Schema for any other ``BaseAPIViewSet`` subclass (images, documents, …)."""

    target_class = "wagtail.api.v2.views.BaseAPIViewSet"
    match_subclasses = True
    priority = 0

    @classmethod
    def _matches(cls, target):
        # match_subclasses also matches BaseAPIViewSet itself, but that's an
        # abstract class with ``model = None`` so there's no real schema to
        # generate.
        view_class = get_class(target)
        if getattr(view_class, "model", None) is None:
            return False
        return super()._matches(target)
