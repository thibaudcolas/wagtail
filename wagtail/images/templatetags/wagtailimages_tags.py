import re
from functools import lru_cache

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.urls import NoReverseMatch

from wagtail.images.models import Filter
from wagtail.images.shortcuts import get_rendition_or_not_found 
from wagtail.images.shortcuts import get_multiple_renditions_or_not_found
from wagtail.images.utils import to_svg_safe_spec
from wagtail.images.views.serve import generate_image_url

register = template.Library()
allowed_filter_pattern = re.compile(r"^[A-Za-z0-9_\-\.]+$")


@register.tag(name="image")
def image(parser, token):
    bits = token.split_contents()[1:]
    image_expr = parser.compile_filter(bits[0])
    bits = bits[1:]

    filter_specs = []
    attrs = {}
    output_var_name = None

    as_context = False  # if True, the next bit to be read is the output variable name
    is_valid = True

    preserve_svg = False

    for bit in bits:
        if bit == "as":
            # token is of the form {% image self.photo max-320x200 as img %}
            as_context = True
        elif as_context:
            if output_var_name is None:
                output_var_name = bit
            else:
                # more than one item exists after 'as' - reject as invalid
                is_valid = False
        elif bit == "preserve-svg":
            preserve_svg = True
        else:
            try:
                name, value = bit.split("=")
                attrs[name] = parser.compile_filter(
                    value
                )  # setup to resolve context variables as value
            except ValueError:
                if allowed_filter_pattern.match(bit):
                    filter_specs.append(bit)
                else:
                    raise template.TemplateSyntaxError(
                        "filter specs in 'image' tag may only contain A-Z, a-z, 0-9, dots, hyphens and underscores. "
                        "(given filter: {})".format(bit)
                    )

    if as_context and output_var_name is None:
        # context was introduced but no variable given ...
        is_valid = False

    if output_var_name and attrs:
        # attributes are not valid when using the 'as img' form of the tag
        is_valid = False

    if len(filter_specs) == 0:
        # there must always be at least one filter spec provided
        is_valid = False

    if len(bits) == 0:
        # no resize rule provided eg. {% image page.image %}
        raise template.TemplateSyntaxError(
            "no resize rule provided. "
            "'image' tag should be of the form {% image self.photo max-320x200 [ custom-attr=\"value\" ... ] %} "
            "or {% image self.photo max-320x200 as img %}"
        )

    if is_valid:
        return ImageNode(
            image_expr,
            filter_specs,
            attrs=attrs,
            output_var_name=output_var_name,
            preserve_svg=preserve_svg,
        )
    else:
        raise template.TemplateSyntaxError(
            "'image' tag should be of the form {% image self.photo max-320x200 [ custom-attr=\"value\" ... ] %} "
            "or {% image self.photo max-320x200 as img %}"
        )


class ImageNode(template.Node):
    def __init__(
        self,
        image_expr,
        filter_specs,
        output_var_name=None,
        attrs={},
        preserve_svg=False,
    ):
        self.image_expr = image_expr
        self.output_var_name = output_var_name
        self.attrs = attrs
        self.filter_specs = filter_specs
        self.preserve_svg = preserve_svg

    @lru_cache
    def get_filter(self, preserve_svg=False):
        if preserve_svg:
            return Filter(to_svg_safe_spec(self.filter_specs))
        return Filter(spec="|".join(self.filter_specs))

    def render(self, context):
        try:
            image = self.image_expr.resolve(context)
        except template.VariableDoesNotExist:
            return ""

        if not image:
            if self.output_var_name:
                context[self.output_var_name] = None
            return ""

        if not hasattr(image, "get_rendition"):
            raise ValueError("image tag expected an Image object, got %r" % image)

        rendition = get_rendition_or_not_found(
            image,
            self.get_filter(preserve_svg=self.preserve_svg and image.is_svg()),
        )

        if self.output_var_name:
            # return the rendition object in the given variable
            context[self.output_var_name] = rendition
            return ""
        else:
            # render the rendition's image tag now
            resolved_attrs = {}
            for key in self.attrs:
                resolved_attrs[key] = self.attrs[key].resolve(context)
            return rendition.img_tag(resolved_attrs)


@register.simple_tag()
def image_url(image, filter_spec, viewname="wagtailimages_serve"):
    try:
        return generate_image_url(image, filter_spec, viewname)
    except NoReverseMatch:
        raise ImproperlyConfigured(
            "'image_url' tag requires the "
            + viewname
            + " view to be configured. Please see "
            "https://docs.wagtail.org/en/stable/advanced_topics/images/image_serve_view.html#setup for instructions."
        )

