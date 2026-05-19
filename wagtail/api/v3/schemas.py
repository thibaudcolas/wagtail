"""
Serialisation helpers for the v3 API.

Read responses are produced by :func:`serialize_page`, which honours each
model's ``api_fields`` list for anonymous callers and exposes all editable
fields to authenticated users. Write payloads are accepted as plain dicts
(``dict[str, Any]``) and validated against the target model's fields by
:func:`apply_payload`.

This intentionally avoids pre-generating a Pydantic schema per model — the
output shape varies per request based on the ``?fields=`` parameter and the
caller's auth state, so a dynamic dict-based response is more honest than
a static schema would be.
"""

from collections import OrderedDict
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from django.db import models as dj_models
from modelcluster.fields import ParentalKey

from wagtail.api import APIField
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page

from .utils import BadRequestError, parse_fields_parameter


COMMON_BODY_FIELDS = ["id"]
COMMON_META_FIELDS = ["type", "detail_url"]
PAGE_BODY_FIELDS = COMMON_BODY_FIELDS + ["title"]
PAGE_META_FIELDS = COMMON_META_FIELDS + [
    "html_url",
    "slug",
    "show_in_menus",
    "seo_title",
    "search_description",
    "first_published_at",
    "locale",
]
PAGE_LISTING_DEFAULT_FIELDS = ["id", "type", "detail_url", "title", "html_url", "slug"]


def _model_type(instance) -> str:
    meta = instance._meta
    return f"{meta.app_label}.{meta.object_name}"


def _detail_url(request, instance, endpoint_path: str) -> str:
    return request.build_absolute_uri(f"{endpoint_path}{instance.pk}/")


def _editable_field_names(model) -> list[str]:
    """All concrete editable database fields, excluding internal Wagtail fields."""
    skip = {
        "id",
        "page_ptr",
        "content_type",
        "path",
        "depth",
        "numchild",
        "url_path",
        "draft_title",
        "live",
        "has_unpublished_changes",
        "owner",
        "live_revision",
        "go_live_at",
        "expire_at",
        "expired",
        "locked",
        "locked_at",
        "locked_by",
        "latest_revision",
        "latest_revision_created_at",
        "first_published_at",
        "last_published_at",
        "alias_of",
        "translation_key",
        "wagtail_admin_comments",
    }
    names = []
    for field in model._meta.get_fields():
        if not getattr(field, "concrete", False):
            continue
        if field.auto_created and not field.concrete:
            continue
        if field.name in skip:
            continue
        names.append(field.name)
    return names


def _serialize_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dj_models.Model):
        return {
            "id": value.pk,
            "type": _model_type(value),
        }
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [_serialize_value(item) for item in value]
    return str(value)


def _serialize_field(instance, field_name, model, sub_fields=None):
    """Resolve ``field_name`` on ``instance`` to a JSON-serialisable value."""
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        # Not a model field — try attribute access (custom api_field property).
        attr = getattr(instance, field_name, None)
        if callable(attr):
            attr = attr()
        return _serialize_value(attr)

    if isinstance(field, StreamField):
        raw = getattr(instance, field_name)
        # StreamValue exposes a `raw_data` property containing block dicts.
        return getattr(raw, "raw_data", None) or list(raw)
    if isinstance(field, RichTextField):
        # Wagtail internal storage format (raw DB value), as agreed for v3.
        return getattr(instance, field_name)
    if field.is_relation and field.many_to_one:
        related = getattr(instance, field_name, None)
        if related is None:
            return None
        return {
            "id": related.pk,
            "type": _model_type(related),
        }
    if field.is_relation and (field.one_to_many or field.many_to_many):
        manager = getattr(instance, field_name)
        return [
            serialize_instance(child, request=None, authenticated=True)
            for child in manager.all()
        ]
    return _serialize_value(getattr(instance, field_name))


def _resolve_fields_config(request, default_fields, all_available, detail=False):
    """Apply ``?fields=`` semantics: ``*`` adds all, ``_`` clears, ``-`` removes."""
    fields_param = request.GET.get("fields", "") if hasattr(request, "GET") else ""
    try:
        config = parse_fields_parameter(fields_param)
    except ValueError as e:
        raise BadRequestError(f"fields error: {e}") from e

    if config and config[0][0] == "*":
        selected = set(all_available)
        config = config[1:]
    elif config and config[0][0] == "_":
        selected = set()
        config = config[1:]
    else:
        selected = set(default_fields)

    sub_fields_map = {}
    mentioned = set()
    for name, negated, sub in config:
        if negated:
            selected.discard(name)
        else:
            selected.add(name)
            if sub:
                sub_fields_map[name] = sub
        mentioned.add(name)

    unknown = mentioned - set(all_available)
    if unknown:
        raise BadRequestError("unknown fields: " + ", ".join(sorted(unknown)))

    # Preserve the order from all_available so output is stable.
    return [name for name in all_available if name in selected], sub_fields_map


def _model_api_field_names(model) -> list[str]:
    """Names declared via ``api_fields`` on the model (or any of its bases)."""
    names = []
    for entry in getattr(model, "api_fields", ()):
        if isinstance(entry, APIField):
            names.append(entry.name)
        else:
            names.append(entry)
    return names


def get_available_fields(model, *, authenticated: bool, is_page: bool) -> list[str]:
    """Return the universe of field names that callers may request."""
    base_body = PAGE_BODY_FIELDS if is_page else COMMON_BODY_FIELDS
    base_meta = PAGE_META_FIELDS if is_page else COMMON_META_FIELDS
    available = list(OrderedDict.fromkeys(base_body + base_meta))

    if authenticated:
        # Authenticated callers may see every editable field on the model.
        for name in _editable_field_names(model):
            if name not in available:
                available.append(name)
    else:
        # Anonymous callers only see fields declared via api_fields.
        for name in _model_api_field_names(model):
            if name not in available:
                available.append(name)

    return available


