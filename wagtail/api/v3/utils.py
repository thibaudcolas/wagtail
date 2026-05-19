from django.apps import apps
from django.http import Http404

from wagtail.models import Page


class BadRequestError(Exception):
    """Raised when a request includes invalid parameters or values."""


def parse_fields_parameter(input_str):
    """
    Parse a ``?fields=`` querystring value into a list of
    ``(field_name, negated, sub_fields)`` tuples.

    Mirrors :func:`wagtail.api.v2.utils.parse_fields_parameter` so authors
    can reuse the v2 ``?fields=`` syntax in v3.
    """
    if not input_str:
        return []

    items = []
    buffer = ""
    depth = 0

    for char in input_str + ",":
        if char == "(" and depth == 0:
            field_name = buffer
            buffer = ""
            depth = 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                sub = parse_fields_parameter(buffer)
                items.append((field_name, False, sub))
                buffer = ""
                field_name = None
                continue
        if char == "," and depth == 0:
            name = buffer.strip()
            buffer = ""
            if not name:
                continue
            negated = False
            if name.startswith("-"):
                negated = True
                name = name[1:]
            items.append((name, negated, None))
            continue
        buffer += char

    return items


def page_models_from_string(type_str):
    """Given a comma-separated string of "app_label.ModelName" pairs, return a
    list of page model classes. Raises LookupError for unknown labels and
    ValueError if any of the models is not a Page subclass."""
    models = []
    for item in type_str.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            app_label, model_name = item.split(".", 1)
        except ValueError as e:
            raise ValueError(f"invalid type name: {item!r}") from e
        model = apps.get_model(app_label, model_name)
        if not issubclass(model, Page):
            raise ValueError(f"{item} is not a Page model")
        models.append(model)
    return models


def get_object_or_404(queryset, **filters):
    try:
        return queryset.get(**filters)
    except queryset.model.DoesNotExist as e:
        raise Http404(f"{queryset.model.__name__} not found") from e