@register.tag(name="image_srcset")
def image_srcset(parser, token):
    bits = token.split_contents()[1:]
    image_expr = parser.compile_filter(bits[0])
    bits = bits[1:]

    filter_specs = []
    attrs = {}
    output_var_name = None

    as_context = False  # if True, the next bit to be read is the output variable name
    is_valid = True

    preserve_svg = False

    for bit in bits:
        if bit == "as":
            # token is of the form {% image self.photo max-320x200 as img %}
            as_context = True
        elif as_context:
            if output_var_name is None:
                output_var_name = bit
            else:
                # more than one item exists after 'as' - reject as invalid
                is_valid = False
        elif bit == "preserve-svg":
            preserve_svg = True
        else:
            try:
                name, value = bit.split("=")
                attrs[name] = parser.compile_filter(
                    value
                )  # setup to resolve context variables as value
            except ValueError:
                if allowed_filter_pattern.match(bit):
                    filter_specs.append(bit)
                else:
                    raise template.TemplateSyntaxError(
                        "filter specs in 'image' tag may only contain A-Z, a-z, 0-9, dots, hyphens and underscores. "
                        "(given filter: {})".format(bit)
                    )

    if as_context and output_var_name is None:
        # context was introduced but no variable given ...
        is_valid = False

    if output_var_name and attrs:
        # attributes are not valid when using the 'as img' form of the tag
        is_valid = False

    if len(filter_specs) == 0:
        # there must always be at least one filter spec provided
        is_valid = False

    if len(bits) == 0:
        # no resize rule provided eg. {% image page.image %}
        raise template.TemplateSyntaxError(
            "no resize rule provided. "
            "'image' tag should be of the form {% image self.photo max-320x200 [ custom-attr=\"value\" ... ] %} "
            "or {% image self.photo max-320x200 as img %}"
        )

    if is_valid:
        return ImageSrcsetNode(
            image_expr,
            filter_specs,
            attrs=attrs,
            output_var_name=output_var_name,
            preserve_svg=preserve_svg,
        )
    else:
        raise template.TemplateSyntaxError(
            "'image' tag should be of the form {% image self.photo max-320x200 [ custom-attr=\"value\" ... ] %} "
            "or {% image self.photo max-320x200 as img %}"
        )

class ImageSrcsetNode(template.Node):
    def __init__(
        self,
        image_expr,
        filter_specs,
        output_var_name=None,
        attrs={},
        preserve_svg=False,
    ):
        self.image_expr = image_expr
        self.output_var_name = output_var_name
        self.attrs = attrs
        self.filter_specs = filter_specs
        self.preserve_svg = preserve_svg

    @lru_cache()
    def raw_filter_specs(self, context):
        # Space-separated filters, to be concatenated.
        spaced_filters = []
        # Brace-expanded filters, to generate multiple renditions at once.
        braced_filters = []

        named_filters = getattr(settings, 'WAGTAIL_PICTURE_PROPOSAL_NAMED_FILTERS', {})

        for spec in self.filter_specs:
            raw_filter = spec.resolve(context) if isinstance(spec, FilterExpression) else spec
            # TODO If the filter matches one of the predefined named filters.
            # Do we want those to be expanded as well?
            raw_filter = named_filters.get(raw_filter, raw_filter)
            if "{" in raw_filter:
                if len(braced_filters) > 0:
                    raise ValueError(f"{self.tag_name} tag supports at most one pattern with brace-expansion, got {braced_filters:r} and {raw_filter:r}")

                # Example: fill-{1600x900,800x450}-c80
                split_filter = raw_filter.split('{')
                filter_prefix, repeat_pattern_suffixed = split_filter

                if len(split_filter) > 2:
                    raise ValueError(f"{self.tag_name} tag expected at most one brace-expansion pattern in the filter, got {len(split_filter) - 1} in {raw_filter:r}")

                repeat_pattern, filter_suffix = repeat_pattern_suffixed.split('}')
                repeats = repeat_pattern.split(',')
                braced_filters = [f"{filter_prefix}{repeat}{filter_suffix}" for repeat in repeats]
            else:
                spaced_filters.append(raw_filter)

        if braced_filters:
            raw_combined_filters = ['|'.join((f, *spaced_filters)) for f in braced_filters]
        else:
            raw_combined_filters = ['|'.join(spaced_filters)]

        return raw_combined_filters

    def render(self, context):
        try:
            image = self.image_expr.resolve(context)
        except template.VariableDoesNotExist:
            return ''

        if not image:
            if self.output_var_name:
                context[self.output_var_name] = None
            return ''

        if not hasattr(image, 'get_rendition'):
            raise ValueError(f"{self.tag_name} tag expected an Image object, got {image!r}")

        filters = [Filter(spec=f) for f in self.raw_filter_specs(context)]
        renditions = get_renditions_or_not_found(image, filters)

        if self.output_var_name:
            # return the rendition object in the given variable
            context[self.output_var_name] = renditions
            return ''
        else:
            # render the rendition's image tag now
            resolved_attrs = {
                'srcset': ",".join([f"{rendition.url} {rendition.width}w" for rendition in renditions])
            for key in self.attrs:
                resolved_attrs[key] = self.attrs[key].resolve(context)

            return render_to_string('img_srcset_wip.html', {
                "fallback_renditions": renditions,
                "attributes": resolved_attrs
            })

    def get_filter(self, preserve_svg=False):
        if preserve_svg:
            return Filter(to_svg_safe_spec(self.filter_specs))
        return Filter(spec="|".join(self.filter_specs))

    def render(self, context):
        try:
            image = self.image_expr.resolve(context)
        except template.VariableDoesNotExist:
            return ""

        if not image:
            if self.output_var_name:
                context[self.output_var_name] = None
            return ""

        if not hasattr(image, "get_rendition"):
            raise ValueError("image tag expected an Image object, got %r" % image)

        rendition = get_multiple_rendition_or_not_found(
            image,
            self.get_filter(preserve_svg=self.preserve_svg and image.is_svg()),
        )

        if self.output_var_name:
            # return the rendition object in the given variable
            context[self.output_var_name] = rendition
            return ""
        else:
            # render the rendition's image tag now
            resolved_attrs = {}
            for key in self.attrs:
                resolved_attrs[key] = self.attrs[key].resolve(context)
            return rendition.img_tag(resolved_attrs)
