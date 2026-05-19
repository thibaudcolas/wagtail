"""
Queryset filters for the v3 pages endpoint.

Mirrors the v2 filter parameters (``?type``, ``?child_of``, ``?ancestor_of``,
``?descendant_of``, ``?locale``, ``?translation_of``, ``?site``, ``?search``,
``?order``) but applies them imperatively rather than via DRF filter backends.
"""

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist

from wagtail.models import Page, Site

from .utils import BadRequestError, page_models_from_string


KNOWN_PAGE_QUERY_PARAMS = frozenset(
    [
        "limit",
        "offset",
        "fields",
        "order",
        "search",
        "search_operator",
        "type",
        "child_of",
        "ancestor_of",
        "descendant_of",
        "translation_of",
        "locale",
        "site",
    ]
)


def filter_by_type(queryset, request):
    type_str = request.GET.get("type")
    if not type_str:
        return queryset
    try:
        models = page_models_from_string(type_str)
    except (LookupError, ValueError) as e:
        raise BadRequestError(f"type doesn't exist: {e}") from e
    if not models:
        return queryset
    if len(models) == 1:
        # Switch to the specific page model so its fields become filterable.
        return models[0].objects.filter(
            pk__in=queryset.values_list("pk", flat=True)
        )
    return queryset.type(*models)


def filter_by_tree(queryset, request):
    if child_of := request.GET.get("child_of"):
        try:
            parent_id = int(child_of)
        except ValueError as e:
            raise BadRequestError("child_of must be a page id") from e
        queryset = queryset.child_of(Page.objects.get(pk=parent_id))
    if ancestor_of := request.GET.get("ancestor_of"):
        try:
            page_id = int(ancestor_of)
        except ValueError as e:
            raise BadRequestError("ancestor_of must be a page id") from e
        queryset = queryset.ancestor_of(Page.objects.get(pk=page_id))
    if descendant_of := request.GET.get("descendant_of"):
        try:
            page_id = int(descendant_of)
        except ValueError as e:
            raise BadRequestError("descendant_of must be a page id") from e
        queryset = queryset.descendant_of(Page.objects.get(pk=page_id))
    return queryset


def filter_by_locale(queryset, request):
    if not getattr(settings, "WAGTAIL_I18N_ENABLED", False):
        return queryset
    if locale_code := request.GET.get("locale"):
        queryset = queryset.filter(locale__language_code=locale_code)
    if translation_of := request.GET.get("translation_of"):
        try:
            page = Page.objects.get(pk=int(translation_of))
        except (ValueError, Page.DoesNotExist) as e:
            raise BadRequestError("translation_of must be a page id") from e
        queryset = queryset.translation_of(page)
    return queryset


def filter_by_site(queryset, request):
    site = None
    if "site" in request.GET:
        site_str = request.GET["site"]
        if ":" in site_str:
            hostname, _, port = site_str.partition(":")
            try:
                site = Site.objects.get(hostname=hostname, port=port)
            except Site.DoesNotExist as e:
                raise BadRequestError(f"site {site_str!r} not found") from e
        else:
            try:
                site = Site.objects.get(hostname=site_str)
            except Site.MultipleObjectsReturned as e:
                raise BadRequestError(
                    "site filter matched multiple sites; include a port"
                ) from e
            except Site.DoesNotExist as e:
                raise BadRequestError(f"site {site_str!r} not found") from e
    else:
        site = Site.find_for_request(request)
    if site is not None:
        queryset = queryset.descendant_of(site.root_page, inclusive=True)
    return queryset


def filter_by_fields(queryset, request, allowed_fields):
    """Exact-match filter on any whitelisted field, ignoring known params."""
    for key, value in request.GET.items():
        if key in KNOWN_PAGE_QUERY_PARAMS:
            continue
        if key not in allowed_fields:
            raise BadRequestError(
                f"query parameter is not an operation or a recognised field: {key}"
            )
        try:
            queryset.model._meta.get_field(key)
        except FieldDoesNotExist:
            continue
        queryset = queryset.filter(**{key: value})
    return queryset


def order_queryset(queryset, request):
    order = request.GET.get("order")
    if not order:
        return queryset.order_by("path")
    if order == "random":
        return queryset.order_by("?")
    fields = [part.strip() for part in order.split(",") if part.strip()]
    return queryset.order_by(*fields)


def apply_search(queryset, request):
    search = request.GET.get("search")
    if not search:
        return queryset
    operator = request.GET.get("search_operator", "and")
    return queryset.search(search, operator=operator)