def serialize_instance(instance, *, request, authenticated: bool, endpoint_path="", detail=False):
    model = type(instance)
    is_page = isinstance(instance, Page)
    available = get_available_fields(model, authenticated=authenticated, is_page=is_page)

    if detail:
        default_fields = available
    elif is_page:
        default_fields = PAGE_LISTING_DEFAULT_FIELDS
    else:
        default_fields = list(COMMON_BODY_FIELDS + COMMON_META_FIELDS)

    if request is not None:
        selected, sub_fields = _resolve_fields_config(
            request, default_fields, available, detail=detail
        )
    else:
        selected, sub_fields = default_fields, {}

    body_names = PAGE_BODY_FIELDS if is_page else COMMON_BODY_FIELDS
    meta_names = PAGE_META_FIELDS if is_page else COMMON_META_FIELDS

    body = OrderedDict()
    meta = OrderedDict()

    for name in selected:
        value = _resolve_special_field(
            instance, name, request, endpoint_path, is_page
        )
        if value is _UNSET:
            value = _serialize_field(instance, name, model, sub_fields.get(name))
        if name in meta_names:
            meta[name] = value
        elif name in body_names or name == "id":
            body[name] = value
        else:
            body[name] = value

    # Always include id+type+detail_url-style keys in expected positions.
    result = OrderedDict()
    if "id" in body:
        result["id"] = body.pop("id")
    if meta:
        result["meta"] = meta
    result.update(body)
    return result


_UNSET = object()


def _resolve_special_field(instance, name, request, endpoint_path, is_page):
    if name == "type":
        return _model_type(instance)
    if name == "detail_url" and request is not None and endpoint_path:
        return _detail_url(request, instance, endpoint_path)
    if name == "html_url" and is_page:
        try:
            return instance.full_url
        except Exception:
            return None
    if name == "locale" and is_page:
        locale = getattr(instance, "locale", None)
        return getattr(locale, "language_code", None) if locale else None
    return _UNSET


def serialize_page(instance, *, request, authenticated, endpoint_path, detail=False):
    """Convenience wrapper for serialising Page instances.

    For listing views the base ``Page`` instance is enough — the default
    fields all live on the base model. Resolving ``.specific`` is only worth
    the per-row query (and the ``DoesNotExist`` blast radius if a specific
    row is missing) when we're rendering a detail view or the caller asked
    for fields beyond the defaults.
    """
    target = instance
    if detail and hasattr(instance, "specific"):
        try:
            target = instance.specific
        except Exception:
            # Specific row missing or unreadable; fall back to the base page
            # rather than failing the whole request.
            target = instance
    return serialize_instance(
        target,
        request=request,
        authenticated=authenticated,
        endpoint_path=endpoint_path,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


WRITE_PROTECTED = {
    "id",
    "page_ptr",
    "content_type",
    "path",
    "depth",
    "numchild",
    "url_path",
    "live",
    "has_unpublished_changes",
    "owner",
    "live_revision",
    "latest_revision",
    "latest_revision_created_at",
    "first_published_at",
    "last_published_at",
    "alias_of",
    "translation_key",
}


def apply_payload(instance, payload: dict) -> dict[str, list]:
    """
    Copy values from ``payload`` onto ``instance`` for fields that exist on
    the model and are not in :data:`WRITE_PROTECTED`. Returns a dict of
    ``{"applied": [...], "skipped": [...]}`` for the response body.
    """
    if not isinstance(payload, dict):
        raise BadRequestError("request body must be a JSON object")

    model = type(instance)
    applied = []
    skipped = []
    child_field_payloads: dict[str, list[dict]] = {}

    for name, value in payload.items():
        if name in WRITE_PROTECTED:
            skipped.append(name)
            continue
        try:
            field = model._meta.get_field(name)
        except Exception:
            skipped.append(name)
            continue

        if isinstance(field, StreamField):
            setattr(instance, name, value or [])
            applied.append(name)
            continue
        if field.is_relation and field.many_to_one:
            if value is None:
                setattr(instance, f"{field.name}_id", None)
            else:
                setattr(instance, f"{field.name}_id", value)
            applied.append(name)
            continue
        if field.is_relation and field.one_to_many and isinstance(value, list):
            # Defer Orderable/InlinePanel children until after instance is saved.
            child_field_payloads[name] = value
            applied.append(name)
            continue
        if field.is_relation:
            skipped.append(name)
            continue

        setattr(instance, name, value)
        applied.append(name)

    instance._pending_child_payloads = child_field_payloads
    return {"applied": applied, "skipped": skipped}


def save_child_payloads(instance) -> None:
    """Persist Orderable child payloads recorded by :func:`apply_payload`."""
    pending = getattr(instance, "_pending_child_payloads", None) or {}
    for relation_name, items in pending.items():
        manager = getattr(instance, relation_name)
        child_model = manager.model
        parental_fk_name = None
        for field in child_model._meta.get_fields():
            if isinstance(field, ParentalKey):
                parental_fk_name = field.name
                break
        manager.all().delete()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            kwargs = {
                k: v
                for k, v in item.items()
                if k not in WRITE_PROTECTED and k != parental_fk_name
            }
            if "sort_order" not in kwargs and any(
                f.name == "sort_order" for f in child_model._meta.get_fields()
            ):
                kwargs["sort_order"] = index
            manager.create(**kwargs)
    instance._pending_child_payloads = {}
