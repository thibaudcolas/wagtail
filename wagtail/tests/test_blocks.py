import base64
import collections
import copy
import json
import unittest
import unittest.mock
from decimal import Decimal

# non-standard import name for gettext_lazy, to prevent strings from being picked up for translation
from django import forms
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.utils import ErrorList
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils.safestring import SafeData, mark_safe
from django.utils.translation import gettext_lazy as _

from wagtail import blocks
from wagtail.blocks.base import get_error_json_data
from wagtail.blocks.definition_lookup import BlockDefinitionLookup
from wagtail.blocks.field_block import FieldBlockAdapter
from wagtail.blocks.list_block import ListBlockAdapter, ListBlockValidationError
from wagtail.blocks.static_block import StaticBlockAdapter
from wagtail.blocks.stream_block import StreamBlockAdapter, StreamBlockValidationError
from wagtail.blocks.struct_block import StructBlockAdapter, StructBlockValidationError
from wagtail.models import Page
from wagtail.rich_text import RichText
from wagtail.test.testapp.blocks import LinkBlock as CustomLinkBlock
from wagtail.test.testapp.blocks import SectionBlock
from wagtail.test.testapp.models import EventPage, SimplePage
from wagtail.test.utils import WagtailTestUtils


class FooStreamBlock(blocks.StreamBlock):
    text = blocks.CharBlock()
    error = 'At least one block must say "foo"'

    def clean(self, value):
        value = super().clean(value)
        if not any(block.value == "foo" for block in value):
            raise blocks.StreamBlockValidationError(
                non_block_errors=ErrorList([self.error])
            )
        return value


class ContextCharBlock(blocks.CharBlock):
    def get_context(self, value, parent_context=None):
        value = str(value).upper()
        return super(blocks.CharBlock, self).get_context(value, parent_context)


class TestBlock(SimpleTestCase):
    def test_normalize(self):
        """The base normalize implementation should return its argument unchanged"""
        obj = object()
        self.assertIs(blocks.Block().normalize(obj), obj)

    def test_block_definition_registry(self):
        """Instantiating a Block should register it in the definition registry"""
        block = blocks.Block()
        self.assertIs(blocks.Block.definition_registry[block.definition_prefix], block)

    def test_block_is_previewable(self):
        class CustomContextBlock(blocks.Block):
            def get_preview_context(self, value, parent_context=None):
                return {"value": value, "foo": "bar"}

        class CustomTemplateBlock(blocks.Block):
            def get_preview_template(self, value=None, context=None):
                return "foo.html"

        class CustomValueBlock(blocks.Block):
            def get_preview_value(self):
                return "foo"

        variants = {
            "no_config": [
                blocks.Block(),
            ],
            "specific_template": [
                blocks.Block(preview_template="foo.html"),
                CustomTemplateBlock(),
            ],
            "custom_value": [
                blocks.Block(preview_value="foo"),
                blocks.Block(default="bar"),
                CustomContextBlock(),
                CustomValueBlock(),
            ],
            "specific_template_and_custom_value": [
                blocks.Block(preview_template="foo.html", preview_value="bar"),
            ],
            "unset_default_not_none": [
                blocks.ListBlock(blocks.Block()),
                blocks.StreamBlock(),
                blocks.StructBlock(),
            ],
        }

        # Test without a global template override
        cases = [
            # Unconfigured block should not be previewable
            ("no_config", False),
            # Providing a specific preview template should make the block
            # previewable even without a custom preview value, as the content
            # may be hardcoded in the template
            ("specific_template", True),
            # Providing a preview value without a custom template should not
            # make the block previewable, as it may be missing the static assets
            ("custom_value", False),
            # Providing both a preview template and value also makes the block
            # previewable, this is the same as providing a custom template only
            ("specific_template_and_custom_value", True),
            # These blocks define their own unset default value that is not
            # `None`, and that value should not make it previewable
            ("unset_default_not_none", False),
        ]
        for variant, is_previewable in cases:
            with self.subTest(variant=variant, custom_global_template=False):
                for block in variants[variant]:
                    self.assertIs(block.is_previewable, is_previewable)

        # Test with a global template override
        with unittest.mock.patch(
            "wagtail.blocks.base.template_is_overridden",
            return_value=True,
        ):
            cases = [
                # Global template override + no preview value = not previewable,
                # since it's unlikely the global template alone will provide a
                # useful preview
                ("no_config", False),
                # Unchanged – specific template always makes the block previewable
                ("specific_template", True),
                # Global template override + custom preview value = previewable.
                # We assume the global template will provide the static assets,
                # and the custom value (and the block's real template via
                # {% include_block %}) will provide the content.
                ("custom_value", True),
                # Unchanged – providing both also makes the block previewable
                ("specific_template_and_custom_value", True),
                # Unchanged – even after providing a global template override,
                # these blocks should not be previewable
                ("unset_default_not_none", False),
            ]
            for variant, is_previewable in cases:
                with self.subTest(variant=variant, custom_global_template=True):
                    for block in variants[variant]:
                        del block.is_previewable  # Clear cached_property
                        self.assertIs(block.is_previewable, is_previewable)


class TestFieldBlock(WagtailTestUtils, SimpleTestCase):
    def test_charfield_render(self):
        block = blocks.CharBlock()
        html = block.render("Hello world!")

        self.assertEqual(html, "Hello world!")

    def test_block_definition_registry(self):
        block = blocks.CharBlock(label="Test block")
        registered_block = blocks.Block.definition_registry[block.definition_prefix]
        self.assertIsInstance(registered_block, blocks.CharBlock)
        self.assertEqual(registered_block.meta.label, "Test block")
        self.assertIs(registered_block, block)

    def test_charfield_render_with_template(self):
        block = blocks.CharBlock(template="tests/blocks/heading_block.html")
        html = block.render("Hello world!")

        self.assertEqual(html, "<h1>Hello world!</h1>")

    def test_charblock_adapter(self):
        block = blocks.CharBlock(help_text="Some helpful text")

        block.set_name("test_block")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_block")
        self.assertIsInstance(js_args[1], forms.TextInput)
        self.assertEqual(
            js_args[2],
            {
                "label": "Test block",
                "description": "Some helpful text",
                "helpText": "Some helpful text",
                "required": True,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--char_field w-field--text_input",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_charblock_adapter_form_classname(self):
        """
        Meta data test for FormField; this checks if both the meta values
        form_classname and classname are accepted and are rendered
        in the form
        """
        block = blocks.CharBlock(form_classname="special-char-formclassname")

        block.set_name("test_block")
        js_args = FieldBlockAdapter().js_args(block)
        self.assertIn(" special-char-formclassname", js_args[2]["classname"])

        # Checks if it is backward compatible with classname
        block_with_classname = blocks.CharBlock(classname="special-char-classname")
        block_with_classname.set_name("test_block")
        js_args = FieldBlockAdapter().js_args(block_with_classname)
        self.assertIn(" special-char-classname", js_args[2]["classname"])

    def test_charblock_adapter_attrs(self):
        block = blocks.CharBlock(
            form_attrs={
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

        block.set_name("test_block")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_block")
        self.assertIsInstance(js_args[1], forms.TextInput)
        self.assertEqual(
            js_args[2].get("attrs"),
            {
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

    def test_charfield_render_with_template_with_extra_context(self):
        block = ContextCharBlock(template="tests/blocks/heading_block.html")
        html = block.render(
            "Bonjour le monde!",
            context={
                "language": "fr",
            },
        )

        self.assertEqual(html, '<h1 lang="fr">BONJOUR LE MONDE!</h1>')

    def test_charfield_get_form_state(self):
        block = blocks.CharBlock()
        form_state = block.get_form_state("Hello world!")

        self.assertEqual(form_state, "Hello world!")

    def test_charfield_searchable_content(self):
        block = blocks.CharBlock()
        content = block.get_searchable_content("Hello world!")

        self.assertEqual(content, ["Hello world!"])

    def test_search_index_searchable_content(self):
        block = blocks.CharBlock(search_index=False)
        content = block.get_searchable_content("Hello world!")

        self.assertEqual(content, [])

    def test_charfield_with_validator(self):
        def validate_is_foo(value):
            if value != "foo":
                raise ValidationError("Value must be 'foo'")

        block = blocks.CharBlock(validators=[validate_is_foo])

        with self.assertRaises(ValidationError):
            block.clean("bar")

    def test_charfield_with_callable_default(self):
        def callable_default():
            return "Hello world!"

        block = blocks.CharBlock(default=callable_default)
        self.assertEqual(block.get_default(), "Hello world!")

    def test_choicefield_render(self):
        class ChoiceBlock(blocks.FieldBlock):
            field = forms.ChoiceField(
                choices=(
                    ("choice-1", "Choice 1"),
                    ("choice-2", "Choice 2"),
                )
            )

        block = ChoiceBlock()
        html = block.render("choice-2")

        self.assertEqual(html, "choice-2")

    def test_adapt_custom_choicefield(self):
        class ChoiceBlock(blocks.FieldBlock):
            field = forms.ChoiceField(
                choices=(
                    ("choice-1", "Choice 1"),
                    ("choice-2", "Choice 2"),
                )
            )

        block = ChoiceBlock(description="A selection of two choices")

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_choiceblock")
        self.assertIsInstance(js_args[1], forms.Select)
        self.assertEqual(
            js_args[1].choices,
            [
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ],
        )
        self.assertEqual(
            js_args[2],
            {
                "label": "Test choiceblock",
                "description": "A selection of two choices",
                "required": True,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--choice_field w-field--select",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_searchable_content(self):
        """
        FieldBlock should not return anything for `get_searchable_content` by
        default. Subclasses are free to override it and provide relevant
        content.
        """

        class CustomBlock(blocks.FieldBlock):
            field = forms.CharField(required=True)

        block = CustomBlock()
        self.assertEqual(block.get_searchable_content("foo bar"), [])

    def test_form_handling_is_independent_of_serialisation(self):
        class Base64EncodingCharBlock(blocks.CharBlock):
            """A CharBlock with a deliberately perverse JSON (de)serialisation format
            so that it visibly blows up if we call to_python / get_prep_value where we shouldn't
            """

            def to_python(self, jsonish_value):
                # decode as base64 on the way out of the JSON serialisation
                return base64.b64decode(jsonish_value)

            def get_prep_value(self, native_value):
                # encode as base64 on the way into the JSON serialisation
                return base64.b64encode(native_value)

        block = Base64EncodingCharBlock()
        form_state = block.get_form_state("hello world")
        self.assertEqual(form_state, "hello world")

    def test_prepare_value_called(self):
        """
        Check that Field.prepare_value is called before sending the value to
        the widget for rendering.

        Actual real-world use case: A Youtube field that produces YoutubeVideo
        instances from IDs, but videos are entered using their full URLs.
        """

        class PrefixWrapper:
            prefix = "http://example.com/"

            def __init__(self, value):
                self.value = value

            def with_prefix(self):
                return self.prefix + self.value

            @classmethod
            def from_prefixed(cls, value):
                if not value.startswith(cls.prefix):
                    raise ValueError
                return cls(value[len(cls.prefix) :])

            def __eq__(self, other):
                return self.value == other.value

        class PrefixField(forms.Field):
            def clean(self, value):
                value = super().clean(value)
                return PrefixWrapper.from_prefixed(value)

            def prepare_value(self, value):
                return value.with_prefix()

        class PrefixedBlock(blocks.FieldBlock):
            def __init__(self, required=True, help_text="", **kwargs):
                super().__init__(**kwargs)
                self.field = PrefixField(required=required, help_text=help_text)

        block = PrefixedBlock()

        # Check that the form value is serialized with a prefix correctly
        value = PrefixWrapper("foo")
        form_state = block.get_form_state(value)

        self.assertEqual(form_state, "http://example.com/foo")

        # Check that the value was coerced back to a PrefixValue
        data = {"url": "http://example.com/bar"}
        new_value = block.clean(block.value_from_datadict(data, {}, "url"))
        self.assertEqual(new_value, PrefixWrapper("bar"))


class TestIntegerBlock(unittest.TestCase):
    def test_type(self):
        block = blocks.IntegerBlock()
        digit = block.value_from_form(1234)

        self.assertEqual(type(digit), int)

    def test_render(self):
        block = blocks.IntegerBlock()
        digit = block.value_from_form(1234)

        self.assertEqual(digit, 1234)

    def test_render_required_error(self):
        block = blocks.IntegerBlock()

        with self.assertRaises(ValidationError):
            block.clean("")

    def test_render_max_value_validation(self):
        block = blocks.IntegerBlock(max_value=20)

        with self.assertRaises(ValidationError):
            block.clean(25)

    def test_render_min_value_validation(self):
        block = blocks.IntegerBlock(min_value=20)

        with self.assertRaises(ValidationError):
            block.clean(10)

    def test_render_with_validator(self):
        def validate_is_even(value):
            if value % 2 > 0:
                raise ValidationError("Value must be even")

        block = blocks.IntegerBlock(validators=[validate_is_even])

        with self.assertRaises(ValidationError):
            block.clean(3)


class TestEmailBlock(unittest.TestCase):
    def test_render(self):
        block = blocks.EmailBlock()
        email = block.render("example@email.com")

        self.assertEqual(email, "example@email.com")

    def test_render_required_error(self):
        block = blocks.EmailBlock()

        with self.assertRaises(ValidationError):
            block.clean("")

    def test_format_validation(self):
        block = blocks.EmailBlock()

        with self.assertRaises(ValidationError):
            block.clean("example.email.com")

    def test_render_with_validator(self):
        def validate_is_example_domain(value):
            if not value.endswith("@example.com"):
                raise ValidationError("E-mail address must end in @example.com")

        block = blocks.EmailBlock(validators=[validate_is_example_domain])

        with self.assertRaises(ValidationError):
            block.clean("foo@example.net")


class TestBooleanBlock(unittest.TestCase):
    def test_get_form_state(self):
        block = blocks.BooleanBlock(required=False)
        form_state = block.get_form_state(True)
        self.assertIs(form_state, True)
        form_state = block.get_form_state(False)
        self.assertIs(form_state, False)


class TestBlockQuoteBlock(unittest.TestCase):
    def test_render(self):
        block = blocks.BlockQuoteBlock()
        quote = block.render("Now is the time...")

        self.assertEqual(quote, "<blockquote>Now is the time...</blockquote>")

    def test_render_with_validator(self):
        def validate_is_proper_story(value):
            if not value.startswith("Once upon a time"):
                raise ValidationError("Value must be a proper story")

        block = blocks.BlockQuoteBlock(validators=[validate_is_proper_story])

        with self.assertRaises(ValidationError):
            block.clean("A long, long time ago")


class TestFloatBlock(TestCase):
    def test_type(self):
        block = blocks.FloatBlock()
        block_val = block.value_from_form(float(1.63))
        self.assertEqual(type(block_val), float)

    def test_render(self):
        block = blocks.FloatBlock()
        test_val = float(1.63)
        block_val = block.value_from_form(test_val)
        self.assertEqual(block_val, test_val)

    def test_raises_required_error(self):
        block = blocks.FloatBlock()

        with self.assertRaises(ValidationError):
            block.clean("")

    def test_raises_max_value_validation_error(self):
        block = blocks.FloatBlock(max_value=20)

        with self.assertRaises(ValidationError):
            block.clean("20.01")

    def test_raises_min_value_validation_error(self):
        block = blocks.FloatBlock(min_value=20)

        with self.assertRaises(ValidationError):
            block.clean("19.99")

    def test_render_with_validator(self):
        def validate_is_even(value):
            if value % 2 > 0:
                raise ValidationError("Value must be even")

        block = blocks.FloatBlock(validators=[validate_is_even])

        with self.assertRaises(ValidationError):
            block.clean("3.0")


class TestDecimalBlock(TestCase):
    def test_type(self):
        block = blocks.DecimalBlock()
        block_val = block.value_from_form(Decimal("1.63"))
        self.assertEqual(type(block_val), Decimal)

    def test_type_to_python(self):
        block = blocks.DecimalBlock()
        block_val = block.to_python(
            "1.63"
        )  # decimals get saved as string in JSON field
        self.assertEqual(type(block_val), Decimal)

    def test_type_to_python_decimal_none_value(self):
        block = blocks.DecimalBlock()
        block_val = block.to_python(None)
        self.assertIsNone(block_val)

    def test_render(self):
        block = blocks.DecimalBlock()
        test_val = Decimal(1.63)
        block_val = block.value_from_form(test_val)

        self.assertEqual(block_val, test_val)

    def test_raises_required_error(self):
        block = blocks.DecimalBlock()

        with self.assertRaises(ValidationError):
            block.clean("")

    def test_raises_max_value_validation_error(self):
        block = blocks.DecimalBlock(max_value=20)

        with self.assertRaises(ValidationError):
            block.clean("20.01")

    def test_raises_min_value_validation_error(self):
        block = blocks.DecimalBlock(min_value=20)

        with self.assertRaises(ValidationError):
            block.clean("19.99")

    def test_render_with_validator(self):
        def validate_is_even(value):
            if value % 2 > 0:
                raise ValidationError("Value must be even")

        block = blocks.DecimalBlock(validators=[validate_is_even])

        with self.assertRaises(ValidationError):
            block.clean("3.0")

    def test_round_trip_to_db_preserves_type(self):
        block = blocks.DecimalBlock()
        original_value = Decimal(1.63)
        db_value = json.dumps(
            block.get_prep_value(original_value), cls=DjangoJSONEncoder
        )
        restored_value = block.to_python(json.loads(db_value))
        self.assertEqual(type(restored_value), Decimal)
        self.assertEqual(original_value, restored_value)


class TestRegexBlock(TestCase):
    def test_render(self):
        block = blocks.RegexBlock(regex=r"^[0-9]{3}$")
        test_val = "123"
        block_val = block.value_from_form(test_val)

        self.assertEqual(block_val, test_val)

    def test_raises_required_error(self):
        block = blocks.RegexBlock(regex=r"^[0-9]{3}$")

        with self.assertRaises(ValidationError) as context:
            block.clean("")

        self.assertIn("This field is required.", context.exception.messages)

    def test_raises_custom_required_error(self):
        test_message = "Oops, you missed a bit."
        block = blocks.RegexBlock(
            regex=r"^[0-9]{3}$",
            error_messages={
                "required": test_message,
            },
        )

        with self.assertRaises(ValidationError) as context:
            block.clean("")

        self.assertIn(test_message, context.exception.messages)

    def test_raises_validation_error(self):
        block = blocks.RegexBlock(regex=r"^[0-9]{3}$")

        with self.assertRaises(ValidationError) as context:
            block.clean("[/]")

        self.assertIn("Enter a valid value.", context.exception.messages)

    def test_raises_custom_error_message(self):
        test_message = "Not a valid library card number."
        block = blocks.RegexBlock(
            regex=r"^[0-9]{3}$", error_messages={"invalid": test_message}
        )

        with self.assertRaises(ValidationError) as context:
            block.clean("[/]")

        self.assertIn(test_message, context.exception.messages)

    def test_render_with_validator(self):
        def validate_is_foo(value):
            if value != "foo":
                raise ValidationError("Value must be 'foo'")

        block = blocks.RegexBlock(regex=r"^.*$", validators=[validate_is_foo])

        with self.assertRaises(ValidationError):
            block.clean("bar")


class TestRichTextBlock(TestCase):
    fixtures = ["test.json"]

    def test_get_default_with_fallback_value(self):
        default_value = blocks.RichTextBlock().get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "")

    def test_get_default_with_default_none(self):
        default_value = blocks.RichTextBlock(default=None).get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "")

    def test_get_default_with_empty_string(self):
        default_value = blocks.RichTextBlock(default="").get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "")

    def test_get_default_with_nonempty_string(self):
        default_value = blocks.RichTextBlock(default="<p>foo</p>").get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "<p>foo</p>")

    def test_get_default_with_localized_string(self):
        default_value = blocks.RichTextBlock(default=_("<p>english</p>")).get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "<p>english</p>")

    def test_get_default_with_richtext_value(self):
        default_value = blocks.RichTextBlock(
            default=RichText("<p>foo</p>")
        ).get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "<p>foo</p>")

    def test_get_default_with_callable(self):
        def callable_default():
            return RichText("<p>foo</p>")

        default_value = blocks.RichTextBlock(default=callable_default).get_default()
        self.assertIsInstance(default_value, RichText)
        self.assertEqual(default_value.source, "<p>foo</p>")

    def test_render(self):
        block = blocks.RichTextBlock()
        value = RichText('<p>Merry <a linktype="page" id="4">Christmas</a>!</p>')
        result = block.render(value)
        self.assertEqual(
            result, '<p>Merry <a href="/events/christmas/">Christmas</a>!</p>'
        )

    def test_adapter(self):
        from wagtail.test.testapp.rich_text import CustomRichTextArea

        block = blocks.RichTextBlock(editor="custom")

        block.set_name("test_richtextblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_richtextblock")
        self.assertIsInstance(js_args[1], CustomRichTextArea)
        self.assertEqual(
            js_args[2],
            {
                "classname": "w-field w-field--char_field w-field--custom_rich_text_area",
                "icon": "pilcrow",
                "label": "Test richtextblock",
                "description": "",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "attrs": {},
                "required": True,
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_adapter_with_draftail(self):
        from wagtail.admin.rich_text import DraftailRichTextArea

        block = blocks.RichTextBlock()

        block.set_name("test_richtextblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_richtextblock")
        self.assertIsInstance(js_args[1], DraftailRichTextArea)
        self.assertEqual(
            js_args[2],
            {
                "label": "Test richtextblock",
                "description": "",
                "required": True,
                "icon": "pilcrow",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--char_field w-field--draftail_rich_text_area",
                "attrs": {},
                "showAddCommentButton": False,  # Draftail manages its own comments
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_adapter_with_max_length(self):
        from wagtail.admin.rich_text import DraftailRichTextArea

        block = blocks.RichTextBlock(max_length=400)

        block.set_name("test_richtextblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_richtextblock")
        self.assertIsInstance(js_args[1], DraftailRichTextArea)
        self.assertEqual(
            js_args[2],
            {
                "label": "Test richtextblock",
                "description": "",
                "required": True,
                "icon": "pilcrow",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--char_field w-field--draftail_rich_text_area",
                "attrs": {},
                "showAddCommentButton": False,  # Draftail manages its own comments
                "strings": {"ADD_COMMENT": "Add Comment"},
                "maxLength": 400,
            },
        )

    def test_validate_required_richtext_block(self):
        block = blocks.RichTextBlock()

        with self.assertRaises(ValidationError):
            block.clean(RichText(""))

    def test_validate_non_required_richtext_block(self):
        block = blocks.RichTextBlock(required=False)
        result = block.clean(RichText(""))
        self.assertIsInstance(result, RichText)
        self.assertEqual(result.source, "")

    def test_render_with_validator(self):
        def validate_contains_foo(value):
            if "foo" not in value:
                raise ValidationError("Value must contain 'foo'")

        block = blocks.RichTextBlock(validators=[validate_contains_foo])

        with self.assertRaises(ValidationError):
            block.clean(RichText("<p>bar</p>"))

    def test_validate_max_length(self):
        block = blocks.RichTextBlock(max_length=20)

        block.clean(RichText("<p>short</p>"))

        with self.assertRaises(ValidationError):
            block.clean(RichText("<p>this exceeds the 20 character limit</p>"))

        # Case to test that markup is not counted towards length
        block.clean(
            RichText(
                '<p><a href="http://really-long-domain-name.example.com">also</a> short</p>'
            )
        )

    def test_validate_min_length(self):
        block = blocks.RichTextBlock(min_length=20)

        block.clean(RichText("<p>this passes the 20 character minimum</p>"))

        with self.assertRaises(ValidationError):
            block.clean(RichText("<p>too short</p>"))

        # Case to test that markup is not counted towards length
        with self.assertRaises(ValidationError):
            block.clean(
                RichText(
                    '<p><a href="http://really-long-domain-name.example.com">also</a> too short</p>'
                )
            )

    def test_get_searchable_content(self):
        block = blocks.RichTextBlock()
        value = RichText(
            '<p>Merry <a linktype="page" id="4">Christmas</a>! &amp; a happy new year</p>\n'
            "<p>Our Santa pet <b>Wagtail</b> has some cool stuff in store for you all!</p>"
        )
        result = block.get_searchable_content(value)
        self.assertEqual(
            result,
            [
                "Merry Christmas! & a happy new year \n"
                "Our Santa pet Wagtail has some cool stuff in store for you all!"
            ],
        )

    def test_search_index_get_searchable_content(self):
        block = blocks.RichTextBlock(search_index=False)
        value = RichText(
            '<p>Merry <a linktype="page" id="4">Christmas</a>! &amp; a happy new year</p>\n'
            "<p>Our Santa pet <b>Wagtail</b> has some cool stuff in store for you all!</p>"
        )
        result = block.get_searchable_content(value)
        self.assertEqual(
            result,
            [],
        )

    def test_get_searchable_content_whitespace(self):
        block = blocks.RichTextBlock()
        value = RichText("<p>mashed</p><p>po<i>ta</i>toes</p>")
        result = block.get_searchable_content(value)
        self.assertEqual(result, ["mashed potatoes"])

    def test_extract_references(self):
        block = blocks.RichTextBlock()
        value = RichText('<a linktype="page" id="1">Link to an internal page</a>')

        self.assertEqual(list(block.extract_references(value)), [(Page, "1", "", "")])

    def test_normalize(self):
        block = blocks.RichTextBlock()
        for value in ("Hello, world", RichText("Hello, world")):
            with self.subTest(value=value):
                normalized = block.normalize(value)
                self.assertIsInstance(normalized, RichText)
                self.assertEqual(normalized.source, "Hello, world")


class TestChoiceBlock(WagtailTestUtils, SimpleTestCase):
    def setUp(self):
        from django.db.models.fields import BLANK_CHOICE_DASH

        self.blank_choice_dash_label = BLANK_CHOICE_DASH[0][1]

    def test_adapt_choice_block(self):
        block = blocks.ChoiceBlock(choices=[("tea", "Tea"), ("coffee", "Coffee")])

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_choiceblock")
        self.assertIsInstance(js_args[1], forms.Select)
        self.assertEqual(
            list(js_args[1].choices),
            [("", "---------"), ("tea", "Tea"), ("coffee", "Coffee")],
        )
        self.assertEqual(
            js_args[2],
            {
                "label": "Test choiceblock",
                "description": "",
                "required": True,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--choice_field w-field--select",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_choice_block_with_default(self):
        block = blocks.ChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], default="tea"
        )
        self.assertEqual(block.get_default(), "tea")

    def test_adapt_choice_block_with_callable_choices(self):
        def callable_choices():
            return [("tea", "Tea"), ("coffee", "Coffee")]

        block = blocks.ChoiceBlock(choices=callable_choices)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertIsInstance(js_args[1], forms.Select)
        self.assertEqual(
            list(js_args[1].choices),
            [("", "---------"), ("tea", "Tea"), ("coffee", "Coffee")],
        )

    def test_validate_required_choice_block(self):
        block = blocks.ChoiceBlock(choices=[("tea", "Tea"), ("coffee", "Coffee")])
        self.assertEqual(block.clean("coffee"), "coffee")

        with self.assertRaises(ValidationError):
            block.clean("whisky")

        with self.assertRaises(ValidationError):
            block.clean("")

        with self.assertRaises(ValidationError):
            block.clean(None)

    def test_adapt_non_required_choice_block(self):
        block = blocks.ChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], required=False
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertFalse(js_args[2]["required"])

    def test_validate_non_required_choice_block(self):
        block = blocks.ChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], required=False
        )
        self.assertEqual(block.clean("coffee"), "coffee")

        with self.assertRaises(ValidationError):
            block.clean("whisky")

        self.assertEqual(block.clean(""), "")
        self.assertEqual(block.clean(None), "")

    def test_adapt_choice_block_with_existing_blank_choice(self):
        block = blocks.ChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")],
            required=False,
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")],
        )

    def test_adapt_choice_block_with_existing_blank_choice_and_with_callable_choices(
        self,
    ):
        def callable_choices():
            return [("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")]

        block = blocks.ChoiceBlock(choices=callable_choices, required=False)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")],
        )

    def test_named_groups_without_blank_option(self):
        block = blocks.ChoiceBlock(
            choices=[
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
            ]
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                ("", "---------"),
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
            ],
        )

    def test_named_groups_with_blank_option(self):
        block = blocks.ChoiceBlock(
            choices=[
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
                ("Not thirsty", [("", "No thanks")]),
            ],
            required=False,
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                # Blank option not added
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
                ("Not thirsty", [("", "No thanks")]),
            ],
        )

    def test_subclassing(self):
        class BeverageChoiceBlock(blocks.ChoiceBlock):
            choices = [
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ]

        block = BeverageChoiceBlock(required=False)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                ("", "---------"),
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ],
        )

        # subclasses of ChoiceBlock should deconstruct to a basic ChoiceBlock for migrations
        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.ChoiceBlock",
                [],
                {
                    "choices": [("tea", "Tea"), ("coffee", "Coffee")],
                    "required": False,
                },
            ),
        )

    def test_searchable_content(self):
        block = blocks.ChoiceBlock(
            choices=[
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ]
        )
        self.assertEqual(block.get_searchable_content("choice-1"), ["Choice 1"])

    def test_search_index_searchable_content(self):
        block = blocks.ChoiceBlock(
            choices=[
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ],
            search_index=False,
        )
        self.assertEqual(block.get_searchable_content("choice-1"), [])

    def test_searchable_content_with_callable_choices(self):
        def callable_choices():
            return [
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ]

        block = blocks.ChoiceBlock(choices=callable_choices)
        self.assertEqual(block.get_searchable_content("choice-1"), ["Choice 1"])

    def test_optgroup_searchable_content(self):
        block = blocks.ChoiceBlock(
            choices=[
                (
                    "Section 1",
                    [
                        ("1-1", "Block 1"),
                        ("1-2", "Block 2"),
                    ],
                ),
                (
                    "Section 2",
                    [
                        ("2-1", "Block 1"),
                        ("2-2", "Block 2"),
                    ],
                ),
            ]
        )
        self.assertEqual(block.get_searchable_content("2-2"), ["Section 2", "Block 2"])

    def test_invalid_searchable_content(self):
        block = blocks.ChoiceBlock(
            choices=[
                ("one", "One"),
                ("two", "Two"),
            ]
        )
        self.assertEqual(block.get_searchable_content("three"), [])

    def test_searchable_content_with_lazy_translation(self):
        block = blocks.ChoiceBlock(
            choices=[
                ("choice-1", _("Choice 1")),
                ("choice-2", _("Choice 2")),
            ]
        )
        result = block.get_searchable_content("choice-1")
        # result must survive JSON (de)serialisation, which is not the case for
        # lazy translation objects
        result = json.loads(json.dumps(result))
        self.assertEqual(result, ["Choice 1"])

    def test_optgroup_searchable_content_with_lazy_translation(self):
        block = blocks.ChoiceBlock(
            choices=[
                (
                    _("Section 1"),
                    [
                        ("1-1", _("Block 1")),
                        ("1-2", _("Block 2")),
                    ],
                ),
                (
                    _("Section 2"),
                    [
                        ("2-1", _("Block 1")),
                        ("2-2", _("Block 2")),
                    ],
                ),
            ]
        )
        result = block.get_searchable_content("2-2")
        # result must survive JSON (de)serialisation, which is not the case for
        # lazy translation objects
        result = json.loads(json.dumps(result))
        self.assertEqual(result, ["Section 2", "Block 2"])

    def test_deconstruct_with_callable_choices(self):
        def callable_choices():
            return [
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ]

        block = blocks.ChoiceBlock(choices=callable_choices, required=False)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                ("", "---------"),
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ],
        )

        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.ChoiceBlock",
                [],
                {
                    "choices": callable_choices,
                    "required": False,
                },
            ),
        )

    def test_render_with_validator(self):
        choices = [
            ("tea", "Tea"),
            ("coffee", "Coffee"),
        ]

        def validate_tea_is_selected(value):
            raise ValidationError("You must select 'tea'")

        block = blocks.ChoiceBlock(
            choices=choices, validators=[validate_tea_is_selected]
        )

        with self.assertRaises(ValidationError):
            block.clean("coffee")

    def test_get_form_state(self):
        block = blocks.ChoiceBlock(choices=[("tea", "Tea"), ("coffee", "Coffee")])
        form_state = block.get_form_state("tea")
        self.assertEqual(form_state, ["tea"])

    def test_get_form_state_with_radio_widget(self):
        block = blocks.ChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], widget=forms.RadioSelect
        )
        form_state = block.get_form_state("tea")
        self.assertEqual(form_state, ["tea"])


class TestMultipleChoiceBlock(WagtailTestUtils, SimpleTestCase):
    def setUp(self):
        from django.db.models.fields import BLANK_CHOICE_DASH

        self.blank_choice_dash_label = BLANK_CHOICE_DASH[0][1]

    def test_adapt_multiple_choice_block(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")]
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_choiceblock")
        self.assertIsInstance(js_args[1], forms.Select)
        self.assertEqual(
            list(js_args[1].choices), [("tea", "Tea"), ("coffee", "Coffee")]
        )
        self.assertEqual(
            js_args[2],
            {
                "label": "Test choiceblock",
                "description": "",
                "required": True,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--multiple_choice_field w-field--select_multiple",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_multiple_choice_block_with_default(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], default="tea"
        )
        self.assertEqual(block.get_default(), "tea")

    def test_adapt_multiple_choice_block_with_callable_choices(self):
        def callable_choices():
            return [("tea", "Tea"), ("coffee", "Coffee")]

        block = blocks.MultipleChoiceBlock(choices=callable_choices)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertIsInstance(js_args[1], forms.Select)
        self.assertEqual(
            list(js_args[1].choices), [("tea", "Tea"), ("coffee", "Coffee")]
        )

    def test_validate_required_multiple_choice_block(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")]
        )
        self.assertEqual(block.clean(["coffee"]), ["coffee"])

        with self.assertRaises(ValidationError):
            block.clean(["whisky"])

        with self.assertRaises(ValidationError):
            block.clean("")

        with self.assertRaises(ValidationError):
            block.clean(None)

    def test_adapt_non_required_multiple_choice_block(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], required=False
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertFalse(js_args[2]["required"])

    def test_validate_non_required_multiple_choice_block(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")], required=False
        )
        self.assertEqual(block.clean(["coffee"]), ["coffee"])

        with self.assertRaises(ValidationError):
            block.clean(["whisky"])

        self.assertEqual(block.clean(""), [])
        self.assertEqual(block.clean(None), [])

    def test_adapt_multiple_choice_block_with_existing_blank_choice(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")],
            required=False,
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")],
        )

    def test_adapt_multiple_choice_block_with_existing_blank_choice_and_with_callable_choices(
        self,
    ):
        def callable_choices():
            return [("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")]

        block = blocks.MultipleChoiceBlock(choices=callable_choices, required=False)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [("tea", "Tea"), ("coffee", "Coffee"), ("", "No thanks")],
        )

    def test_named_groups_without_blank_option(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
            ]
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
            ],
        )

    def test_named_groups_with_blank_option(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
                ("Not thirsty", [("", "No thanks")]),
            ],
            required=False,
        )

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                (
                    "Alcoholic",
                    [
                        ("gin", "Gin"),
                        ("whisky", "Whisky"),
                    ],
                ),
                (
                    "Non-alcoholic",
                    [
                        ("tea", "Tea"),
                        ("coffee", "Coffee"),
                    ],
                ),
                ("Not thirsty", [("", "No thanks")]),
            ],
        )

    def test_subclassing(self):
        class BeverageMultipleChoiceBlock(blocks.MultipleChoiceBlock):
            choices = [
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ]

        block = BeverageMultipleChoiceBlock(required=False)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ],
        )

        # subclasses of ChoiceBlock should deconstruct to a basic ChoiceBlock for migrations
        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.MultipleChoiceBlock",
                [],
                {
                    "choices": [("tea", "Tea"), ("coffee", "Coffee")],
                    "required": False,
                },
            ),
        )

    def test_searchable_content(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ]
        )
        self.assertEqual(block.get_searchable_content("choice-1"), ["Choice 1"])

    def test_search_index_searchable_content(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ],
            search_index=False,
        )
        self.assertEqual(block.get_searchable_content("choice-1"), [])

    def test_searchable_content_with_callable_choices(self):
        def callable_choices():
            return [
                ("choice-1", "Choice 1"),
                ("choice-2", "Choice 2"),
            ]

        block = blocks.MultipleChoiceBlock(choices=callable_choices)
        self.assertEqual(block.get_searchable_content("choice-1"), ["Choice 1"])

    def test_optgroup_searchable_content(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                (
                    "Section 1",
                    [
                        ("1-1", "Block 1"),
                        ("1-2", "Block 2"),
                    ],
                ),
                (
                    "Section 2",
                    [
                        ("2-1", "Block 1"),
                        ("2-2", "Block 2"),
                    ],
                ),
            ]
        )
        self.assertEqual(block.get_searchable_content("2-2"), ["Section 2", "Block 2"])

    def test_invalid_searchable_content(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                ("one", "One"),
                ("two", "Two"),
            ]
        )
        self.assertEqual(block.get_searchable_content("three"), [])

    def test_searchable_content_with_lazy_translation(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                ("choice-1", _("Choice 1")),
                ("choice-2", _("Choice 2")),
            ]
        )
        result = block.get_searchable_content("choice-1")
        # result must survive JSON (de)serialisation, which is not the case for
        # lazy translation objects
        result = json.loads(json.dumps(result))
        self.assertEqual(result, ["Choice 1"])

    def test_optgroup_searchable_content_with_lazy_translation(self):
        block = blocks.MultipleChoiceBlock(
            choices=[
                (
                    _("Section 1"),
                    [
                        ("1-1", _("Block 1")),
                        ("1-2", _("Block 2")),
                    ],
                ),
                (
                    _("Section 2"),
                    [
                        ("2-1", _("Block 1")),
                        ("2-2", _("Block 2")),
                    ],
                ),
            ]
        )
        result = block.get_searchable_content("2-2")
        # result must survive JSON (de)serialisation, which is not the case for
        # lazy translation objects
        result = json.loads(json.dumps(result))
        self.assertEqual(result, ["Section 2", "Block 2"])

    def test_deconstruct_with_callable_choices(self):
        def callable_choices():
            return [
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ]

        block = blocks.MultipleChoiceBlock(choices=callable_choices, required=False)

        block.set_name("test_choiceblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(
            list(js_args[1].choices),
            [
                ("tea", "Tea"),
                ("coffee", "Coffee"),
            ],
        )

        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.MultipleChoiceBlock",
                [],
                {
                    "choices": callable_choices,
                    "required": False,
                },
            ),
        )

    def test_render_with_validator(self):
        choices = [
            ("tea", "Tea"),
            ("coffee", "Coffee"),
        ]

        def validate_tea_is_selected(value):
            raise ValidationError("You must select 'tea'")

        block = blocks.MultipleChoiceBlock(
            choices=choices, validators=[validate_tea_is_selected]
        )

        with self.assertRaises(ValidationError):
            block.clean("coffee")

    def test_get_form_state(self):
        block = blocks.MultipleChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")]
        )
        form_state = block.get_form_state(["tea", "coffee"])
        self.assertEqual(form_state, ["tea", "coffee"])

    def test_get_form_state_with_checkbox_widget(self):
        block = blocks.ChoiceBlock(
            choices=[("tea", "Tea"), ("coffee", "Coffee")],
            widget=forms.CheckboxSelectMultiple,
        )
        form_state = block.get_form_state(["tea", "coffee"])
        self.assertEqual(form_state, ["tea", "coffee"])


class TestRawHTMLBlock(unittest.TestCase):
    def test_get_default_with_fallback_value(self):
        default_value = blocks.RawHTMLBlock().get_default()
        self.assertEqual(default_value, "")
        self.assertIsInstance(default_value, SafeData)

    def test_get_default_with_none(self):
        default_value = blocks.RawHTMLBlock(default=None).get_default()
        self.assertEqual(default_value, "")
        self.assertIsInstance(default_value, SafeData)

    def test_get_default_with_empty_string(self):
        default_value = blocks.RawHTMLBlock(default="").get_default()
        self.assertEqual(default_value, "")
        self.assertIsInstance(default_value, SafeData)

    def test_get_default_with_nonempty_string(self):
        default_value = blocks.RawHTMLBlock(default="<blink>BÖÖM</blink>").get_default()
        self.assertEqual(default_value, "<blink>BÖÖM</blink>")
        self.assertIsInstance(default_value, SafeData)

    def test_get_default_with_callable(self):
        def callable_default():
            return "<blink>BÖÖM</blink>"

        default_value = blocks.RawHTMLBlock(default=callable_default).get_default()
        self.assertEqual(default_value, "<blink>BÖÖM</blink>")
        self.assertIsInstance(default_value, SafeData)

    def test_serialize(self):
        block = blocks.RawHTMLBlock()
        result = block.get_prep_value(mark_safe("<blink>BÖÖM</blink>"))
        self.assertEqual(result, "<blink>BÖÖM</blink>")
        self.assertNotIsInstance(result, SafeData)

    def test_deserialize(self):
        block = blocks.RawHTMLBlock()
        result = block.to_python("<blink>BÖÖM</blink>")
        self.assertEqual(result, "<blink>BÖÖM</blink>")
        self.assertIsInstance(result, SafeData)

    def test_render(self):
        block = blocks.RawHTMLBlock()
        result = block.render(mark_safe("<blink>BÖÖM</blink>"))
        self.assertEqual(result, "<blink>BÖÖM</blink>")
        self.assertIsInstance(result, SafeData)

    def test_get_form_state(self):
        block = blocks.RawHTMLBlock()
        form_state = block.get_form_state("<blink>BÖÖM</blink>")

        self.assertEqual(form_state, "<blink>BÖÖM</blink>")

    def test_adapt(self):
        block = blocks.RawHTMLBlock()

        block.set_name("test_rawhtmlblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_rawhtmlblock")
        self.assertIsInstance(js_args[1], forms.Textarea)
        self.assertEqual(js_args[1].attrs, {"cols": "40", "rows": "10"})
        self.assertEqual(
            js_args[2],
            {
                "label": "Test rawhtmlblock",
                "description": "",
                "required": True,
                "icon": "code",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--char_field w-field--textarea",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_form_response(self):
        block = blocks.RawHTMLBlock()
        result = block.value_from_datadict(
            {"rawhtml": "<blink>BÖÖM</blink>"}, {}, prefix="rawhtml"
        )
        self.assertEqual(result, "<blink>BÖÖM</blink>")
        self.assertIsInstance(result, SafeData)

    def test_value_omitted_from_data(self):
        block = blocks.RawHTMLBlock()
        self.assertFalse(
            block.value_omitted_from_data({"rawhtml": "ohai"}, {}, "rawhtml")
        )
        self.assertFalse(block.value_omitted_from_data({"rawhtml": ""}, {}, "rawhtml"))
        self.assertTrue(
            block.value_omitted_from_data({"nothing-here": "nope"}, {}, "rawhtml")
        )

    def test_clean_required_field(self):
        block = blocks.RawHTMLBlock()
        result = block.clean(mark_safe("<blink>BÖÖM</blink>"))
        self.assertEqual(result, "<blink>BÖÖM</blink>")
        self.assertIsInstance(result, SafeData)

        with self.assertRaises(ValidationError):
            block.clean(mark_safe(""))

    def test_clean_nonrequired_field(self):
        block = blocks.RawHTMLBlock(required=False)
        result = block.clean(mark_safe("<blink>BÖÖM</blink>"))
        self.assertEqual(result, "<blink>BÖÖM</blink>")
        self.assertIsInstance(result, SafeData)

        result = block.clean(mark_safe(""))
        self.assertEqual(result, "")
        self.assertIsInstance(result, SafeData)

    def test_render_with_validator(self):
        def validate_contains_foo(value):
            if "foo" not in value:
                raise ValidationError("Value must contain 'foo'")

        block = blocks.RawHTMLBlock(validators=[validate_contains_foo])

        with self.assertRaises(ValidationError):
            block.clean(mark_safe("<p>bar</p>"))


class TestMeta(unittest.TestCase):
    def test_set_template_with_meta(self):
        class HeadingBlock(blocks.CharBlock):
            class Meta:
                template = "heading.html"

        block = HeadingBlock()
        self.assertEqual(block.meta.template, "heading.html")

    def test_set_template_with_constructor(self):
        block = blocks.CharBlock(template="heading.html")
        self.assertEqual(block.meta.template, "heading.html")

    def test_set_template_with_constructor_overrides_meta(self):
        class HeadingBlock(blocks.CharBlock):
            class Meta:
                template = "heading.html"

        block = HeadingBlock(template="subheading.html")
        self.assertEqual(block.meta.template, "subheading.html")

    def test_meta_nested_inheritance(self):
        """
        Check that having a multi-level inheritance chain works
        """

        class HeadingBlock(blocks.CharBlock):
            class Meta:
                template = "heading.html"
                test = "Foo"

        class SubHeadingBlock(HeadingBlock):
            class Meta:
                template = "subheading.html"

        block = SubHeadingBlock()
        self.assertEqual(block.meta.template, "subheading.html")
        self.assertEqual(block.meta.test, "Foo")

    def test_meta_multi_inheritance(self):
        """
        Check that multi-inheritance and Meta classes work together
        """

        class LeftBlock(blocks.CharBlock):
            class Meta:
                template = "template.html"
                clash = "the band"
                label = "Left block"

        class RightBlock(blocks.CharBlock):
            class Meta:
                default = "hello"
                clash = "the album"
                label = "Right block"

        class ChildBlock(LeftBlock, RightBlock):
            class Meta:
                label = "Child block"

        block = ChildBlock()
        # These should be directly inherited from the LeftBlock/RightBlock
        self.assertEqual(block.meta.template, "template.html")
        self.assertEqual(block.meta.default, "hello")

        # This should be inherited from the LeftBlock, solving the collision,
        # as LeftBlock comes first
        self.assertEqual(block.meta.clash, "the band")

        # This should come from ChildBlock itself, ignoring the label on
        # LeftBlock/RightBlock
        self.assertEqual(block.meta.label, "Child block")


class TestStructBlock(SimpleTestCase):
    def test_initialisation(self):
        block = blocks.StructBlock(
            [
                ("title", blocks.CharBlock()),
                ("link", blocks.URLBlock()),
            ]
        )

        self.assertEqual(list(block.child_blocks.keys()), ["title", "link"])

    def test_initialisation_from_subclass(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock()

        self.assertEqual(list(block.child_blocks.keys()), ["title", "link"])

    def test_initialisation_from_subclass_with_extra(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock([("classname", blocks.CharBlock())])

        self.assertEqual(
            list(block.child_blocks.keys()), ["title", "link", "classname"]
        )

    def test_initialisation_with_multiple_subclassses(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        class StyledLinkBlock(LinkBlock):
            classname = blocks.CharBlock()

        block = StyledLinkBlock()

        self.assertEqual(
            list(block.child_blocks.keys()), ["title", "link", "classname"]
        )

    def test_initialisation_with_mixins(self):
        """
        The order of fields of classes with multiple parent classes is slightly
        surprising at first. Child fields are inherited in a bottom-up order,
        by traversing the MRO in reverse. In the example below,
        ``StyledLinkBlock`` will have an MRO of::

            [StyledLinkBlock, StylingMixin, LinkBlock, StructBlock, ...]

        This will result in ``classname`` appearing *after* ``title`` and
        ``link`` in ``StyleLinkBlock`.child_blocks`, even though
        ``StylingMixin`` appeared before ``LinkBlock``.
        """

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        class StylingMixin(blocks.StructBlock):
            classname = blocks.CharBlock()

        class StyledLinkBlock(StylingMixin, LinkBlock):
            source = blocks.CharBlock()

        block = StyledLinkBlock()

        self.assertEqual(
            list(block.child_blocks.keys()), ["title", "link", "classname", "source"]
        )

    def test_render(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock()
        html = block.render(
            block.to_python(
                {
                    "title": "Wagtail site",
                    "link": "http://www.wagtail.org",
                }
            )
        )
        expected_html = "\n".join(
            [
                "<dl>",
                "<dt>title</dt>",
                "<dd>Wagtail site</dd>",
                "<dt>link</dt>",
                "<dd>http://www.wagtail.org</dd>",
                "</dl>",
            ]
        )

        self.assertHTMLEqual(html, expected_html)

    def test_get_api_representation_calls_same_method_on_fields_with_context(self):
        """
        The get_api_representation method of a StructBlock should invoke
        the block's get_api_representation method on each field and the
        context should be passed on.
        """

        class ContextBlock(blocks.CharBlock):
            def get_api_representation(self, value, context=None):
                return context[value]

        class AuthorBlock(blocks.StructBlock):
            language = ContextBlock()
            author = ContextBlock()

        block = AuthorBlock()
        api_representation = block.get_api_representation(
            {
                "language": "en",
                "author": "wagtail",
            },
            context={"en": "English", "wagtail": "Wagtail!"},
        )

        self.assertDictEqual(
            api_representation, {"language": "English", "author": "Wagtail!"}
        )

    def test_render_unknown_field(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock()
        html = block.render(
            block.to_python(
                {
                    "title": "Wagtail site",
                    "link": "http://www.wagtail.org",
                    "image": 10,
                }
            )
        )

        self.assertIn("<dt>title</dt>", html)
        self.assertIn("<dd>Wagtail site</dd>", html)
        self.assertIn("<dt>link</dt>", html)
        self.assertIn("<dd>http://www.wagtail.org</dd>", html)

        # Don't render the extra item
        self.assertNotIn("<dt>image</dt>", html)

    def test_render_bound_block(self):
        # the string representation of a bound block should be the value as rendered by
        # the associated block
        class SectionBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            body = blocks.RichTextBlock()

        block = SectionBlock()
        struct_value = block.to_python(
            {
                "title": "hello",
                "body": "<b>world</b>",
            }
        )
        body_bound_block = struct_value.bound_blocks["body"]
        expected = "<b>world</b>"
        self.assertEqual(str(body_bound_block), expected)

    def test_get_form_context(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock()
        context = block.get_form_context(
            block.to_python(
                {
                    "title": "Wagtail site",
                    "link": "http://www.wagtail.org",
                }
            ),
            prefix="mylink",
        )

        self.assertIsInstance(context["children"], collections.OrderedDict)
        self.assertEqual(len(context["children"]), 2)
        self.assertIsInstance(context["children"]["title"], blocks.BoundBlock)
        self.assertEqual(context["children"]["title"].value, "Wagtail site")
        self.assertIsInstance(context["children"]["link"], blocks.BoundBlock)
        self.assertEqual(context["children"]["link"].value, "http://www.wagtail.org")
        self.assertEqual(context["block_definition"], block)
        self.assertEqual(context["prefix"], "mylink")

    def test_adapt(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock(required=False)
            link = blocks.URLBlock(required=False)

        block = LinkBlock()

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_structblock")
        self.assertEqual(
            js_args[2],
            {
                "label": "Test structblock",
                "description": "",
                "required": False,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "struct-block",
                "collapsed": False,
                "attrs": {},
            },
        )

        self.assertEqual(len(js_args[1]), 2)
        title_field, link_field = js_args[1]

        self.assertEqual(title_field, block.child_blocks["title"])
        self.assertEqual(link_field, block.child_blocks["link"])

    def test_adapt_with_form_template(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock(required=False)
            link = blocks.URLBlock(required=False)

            class Meta:
                form_template = "tests/block_forms/struct_block_form_template.html"

        block = LinkBlock()

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[2],
            {
                "label": "Test structblock",
                "description": "",
                "required": False,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "struct-block",
                "collapsed": False,
                "attrs": {},
                "formTemplate": "<div>Hello</div>",
            },
        )

    def test_adapt_with_form_attrs(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock(required=False)
            link = blocks.URLBlock(required=False)

        block = LinkBlock(
            form_attrs={
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            }
        )

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_structblock")
        self.assertEqual(
            js_args[2].get("attrs"),
            {
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

    def test_adapt_with_form_attrs_on_meta(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock(required=False)
            link = blocks.URLBlock(required=False)

            class Meta:
                form_attrs = {
                    "data-controller": "w-custom",
                    "data-action": "click->w-custom#doSomething",
                }

        block = LinkBlock()

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_structblock")
        self.assertEqual(
            js_args[2].get("attrs"),
            {
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

    def test_adapt_with_form_template_jinja(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock(required=False)
            link = blocks.URLBlock(required=False)

            class Meta:
                form_template = "tests/jinja2/struct_block_form_template.html"

        block = LinkBlock()

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[2],
            {
                "label": "Test structblock",
                "description": "",
                "required": False,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "struct-block",
                "collapsed": False,
                "attrs": {},
                "formTemplate": "<div>Hello</div>",
            },
        )

    def test_get_default(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock(default="Torchbox")
            link = blocks.URLBlock(default="http://www.torchbox.com")

        block = LinkBlock()
        default_val = block.get_default()
        self.assertEqual(default_val.get("title"), "Torchbox")

    def test_get_default_with_callable(self):
        def callable_struct_default():
            return {"title": "Torchbox"}

        def default_link():
            return "http://www.torchbox.com"

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock(default=default_link)

        block = LinkBlock(default=callable_struct_default)
        default_val = block.get_default()

        # Should combine the defaults from the StructBlock's default and the
        # child block's default, and allow them both to be callable
        self.assertEqual(default_val.get("title"), "Torchbox")
        self.assertEqual(default_val.get("link"), "http://www.torchbox.com")

    def test_adapt_with_help_text_on_meta(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

            class Meta:
                help_text = "Self-promotion is encouraged"

        block = LinkBlock()

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[2],
            {
                "label": "Test structblock",
                "description": "Self-promotion is encouraged",
                "required": False,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "struct-block",
                "collapsed": False,
                "attrs": {},
                "helpIcon": (
                    '<svg class="icon icon-help default" aria-hidden="true">'
                    '<use href="#icon-help"></use></svg>'
                ),
                "helpText": "Self-promotion is encouraged",
            },
        )

    def test_adapt_with_help_text_as_argument(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock(help_text="Self-promotion is encouraged")

        block.set_name("test_structblock")
        js_args = StructBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[2],
            {
                "label": "Test structblock",
                "description": "Self-promotion is encouraged",
                "required": False,
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "struct-block",
                "collapsed": False,
                "attrs": {},
                "helpIcon": (
                    '<svg class="icon icon-help default" aria-hidden="true">'
                    '<use href="#icon-help"></use></svg>'
                ),
                "helpText": "Self-promotion is encouraged",
            },
        )

    def test_adapt_with_collapsed(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        cases = [None, False, True]
        for case in cases:
            with self.subTest(collapsed=case):
                block = LinkBlock(collapsed=case)

                block.set_name("test_structblock")
                js_args = StructBlockAdapter().js_args(block)

                self.assertIs(js_args[2]["collapsed"], case)

    def test_searchable_content(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = LinkBlock()
        content = block.get_searchable_content(
            block.to_python(
                {
                    "title": "Wagtail site",
                    "link": "http://www.wagtail.org",
                }
            )
        )

        self.assertEqual(content, ["Wagtail site"])

    def test_value_from_datadict(self):
        block = blocks.StructBlock(
            [
                ("title", blocks.CharBlock()),
                ("link", blocks.URLBlock()),
            ]
        )

        struct_val = block.value_from_datadict(
            {"mylink-title": "Torchbox", "mylink-link": "http://www.torchbox.com"},
            {},
            "mylink",
        )

        self.assertEqual(struct_val["title"], "Torchbox")
        self.assertEqual(struct_val["link"], "http://www.torchbox.com")
        self.assertIsInstance(struct_val, blocks.StructValue)
        self.assertIsInstance(struct_val.bound_blocks["link"].block, blocks.URLBlock)

    def test_value_omitted_from_data(self):
        block = blocks.StructBlock(
            [
                ("title", blocks.CharBlock()),
                ("link", blocks.URLBlock()),
            ]
        )

        # overall value is considered present in the form if any sub-field is present
        self.assertFalse(
            block.value_omitted_from_data({"mylink-title": "Torchbox"}, {}, "mylink")
        )
        self.assertTrue(
            block.value_omitted_from_data({"nothing-here": "nope"}, {}, "mylink")
        )

    def test_default_is_returned_as_structvalue(self):
        """When returning the default value of a StructBlock (e.g. because it's
        a child of another StructBlock, and the outer value is missing that key)
        we should receive it as a StructValue, not just a plain dict"""

        class PersonBlock(blocks.StructBlock):
            first_name = blocks.CharBlock()
            surname = blocks.CharBlock()

        class EventBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            guest_speaker = PersonBlock(
                default={"first_name": "Ed", "surname": "Balls"}
            )

        event_block = EventBlock()

        event = event_block.to_python({"title": "Birthday party"})

        self.assertEqual(event["guest_speaker"]["first_name"], "Ed")
        self.assertIsInstance(event["guest_speaker"], blocks.StructValue)

    def test_default_value_is_distinct_instance(self):
        """
        Whenever the default value of a StructBlock is invoked, it should be a distinct
        instance of the dict so that modifying it doesn't modify other places where the
        default value appears.
        """

        class PersonBlock(blocks.StructBlock):
            first_name = blocks.CharBlock()
            surname = blocks.CharBlock()

        class EventBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            guest_speaker = PersonBlock(
                default={"first_name": "Ed", "surname": "Balls"}
            )

        event_block = EventBlock()

        event1 = event_block.to_python(
            {"title": "Birthday party"}
        )  # guest_speaker will default to Ed Balls
        event2 = event_block.to_python(
            {"title": "Christmas party"}
        )  # guest_speaker will default to Ed Balls, but a distinct instance

        event1["guest_speaker"]["surname"] = "Miliband"
        self.assertEqual(event1["guest_speaker"]["surname"], "Miliband")
        # event2 should not be modified
        self.assertEqual(event2["guest_speaker"]["surname"], "Balls")

    def test_bulk_to_python_returns_distinct_default_instances(self):
        """
        Whenever StructBlock.bulk_to_python invokes a child block's get_default method to
        fill in missing fields, it should use a separate invocation for each record so that
        we don't end up with the same instance of a mutable value on multiple records
        """

        class ShoppingListBlock(blocks.StructBlock):
            shop = blocks.CharBlock()
            items = blocks.ListBlock(blocks.CharBlock(default="chocolate"))

        block = ShoppingListBlock()

        shopping_lists = block.bulk_to_python(
            [
                {"shop": "Tesco"},  # 'items' defaults to ['chocolate']
                {
                    "shop": "Asda"
                },  # 'items' defaults to ['chocolate'], but a distinct instance
            ]
        )

        shopping_lists[0]["items"].append("cake")
        self.assertEqual(list(shopping_lists[0]["items"]), ["chocolate", "cake"])
        # shopping_lists[1] should not be updated
        self.assertEqual(list(shopping_lists[1]["items"]), ["chocolate"])

    def test_clean(self):
        block = blocks.StructBlock(
            [
                ("title", blocks.CharBlock()),
                ("link", blocks.URLBlock()),
            ]
        )

        value = block.to_python(
            {"title": "Torchbox", "link": "http://www.torchbox.com/"}
        )
        clean_value = block.clean(value)
        self.assertIsInstance(clean_value, blocks.StructValue)
        self.assertEqual(clean_value["title"], "Torchbox")

        value = block.to_python({"title": "Torchbox", "link": "not a url"})
        with self.assertRaises(ValidationError):
            block.clean(value)

    def test_non_block_validation_error(self):
        class LinkBlock(blocks.StructBlock):
            page = blocks.PageChooserBlock(required=False)
            url = blocks.URLBlock(required=False)

            def clean(self, value):
                result = super().clean(value)
                if not (result["page"] or result["url"]):
                    raise StructBlockValidationError(
                        non_block_errors=ErrorList(
                            ["Either page or URL must be specified"]
                        )
                    )
                return result

        block = LinkBlock()
        bad_data = {"page": None, "url": ""}
        with self.assertRaises(ValidationError):
            block.clean(bad_data)

        good_data = {"page": None, "url": "https://wagtail.org/"}
        self.assertEqual(block.clean(good_data), good_data)

    def test_bound_blocks_are_available_on_template(self):
        """
        Test that we are able to use value.bound_blocks within templates
        to access a child block's own HTML rendering
        """
        block = SectionBlock()
        value = block.to_python({"title": "Hello", "body": "<i>italic</i> world"})
        result = block.render(value)
        self.assertEqual(result, """<h1>Hello</h1><i>italic</i> world""")

    def test_render_block_with_extra_context(self):
        block = SectionBlock()
        value = block.to_python({"title": "Bonjour", "body": "monde <i>italique</i>"})
        result = block.render(value, context={"language": "fr"})
        self.assertEqual(result, """<h1 lang="fr">Bonjour</h1>monde <i>italique</i>""")

    def test_render_structvalue(self):
        """
        The HTML representation of a StructValue should use the block's template
        """
        block = SectionBlock()
        value = block.to_python({"title": "Hello", "body": "<i>italic</i> world"})
        result = value.__html__()
        self.assertEqual(result, """<h1>Hello</h1><i>italic</i> world""")

        # value.render_as_block() should be equivalent to value.__html__()
        result = value.render_as_block()
        self.assertEqual(result, """<h1>Hello</h1><i>italic</i> world""")

    def test_str_structvalue(self):
        """
        The str() representation of a StructValue should NOT render the template, as that's liable
        to cause an infinite loop if any debugging / logging code attempts to log the fact that
        it rendered a template with this object in the context:
        https://github.com/wagtail/wagtail/issues/2874
        https://github.com/jazzband/django-debug-toolbar/issues/950
        """
        block = SectionBlock()
        value = block.to_python({"title": "Hello", "body": "<i>italic</i> world"})
        result = str(value)
        self.assertNotIn("<h1>", result)
        # The expected rendering should correspond to the native representation of an OrderedDict:
        # "StructValue([('title', u'Hello'), ('body', <wagtail.rich_text.RichText object at 0xb12d5eed>)])"
        # - give or take some quoting differences between Python versions
        self.assertIn("StructValue", result)
        self.assertIn("title", result)
        self.assertIn("Hello", result)

    def test_render_structvalue_with_extra_context(self):
        block = SectionBlock()
        value = block.to_python({"title": "Bonjour", "body": "monde <i>italique</i>"})
        result = value.render_as_block(context={"language": "fr"})
        self.assertEqual(result, """<h1 lang="fr">Bonjour</h1>monde <i>italique</i>""")

    def test_copy_structvalue(self):
        block = SectionBlock()
        value = block.to_python({"title": "Hello", "body": "world"})
        copied = copy.copy(value)

        # Ensure we have a new object
        self.assertIsNot(value, copied)

        # Check copy operation
        self.assertIsInstance(copied, blocks.StructValue)
        self.assertIs(value.block, copied.block)
        self.assertEqual(value, copied)

    def test_normalize_base_cases(self):
        """Test the trivially recursive and already normalized cases"""
        block = blocks.StructBlock([("title", blocks.CharBlock())])
        self.assertEqual(
            block.normalize({"title": "Foo"}), block._to_struct_value({"title": "Foo"})
        )
        self.assertEqual(
            block.normalize(block._to_struct_value({"title": "Foo"})),
            block._to_struct_value({"title": "Foo"}),
        )

    def test_recursive_normalize(self):
        """StructBlock.normalize should recursively normalize all children"""

        block = blocks.StructBlock(
            [
                (
                    "inner_stream",
                    blocks.StreamBlock(
                        [
                            ("inner_char", blocks.CharBlock()),
                            ("inner_int", blocks.IntegerBlock()),
                        ]
                    ),
                ),
                ("list_of_ints", blocks.ListBlock(blocks.IntegerBlock())),
            ]
        )

        # A value in the human friendly format
        value = {
            "inner_stream": [("inner_char", "Hello, world"), ("inner_int", 42)],
            "list_of_ints": [5, 6, 7, 8],
        }

        normalized = block.normalize(value)
        self.assertIsInstance(normalized, blocks.StructValue)
        self.assertIsInstance(normalized["inner_stream"], blocks.StreamValue)
        self.assertIsInstance(
            normalized["inner_stream"][0], blocks.StreamValue.StreamChild
        )
        self.assertIsInstance(
            normalized["inner_stream"][1], blocks.StreamValue.StreamChild
        )
        self.assertIsInstance(normalized["list_of_ints"], blocks.list_block.ListValue)
        self.assertIsInstance(normalized["list_of_ints"][0], int)


class TestStructBlockWithCustomStructValue(SimpleTestCase):
    def test_initialisation(self):
        class CustomStructValue(blocks.StructValue):
            def joined(self):
                return self.get("title", "") + self.get("link", "")

        block = blocks.StructBlock(
            [
                ("title", blocks.CharBlock()),
                ("link", blocks.URLBlock()),
            ],
            value_class=CustomStructValue,
        )

        self.assertEqual(list(block.child_blocks.keys()), ["title", "link"])

        block_value = block.to_python(
            {"title": "Birthday party", "link": "https://myparty.co.uk"}
        )
        self.assertIsInstance(block_value, CustomStructValue)

        default_value = block.get_default()
        self.assertIsInstance(default_value, CustomStructValue)

        value_from_datadict = block.value_from_datadict(
            {"mylink-title": "Torchbox", "mylink-link": "http://www.torchbox.com"},
            {},
            "mylink",
        )

        self.assertIsInstance(value_from_datadict, CustomStructValue)

        value = block.to_python(
            {"title": "Torchbox", "link": "http://www.torchbox.com/"}
        )
        clean_value = block.clean(value)
        self.assertIsInstance(clean_value, CustomStructValue)
        self.assertEqual(clean_value["title"], "Torchbox")

        value = block.to_python({"title": "Torchbox", "link": "not a url"})
        with self.assertRaises(ValidationError):
            block.clean(value)

    def test_initialisation_from_subclass(self):
        class LinkStructValue(blocks.StructValue):
            def url(self):
                return self.get("page") or self.get("link")

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            page = blocks.PageChooserBlock(required=False)
            link = blocks.URLBlock(required=False)

            class Meta:
                value_class = LinkStructValue

        block = LinkBlock()

        self.assertEqual(list(block.child_blocks.keys()), ["title", "page", "link"])

        block_value = block.to_python(
            {"title": "Website", "link": "https://website.com"}
        )
        self.assertIsInstance(block_value, LinkStructValue)

        default_value = block.get_default()
        self.assertIsInstance(default_value, LinkStructValue)

    def test_initialisation_with_multiple_subclassses(self):
        class LinkStructValue(blocks.StructValue):
            def url(self):
                return self.get("page") or self.get("link")

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            page = blocks.PageChooserBlock(required=False)
            link = blocks.URLBlock(required=False)

            class Meta:
                value_class = LinkStructValue

        class StyledLinkBlock(LinkBlock):
            classname = blocks.CharBlock()

        block = StyledLinkBlock()

        self.assertEqual(
            list(block.child_blocks.keys()), ["title", "page", "link", "classname"]
        )

        value_from_datadict = block.value_from_datadict(
            {
                "queen-title": "Torchbox",
                "queen-link": "http://www.torchbox.com",
                "queen-classname": "fullsize",
            },
            {},
            "queen",
        )

        self.assertIsInstance(value_from_datadict, LinkStructValue)

    def test_initialisation_with_mixins(self):
        class LinkStructValue(blocks.StructValue):
            pass

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

            class Meta:
                value_class = LinkStructValue

        class StylingMixin(blocks.StructBlock):
            classname = blocks.CharBlock()

        class StyledLinkBlock(StylingMixin, LinkBlock):
            source = blocks.CharBlock()

        block = StyledLinkBlock()

        self.assertEqual(
            list(block.child_blocks.keys()), ["title", "link", "classname", "source"]
        )

        block_value = block.to_python(
            {
                "title": "Website",
                "link": "https://website.com",
                "source": "google",
                "classname": "full-size",
            }
        )
        self.assertIsInstance(block_value, LinkStructValue)

    def test_value_property(self):
        class SectionStructValue(blocks.StructValue):
            @property
            def foo(self):
                return "bar %s" % self.get("title", "")

        class SectionBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            body = blocks.RichTextBlock()

            class Meta:
                value_class = SectionStructValue

        block = SectionBlock()
        struct_value = block.to_python({"title": "hello", "body": "<b>world</b>"})
        value = struct_value.foo
        self.assertEqual(value, "bar hello")

    def test_render_with_template(self):
        class SectionStructValue(blocks.StructValue):
            def title_with_suffix(self):
                title = self.get("title")
                if title:
                    return "SUFFIX %s" % title
                return "EMPTY TITLE"

        class SectionBlock(blocks.StructBlock):
            title = blocks.CharBlock(required=False)

            class Meta:
                value_class = SectionStructValue

        block = SectionBlock(template="tests/blocks/struct_block_custom_value.html")
        struct_value = block.to_python({"title": "hello"})
        html = block.render(struct_value)
        self.assertEqual(html, "<div>SUFFIX hello</div>\n")

        struct_value = block.to_python({})
        html = block.render(struct_value)
        self.assertEqual(html, "<div>EMPTY TITLE</div>\n")

    def test_normalize(self):
        """A normalized StructBlock value should be an instance of the StructBlock's value_class"""

        class CustomStructValue(blocks.StructValue):
            pass

        class CustomStructBlock(blocks.StructBlock):
            text = blocks.TextBlock()

            class Meta:
                value_class = CustomStructValue

        self.assertIsInstance(
            CustomStructBlock().normalize({"text": "She sells sea shells"}),
            CustomStructValue,
        )

    def test_normalize_incorrect_value_class(self):
        """
        If StructBlock.normalize is passed a StructValue instance that doesn't
        match the StructBlock's `value_class', it should convert the value
        to the correct class.
        """

        class CustomStructValue(blocks.StructValue):
            pass

        class CustomStructBlock(blocks.StructBlock):
            text = blocks.TextBlock()

            class Meta:
                value_class = CustomStructValue

        block = CustomStructBlock()
        # Not an instance of CustomStructValue, which CustomStructBlock uses.
        value = blocks.StructValue(block, {"text": "The quick brown fox"})
        self.assertIsInstance(block.normalize(value), CustomStructValue)


class TestListBlock(WagtailTestUtils, SimpleTestCase):
    def assert_eq_list_values(self, p, q):
        # We can't directly compare ListValue instances yet
        self.assertEqual(list(p), list(q))

    def test_initialise_with_class(self):
        block = blocks.ListBlock(blocks.CharBlock)

        # Child block should be initialised for us
        self.assertIsInstance(block.child_block, blocks.CharBlock)

    def test_initialise_with_instance(self):
        child_block = blocks.CharBlock()
        block = blocks.ListBlock(child_block)

        self.assertEqual(block.child_block, child_block)

    def render(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = blocks.ListBlock(LinkBlock())
        return block.render(
            [
                {
                    "title": "Wagtail",
                    "link": "http://www.wagtail.org",
                },
                {
                    "title": "Django",
                    "link": "http://www.djangoproject.com",
                },
            ]
        )

    def test_render_uses_ul(self):
        html = self.render()

        self.assertIn("<ul>", html)
        self.assertIn("</ul>", html)

    def test_render_uses_li(self):
        html = self.render()

        self.assertIn("<li>", html)
        self.assertIn("</li>", html)

    def test_render_calls_block_render_on_children(self):
        """
        The default rendering of a ListBlock should invoke the block's render method
        on each child, rather than just outputting the child value as a string.
        """
        block = blocks.ListBlock(
            blocks.CharBlock(template="tests/blocks/heading_block.html")
        )
        html = block.render(["Hello world!", "Goodbye world!"])

        self.assertIn("<h1>Hello world!</h1>", html)
        self.assertIn("<h1>Goodbye world!</h1>", html)

    def test_render_passes_context_to_children(self):
        """
        Template context passed to the render method should be passed on
        to the render method of the child block.
        """
        block = blocks.ListBlock(
            blocks.CharBlock(template="tests/blocks/heading_block.html")
        )
        html = block.render(
            ["Bonjour le monde!", "Au revoir le monde!"],
            context={
                "language": "fr",
            },
        )

        self.assertIn('<h1 lang="fr">Bonjour le monde!</h1>', html)
        self.assertIn('<h1 lang="fr">Au revoir le monde!</h1>', html)

    def test_get_api_representation_calls_same_method_on_children_with_context(self):
        """
        The get_api_representation method of a ListBlock should invoke
        the block's get_api_representation method on each child and
        the context should be passed on.
        """

        class ContextBlock(blocks.CharBlock):
            def get_api_representation(self, value, context=None):
                return context[value]

        block = blocks.ListBlock(ContextBlock())
        api_representation = block.get_api_representation(
            ["en", "fr"], context={"en": "Hello world!", "fr": "Bonjour le monde!"}
        )

        self.assertEqual(api_representation, ["Hello world!", "Bonjour le monde!"])

    def test_adapt(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = blocks.ListBlock(LinkBlock)

        block.set_name("test_listblock")
        js_args = ListBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_listblock")
        self.assertIsInstance(js_args[1], LinkBlock)
        self.assertEqual(js_args[2], {"title": None, "link": None})
        self.assertEqual(
            js_args[3],
            {
                "label": "Test listblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": None,
                "attrs": {},
                "collapsed": False,
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )

    def test_adapt_with_min_num_max_num(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = blocks.ListBlock(LinkBlock, min_num=2, max_num=5)

        block.set_name("test_listblock")
        js_args = ListBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_listblock")
        self.assertIsInstance(js_args[1], LinkBlock)
        self.assertEqual(js_args[2], {"title": None, "link": None})
        self.assertEqual(
            js_args[3],
            {
                "label": "Test listblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": None,
                "attrs": {},
                "collapsed": False,
                "minNum": 2,
                "maxNum": 5,
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )

    def test_searchable_content(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = blocks.ListBlock(LinkBlock())
        content = block.get_searchable_content(
            [
                {
                    "title": "Wagtail",
                    "link": "http://www.wagtail.org",
                },
                {
                    "title": "Django",
                    "link": "http://www.djangoproject.com",
                },
            ]
        )

        self.assertEqual(content, ["Wagtail", "Django"])

    def test_value_omitted_from_data(self):
        block = blocks.ListBlock(blocks.CharBlock())

        # overall value is considered present in the form if the 'count' field is present
        self.assertFalse(
            block.value_omitted_from_data({"mylist-count": "0"}, {}, "mylist")
        )
        self.assertFalse(
            block.value_omitted_from_data(
                {
                    "mylist-count": "1",
                    "mylist-0-value": "hello",
                    "mylist-0-deleted": "",
                    "mylist-0-order": "0",
                },
                {},
                "mylist",
            )
        )
        self.assertTrue(
            block.value_omitted_from_data({"nothing-here": "nope"}, {}, "mylist")
        )

    def test_id_from_form_submission_is_preserved(self):
        block = blocks.ListBlock(blocks.CharBlock())

        post_data = {"shoppinglist-count": "3"}
        for i in range(0, 3):
            post_data.update(
                {
                    "shoppinglist-%d-deleted" % i: "",
                    "shoppinglist-%d-order" % i: str(i),
                    "shoppinglist-%d-value" % i: "item %d" % i,
                    "shoppinglist-%d-id" % i: "0000000%d" % i,
                }
            )

        block_value = block.value_from_datadict(post_data, {}, "shoppinglist")
        self.assertEqual(block_value.bound_blocks[1].value, "item 1")
        self.assertEqual(block_value.bound_blocks[1].id, "00000001")

    def test_ordering_in_form_submission_uses_order_field(self):
        block = blocks.ListBlock(blocks.CharBlock())

        # check that items are ordered by the 'order' field, not the order they appear in the form
        post_data = {"shoppinglist-count": "3"}
        for i in range(0, 3):
            post_data.update(
                {
                    "shoppinglist-%d-deleted" % i: "",
                    "shoppinglist-%d-order" % i: str(2 - i),
                    "shoppinglist-%d-value" % i: "item %d" % i,
                    "shoppinglist-%d-id" % i: "0000000%d" % i,
                }
            )

        block_value = block.value_from_datadict(post_data, {}, "shoppinglist")
        self.assertEqual(block_value[2], "item 0")

    def test_ordering_in_form_submission_is_numeric(self):
        block = blocks.ListBlock(blocks.CharBlock())

        # check that items are ordered by 'order' numerically, not alphabetically
        post_data = {"shoppinglist-count": "12"}
        for i in range(0, 12):
            post_data.update(
                {
                    "shoppinglist-%d-deleted" % i: "",
                    "shoppinglist-%d-order" % i: str(i),
                    "shoppinglist-%d-value" % i: "item %d" % i,
                    "shoppinglist-%d-id" % i: "0000000%d" % i,
                }
            )

        block_value = block.value_from_datadict(post_data, {}, "shoppinglist")
        self.assertEqual(block_value[2], "item 2")

    def test_can_specify_default(self):
        block = blocks.ListBlock(
            blocks.CharBlock(), default=["peas", "beans", "carrots"]
        )

        self.assertEqual(list(block.get_default()), ["peas", "beans", "carrots"])

    def test_default_callable(self):
        def callable_default():
            return ["chocolate", "vanilla"]

        block = blocks.ListBlock(blocks.CharBlock(), default=callable_default)

        self.assertEqual(list(block.get_default()), ["chocolate", "vanilla"])

    def test_default_default(self):
        """
        if no explicit 'default' is set on the ListBlock, it should fall back on
        a single instance of the child block in its default state.
        """
        block = blocks.ListBlock(blocks.CharBlock(default="chocolate"))

        self.assertEqual(list(block.get_default()), ["chocolate"])

        block.set_name("test_shoppinglistblock")
        js_args = ListBlockAdapter().js_args(block)
        self.assertEqual(js_args[2], "chocolate")

    def test_default_value_is_distinct_instance(self):
        """
        Whenever the default value of a ListBlock is invoked, it should be a distinct
        instance of the list so that modifying it doesn't modify other places where the
        default value appears.
        """

        class ShoppingListBlock(blocks.StructBlock):
            shop = blocks.CharBlock()
            items = blocks.ListBlock(blocks.CharBlock(default="chocolate"))

        block = ShoppingListBlock()

        tesco_shopping = block.to_python(
            {"shop": "Tesco"}
        )  # 'items' will default to ['chocolate']
        asda_shopping = block.to_python(
            {"shop": "Asda"}
        )  # 'items' will default to ['chocolate'], but a distinct instance

        tesco_shopping["items"].append("cake")
        self.assertEqual(list(tesco_shopping["items"]), ["chocolate", "cake"])
        # asda_shopping should not be modified
        self.assertEqual(list(asda_shopping["items"]), ["chocolate"])

    def test_adapt_with_classname_via_kwarg(self):
        """form_classname from kwargs to be used as an additional class when rendering list block"""

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = blocks.ListBlock(LinkBlock, form_classname="special-list-class")

        block.set_name("test_listblock")
        js_args = ListBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[3],
            {
                "label": "Test listblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "special-list-class",
                "attrs": {},
                "collapsed": False,
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )

    def test_adapt_with_classname_via_class_meta(self):
        """form_classname from meta to be used as an additional class when rendering list block"""

        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        class CustomListBlock(blocks.ListBlock):
            class Meta:
                form_classname = "custom-list-class"

        block = CustomListBlock(LinkBlock)

        block.set_name("test_listblock")
        js_args = ListBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[3],
            {
                "label": "Test listblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "custom-list-class",
                "attrs": {},
                "collapsed": False,
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )

    def test_adapt_with_form_attrs(self):
        class LinkBlock(blocks.StructBlock):
            title = blocks.CharBlock()
            link = blocks.URLBlock()

        block = blocks.ListBlock(
            LinkBlock,
            form_attrs={
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

        block.set_name("test_listblock")
        js_args = ListBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_listblock")
        self.assertIsInstance(js_args[1], LinkBlock)
        self.assertEqual(js_args[2], {"title": None, "link": None})
        self.assertEqual(
            js_args[3].get("attrs"),
            {
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

    def test_clean_preserves_block_ids(self):
        block = blocks.ListBlock(blocks.CharBlock())
        block_val = block.to_python(
            [
                {
                    "type": "item",
                    "value": "foo",
                    "id": "11111111-1111-1111-1111-111111111111",
                },
                {
                    "type": "item",
                    "value": "bar",
                    "id": "22222222-2222-2222-2222-222222222222",
                },
            ]
        )
        cleaned_block_val = block.clean(block_val)
        self.assertEqual(
            cleaned_block_val.bound_blocks[0].id, "11111111-1111-1111-1111-111111111111"
        )

    def test_min_num_validation_errors(self):
        block = blocks.ListBlock(blocks.CharBlock(), min_num=2)
        block_val = block.to_python(["foo"])

        with self.assertRaises(ValidationError) as catcher:
            block.clean(block_val)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {
                "messages": ["The minimum number of items is 2"],
            },
        )

        # a value with >= 2 blocks should pass validation
        block_val = block.to_python(["foo", "bar"])
        self.assertTrue(block.clean(block_val))

    def test_max_num_validation_errors(self):
        block = blocks.ListBlock(blocks.CharBlock(), max_num=2)
        block_val = block.to_python(["foo", "bar", "baz"])

        with self.assertRaises(ValidationError) as catcher:
            block.clean(block_val)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {
                "messages": ["The maximum number of items is 2"],
            },
        )

        # a value with <= 2 blocks should pass validation
        block_val = block.to_python(["foo", "bar"])
        self.assertTrue(block.clean(block_val))

    def test_unpack_old_database_format(self):
        block = blocks.ListBlock(blocks.CharBlock())
        list_val = block.to_python(["foo", "bar"])

        # list_val should behave as a list
        self.assertEqual(len(list_val), 2)
        self.assertEqual(list_val[0], "foo")

        # but also provide a bound_blocks property
        self.assertEqual(len(list_val.bound_blocks), 2)
        self.assertEqual(list_val.bound_blocks[0].value, "foo")

        # Bound blocks should be assigned UUIDs
        self.assertRegex(list_val.bound_blocks[0].id, r"[0-9a-f-]+")

    def test_bulk_unpack_old_database_format(self):
        block = blocks.ListBlock(blocks.CharBlock())
        [list_1, list_2] = block.bulk_to_python([["foo", "bar"], ["xxx", "yyy", "zzz"]])

        self.assertEqual(len(list_1), 2)
        self.assertEqual(len(list_2), 3)
        self.assertEqual(list_1[0], "foo")
        self.assertEqual(list_2[0], "xxx")

        # lists also provide a bound_blocks property
        self.assertEqual(len(list_1.bound_blocks), 2)
        self.assertEqual(list_1.bound_blocks[0].value, "foo")

        # Bound blocks should be assigned UUIDs
        self.assertRegex(list_1.bound_blocks[0].id, r"[0-9a-f-]+")

    def test_unpack_new_database_format(self):
        block = blocks.ListBlock(blocks.CharBlock())
        list_val = block.to_python(
            [
                {
                    "type": "item",
                    "value": "foo",
                    "id": "11111111-1111-1111-1111-111111111111",
                },
                {
                    "type": "item",
                    "value": "bar",
                    "id": "22222222-2222-2222-2222-222222222222",
                },
            ]
        )

        # list_val should behave as a list
        self.assertEqual(len(list_val), 2)
        self.assertEqual(list_val[0], "foo")

        # but also provide a bound_blocks property
        self.assertEqual(len(list_val.bound_blocks), 2)
        self.assertEqual(list_val.bound_blocks[0].value, "foo")
        self.assertEqual(
            list_val.bound_blocks[0].id, "11111111-1111-1111-1111-111111111111"
        )

    def test_bulk_unpack_new_database_format(self):
        block = blocks.ListBlock(blocks.CharBlock())
        [list_1, list_2] = block.bulk_to_python(
            [
                [
                    {
                        "type": "item",
                        "value": "foo",
                        "id": "11111111-1111-1111-1111-111111111111",
                    },
                    {
                        "type": "item",
                        "value": "bar",
                        "id": "22222222-2222-2222-2222-222222222222",
                    },
                ],
                [
                    {
                        "type": "item",
                        "value": "baz",
                        "id": "33333333-3333-3333-3333-333333333333",
                    },
                ],
            ]
        )

        self.assertEqual(len(list_1), 2)
        self.assertEqual(len(list_2), 1)
        self.assertEqual(list_1[0], "foo")
        self.assertEqual(list_2[0], "baz")

        # lists also provide a bound_blocks property
        self.assertEqual(len(list_1.bound_blocks), 2)
        self.assertEqual(list_1.bound_blocks[0].value, "foo")
        self.assertEqual(
            list_1.bound_blocks[0].id, "11111111-1111-1111-1111-111111111111"
        )

    def test_assign_listblock_with_list(self):
        stream_block = blocks.StreamBlock(
            [
                ("bullet_list", blocks.ListBlock(blocks.CharBlock())),
            ]
        )
        stream_value = stream_block.to_python([])
        stream_value.append(("bullet_list", ["foo", "bar"]))

        clean_stream_value = stream_block.clean(stream_value)
        result = stream_block.get_prep_value(clean_stream_value)
        self.assertEqual(result[0]["type"], "bullet_list")
        self.assertEqual(len(result[0]["value"]), 2)
        self.assertEqual(result[0]["value"][0]["value"], "foo")

    def test_normalize_base_case(self):
        """Test normalize when trivially recursive, or already a ListValue"""
        block = blocks.ListBlock(blocks.IntegerBlock)
        normalized = block.normalize([0, 1, 1, 2, 3])
        self.assertIsInstance(normalized, blocks.list_block.ListValue)
        self.assert_eq_list_values(normalized, [0, 1, 1, 2, 3])

        normalized = block.normalize(
            blocks.list_block.ListValue(block, [0, 1, 1, 2, 3])
        )
        self.assertIsInstance(normalized, blocks.list_block.ListValue)
        self.assert_eq_list_values(normalized, [0, 1, 1, 2, 3])

    def test_normalize_empty(self):
        block = blocks.ListBlock(blocks.IntegerBlock())
        normalized = block.normalize([])
        self.assertIsInstance(normalized, blocks.list_block.ListValue)
        self.assert_eq_list_values(normalized, [])

    def test_recursive_normalize(self):
        """
        ListBlock.normalize should recursively normalize all values passed to
        it, and return a ListValue.
        """
        inner_list_block = blocks.ListBlock(blocks.IntegerBlock())
        block = blocks.ListBlock(inner_list_block)
        values = [
            [[1, 2, 3]],
            [blocks.list_block.ListValue(block, [1, 2, 3])],
        ]

        for value in values:
            normalized = block.normalize(value)
            self.assertIsInstance(normalized, blocks.list_block.ListValue)
            self.assert_eq_list_values(normalized[0], [1, 2, 3])


class TestListBlockWithFixtures(TestCase):
    fixtures = ["test.json"]

    def test_calls_child_bulk_to_python_when_available(self):
        page_ids = [2, 3, 4, 5]
        expected_pages = Page.objects.filter(pk__in=page_ids)
        block = blocks.ListBlock(blocks.PageChooserBlock())

        with self.assertNumQueries(1):
            pages = block.to_python(page_ids)

        self.assertSequenceEqual(pages, expected_pages)

    def test_bulk_to_python(self):
        block = blocks.ListBlock(blocks.PageChooserBlock())

        with self.assertNumQueries(1):
            result = block.bulk_to_python([[4, 5], [], [2]])
            # result will be a list of ListValues - convert to lists for equality check
            clean_result = [list(val) for val in result]

        self.assertEqual(
            clean_result,
            [
                [Page.objects.get(id=4), Page.objects.get(id=5)],
                [],
                [Page.objects.get(id=2)],
            ],
        )

    def test_extract_references(self):
        block = blocks.ListBlock(blocks.PageChooserBlock())
        christmas_page = Page.objects.get(slug="christmas")
        saint_patrick_page = Page.objects.get(slug="saint-patrick")

        self.assertListEqual(
            list(
                block.extract_references(
                    block.to_python(
                        [
                            {
                                "id": "block1",
                                "type": "item",
                                "value": christmas_page.id,
                            },
                            {
                                "id": "block2",
                                "type": "item",
                                "value": saint_patrick_page.id,
                            },
                        ]
                    )
                )
            ),
            [
                (Page, str(christmas_page.id), "item", "block1"),
                (Page, str(saint_patrick_page.id), "item", "block2"),
            ],
        )


class TestStreamBlock(WagtailTestUtils, SimpleTestCase):
    def test_initialisation(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
                ("paragraph", blocks.CharBlock()),
            ]
        )

        self.assertEqual(list(block.child_blocks.keys()), ["heading", "paragraph"])

    def test_initialisation_with_binary_string_names(self):
        # migrations will sometimes write out names as binary strings, just to keep us on our toes
        block = blocks.StreamBlock(
            [
                (b"heading", blocks.CharBlock()),
                (b"paragraph", blocks.CharBlock()),
            ]
        )

        self.assertEqual(list(block.child_blocks.keys()), [b"heading", b"paragraph"])

    def test_initialisation_from_subclass(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()

        self.assertEqual(list(block.child_blocks.keys()), ["heading", "paragraph"])

    def test_initialisation_from_subclass_with_extra(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock([("intro", blocks.CharBlock())])

        self.assertEqual(
            list(block.child_blocks.keys()), ["heading", "paragraph", "intro"]
        )

    def test_initialisation_with_multiple_subclassses(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        class ArticleWithIntroBlock(ArticleBlock):
            intro = blocks.CharBlock()

        block = ArticleWithIntroBlock()

        self.assertEqual(
            list(block.child_blocks.keys()), ["heading", "paragraph", "intro"]
        )

    def test_initialisation_with_mixins(self):
        """
        The order of child blocks of a ``StreamBlock`` with multiple parent
        classes is slightly surprising at first. Child blocks are inherited in
        a bottom-up order, by traversing the MRO in reverse. In the example
        below, ``ArticleWithIntroBlock`` will have an MRO of::

            [ArticleWithIntroBlock, IntroMixin, ArticleBlock, StreamBlock, ...]

        This will result in ``intro`` appearing *after* ``heading`` and
        ``paragraph`` in ``ArticleWithIntroBlock.child_blocks``, even though
        ``IntroMixin`` appeared before ``ArticleBlock``.
        """

        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        class IntroMixin(blocks.StreamBlock):
            intro = blocks.CharBlock()

        class ArticleWithIntroBlock(IntroMixin, ArticleBlock):
            by_line = blocks.CharBlock()

        block = ArticleWithIntroBlock()

        self.assertEqual(
            list(block.child_blocks.keys()),
            ["heading", "paragraph", "intro", "by_line"],
        )

    def test_field_has_changed(self):
        block = blocks.StreamBlock([("paragraph", blocks.CharBlock())])
        initial_value = blocks.StreamValue(block, [("paragraph", "test")])
        initial_value[0].id = "a"

        data_value = blocks.StreamValue(block, [("paragraph", "test")])
        data_value[0].id = "a"

        # identical ids and content, so has_changed should return False
        self.assertFalse(
            blocks.BlockField(block).has_changed(initial_value, data_value)
        )

        changed_data_value = blocks.StreamValue(block, [("paragraph", "not a test")])
        changed_data_value[0].id = "a"

        # identical ids but changed content, so has_changed should return True
        self.assertTrue(
            blocks.BlockField(block).has_changed(initial_value, changed_data_value)
        )

    def test_required_raises_an_exception_if_empty(self):
        block = blocks.StreamBlock([("paragraph", blocks.CharBlock())], required=True)
        value = blocks.StreamValue(block, [])

        with self.assertRaises(blocks.StreamBlockValidationError):
            block.clean(value)

    def test_required_does_not_raise_an_exception_if_not_empty(self):
        block = blocks.StreamBlock([("paragraph", blocks.CharBlock())], required=True)
        value = block.to_python([{"type": "paragraph", "value": "Hello"}])
        try:
            block.clean(value)
        except blocks.StreamBlockValidationError:
            raise self.failureException(
                "%s was raised" % blocks.StreamBlockValidationError
            )

    def test_not_required_does_not_raise_an_exception_if_empty(self):
        block = blocks.StreamBlock([("paragraph", blocks.CharBlock())], required=False)
        value = blocks.StreamValue(block, [])

        try:
            block.clean(value)
        except blocks.StreamBlockValidationError:
            raise self.failureException(
                "%s was raised" % blocks.StreamBlockValidationError
            )

    def test_required_by_default(self):
        block = blocks.StreamBlock([("paragraph", blocks.CharBlock())])
        value = blocks.StreamValue(block, [])

        with self.assertRaises(blocks.StreamBlockValidationError):
            block.clean(value)

    def render_article(self, data):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.RichTextBlock()

        block = ArticleBlock()
        value = block.to_python(data)

        return block.render(value)

    def test_get_api_representation_calls_same_method_on_children_with_context(self):
        """
        The get_api_representation method of a StreamBlock should invoke
        the block's get_api_representation method on each child and
        the context should be passed on.
        """

        class ContextBlock(blocks.CharBlock):
            def get_api_representation(self, value, context=None):
                return context[value]

        block = blocks.StreamBlock(
            [
                ("language", ContextBlock()),
                ("author", ContextBlock()),
            ]
        )
        api_representation = block.get_api_representation(
            block.to_python(
                [
                    {"type": "language", "value": "en"},
                    {"type": "author", "value": "wagtail", "id": "111111"},
                ]
            ),
            context={"en": "English", "wagtail": "Wagtail!"},
        )

        self.assertListEqual(
            api_representation,
            [
                {"type": "language", "value": "English", "id": None},
                {"type": "author", "value": "Wagtail!", "id": "111111"},
            ],
        )

    def test_render(self):
        html = self.render_article(
            [
                {
                    "type": "heading",
                    "value": "My title",
                },
                {
                    "type": "paragraph",
                    "value": "My <i>first</i> paragraph",
                },
                {
                    "type": "paragraph",
                    "value": "My second paragraph",
                },
            ]
        )

        self.assertIn('<div class="block-heading">My title</div>', html)
        self.assertIn(
            '<div class="block-paragraph">My <i>first</i> paragraph</div>', html
        )
        self.assertIn('<div class="block-paragraph">My second paragraph</div>', html)

    def test_render_unknown_type(self):
        # This can happen if a developer removes a type from their StreamBlock
        html = self.render_article(
            [
                {
                    "type": "foo",
                    "value": "Hello",
                },
                {
                    "type": "paragraph",
                    "value": "My first paragraph",
                },
            ]
        )
        self.assertNotIn("foo", html)
        self.assertNotIn("Hello", html)
        self.assertIn('<div class="block-paragraph">My first paragraph</div>', html)

    def test_render_calls_block_render_on_children(self):
        """
        The default rendering of a StreamBlock should invoke the block's render method
        on each child, rather than just outputting the child value as a string.
        """
        block = blocks.StreamBlock(
            [
                (
                    "heading",
                    blocks.CharBlock(template="tests/blocks/heading_block.html"),
                ),
                ("paragraph", blocks.CharBlock()),
            ]
        )
        value = block.to_python([{"type": "heading", "value": "Hello"}])
        html = block.render(value)
        self.assertIn('<div class="block-heading"><h1>Hello</h1></div>', html)

        # calling render_as_block() on value (a StreamValue instance)
        # should be equivalent to block.render(value)
        html = value.render_as_block()
        self.assertIn('<div class="block-heading"><h1>Hello</h1></div>', html)

    def test_render_passes_context_to_children(self):
        block = blocks.StreamBlock(
            [
                (
                    "heading",
                    blocks.CharBlock(template="tests/blocks/heading_block.html"),
                ),
                ("paragraph", blocks.CharBlock()),
            ]
        )
        value = block.to_python([{"type": "heading", "value": "Bonjour"}])
        html = block.render(
            value,
            context={
                "language": "fr",
            },
        )
        self.assertIn(
            '<div class="block-heading"><h1 lang="fr">Bonjour</h1></div>', html
        )

        # calling render_as_block(context=foo) on value (a StreamValue instance)
        # should be equivalent to block.render(value, context=foo)
        html = value.render_as_block(
            context={
                "language": "fr",
            }
        )
        self.assertIn(
            '<div class="block-heading"><h1 lang="fr">Bonjour</h1></div>', html
        )

    def test_render_on_stream_child_uses_child_template(self):
        """
        Accessing a child element of the stream (giving a StreamChild object) and rendering it
        should use the block template, not just render the value's string representation
        """
        block = blocks.StreamBlock(
            [
                (
                    "heading",
                    blocks.CharBlock(template="tests/blocks/heading_block.html"),
                ),
                ("paragraph", blocks.CharBlock()),
            ]
        )
        value = block.to_python([{"type": "heading", "value": "Hello"}])
        html = value[0].render()
        self.assertEqual("<h1>Hello</h1>", html)

        # StreamChild.__str__ should do the same
        html = str(value[0])
        self.assertEqual("<h1>Hello</h1>", html)

        # and so should StreamChild.render_as_block
        html = value[0].render_as_block()
        self.assertEqual("<h1>Hello</h1>", html)

    def test_can_pass_context_to_stream_child_template(self):
        block = blocks.StreamBlock(
            [
                (
                    "heading",
                    blocks.CharBlock(template="tests/blocks/heading_block.html"),
                ),
                ("paragraph", blocks.CharBlock()),
            ]
        )
        value = block.to_python([{"type": "heading", "value": "Bonjour"}])
        html = value[0].render(context={"language": "fr"})
        self.assertEqual('<h1 lang="fr">Bonjour</h1>', html)

        # the same functionality should be available through the alias `render_as_block`
        html = value[0].render_as_block(context={"language": "fr"})
        self.assertEqual('<h1 lang="fr">Bonjour</h1>', html)

    def test_adapt(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()

        block.set_name("test_streamblock")
        js_args = StreamBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_streamblock")

        # convert group_by iterable into a list
        grouped_blocks = [
            (group_name, list(group_iter)) for (group_name, group_iter) in js_args[1]
        ]
        self.assertEqual(len(grouped_blocks), 1)
        group_name, block_iter = grouped_blocks[0]
        self.assertEqual(group_name, "")
        block_list = list(block_iter)
        self.assertIsInstance(block_list[0], blocks.CharBlock)
        self.assertEqual(block_list[0].name, "heading")
        self.assertIsInstance(block_list[1], blocks.CharBlock)
        self.assertEqual(block_list[1].name, "paragraph")

        self.assertEqual(js_args[2], {"heading": None, "paragraph": None})
        self.assertEqual(
            js_args[3],
            {
                "label": "Test streamblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": None,
                "attrs": {},
                "collapsed": False,
                "maxNum": None,
                "minNum": None,
                "blockCounts": {},
                "required": True,
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )

    def test_value_omitted_from_data(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
            ]
        )

        # overall value is considered present in the form if the 'count' field is present
        self.assertFalse(
            block.value_omitted_from_data({"mystream-count": "0"}, {}, "mystream")
        )
        self.assertFalse(
            block.value_omitted_from_data(
                {
                    "mystream-count": "1",
                    "mystream-0-type": "heading",
                    "mystream-0-value": "hello",
                    "mystream-0-deleted": "",
                    "mystream-0-order": "0",
                },
                {},
                "mystream",
            )
        )
        self.assertTrue(
            block.value_omitted_from_data({"nothing-here": "nope"}, {}, "mystream")
        )

    def test_validation_errors(self):
        class ValidatedBlock(blocks.StreamBlock):
            char = blocks.CharBlock()
            url = blocks.URLBlock()

        block = ValidatedBlock()

        value = blocks.StreamValue(
            block,
            [
                ("char", ""),
                ("char", "foo"),
                ("url", "http://example.com/"),
                ("url", "not a url"),
            ],
        )

        with self.assertRaises(ValidationError) as catcher:
            block.clean(value)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {
                "blockErrors": {
                    0: {"messages": ["This field is required."]},
                    3: {"messages": ["Enter a valid URL."]},
                }
            },
        )

    def test_min_num_validation_errors(self):
        class ValidatedBlock(blocks.StreamBlock):
            char = blocks.CharBlock()
            url = blocks.URLBlock()

        block = ValidatedBlock(min_num=1)

        value = blocks.StreamValue(block, [])

        with self.assertRaises(ValidationError) as catcher:
            block.clean(value)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {"messages": ["The minimum number of items is 1"]},
        )

        # a value with >= 1 blocks should pass validation
        value = blocks.StreamValue(block, [("char", "foo")])
        self.assertTrue(block.clean(value))

    def test_max_num_validation_errors(self):
        class ValidatedBlock(blocks.StreamBlock):
            char = blocks.CharBlock()
            url = blocks.URLBlock()

        block = ValidatedBlock(max_num=1)

        value = blocks.StreamValue(
            block,
            [
                ("char", "foo"),
                ("char", "foo"),
                ("url", "http://example.com/"),
                ("url", "http://example.com/"),
            ],
        )

        with self.assertRaises(ValidationError) as catcher:
            block.clean(value)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {"messages": ["The maximum number of items is 1"]},
        )

        # a value with 1 block should pass validation
        value = blocks.StreamValue(block, [("char", "foo")])
        self.assertTrue(block.clean(value))

    def test_block_counts_min_validation_errors(self):
        class ValidatedBlock(blocks.StreamBlock):
            char = blocks.CharBlock()
            url = blocks.URLBlock()

        block = ValidatedBlock(block_counts={"char": {"min_num": 1}})

        value = blocks.StreamValue(
            block,
            [
                ("url", "http://example.com/"),
                ("url", "http://example.com/"),
            ],
        )

        with self.assertRaises(ValidationError) as catcher:
            block.clean(value)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {"messages": ["Char: The minimum number of items is 1"]},
        )

        # a value with 1 char block should pass validation
        value = blocks.StreamValue(
            block,
            [
                ("url", "http://example.com/"),
                ("char", "foo"),
                ("url", "http://example.com/"),
            ],
        )
        self.assertTrue(block.clean(value))

    def test_block_counts_max_validation_errors(self):
        class ValidatedBlock(blocks.StreamBlock):
            char = blocks.CharBlock()
            url = blocks.URLBlock()

        block = ValidatedBlock(block_counts={"char": {"max_num": 1}})

        value = blocks.StreamValue(
            block,
            [
                ("char", "foo"),
                ("char", "foo"),
                ("url", "http://example.com/"),
                ("url", "http://example.com/"),
            ],
        )

        with self.assertRaises(ValidationError) as catcher:
            block.clean(value)
        self.assertEqual(
            catcher.exception.as_json_data(),
            {"messages": ["Char: The maximum number of items is 1"]},
        )

        # a value with 1 char block should pass validation
        value = blocks.StreamValue(
            block,
            [
                ("char", "foo"),
                ("url", "http://example.com/"),
                ("url", "http://example.com/"),
            ],
        )
        self.assertTrue(block.clean(value))

    def test_ordering_in_form_submission_uses_order_field(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()

        # check that items are ordered by the 'order' field, not the order they appear in the form
        post_data = {"article-count": "3"}
        for i in range(0, 3):
            post_data.update(
                {
                    "article-%d-deleted" % i: "",
                    "article-%d-order" % i: str(2 - i),
                    "article-%d-type" % i: "heading",
                    "article-%d-value" % i: "heading %d" % i,
                    "article-%d-id" % i: "000%d" % i,
                }
            )

        block_value = block.value_from_datadict(post_data, {}, "article")
        self.assertEqual(block_value[2].value, "heading 0")
        self.assertEqual(block_value[2].id, "0000")

    def test_ordering_in_form_submission_is_numeric(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()

        # check that items are ordered by 'order' numerically, not alphabetically
        post_data = {"article-count": "12"}
        for i in range(0, 12):
            post_data.update(
                {
                    "article-%d-deleted" % i: "",
                    "article-%d-order" % i: str(i),
                    "article-%d-type" % i: "heading",
                    "article-%d-value" % i: "heading %d" % i,
                }
            )

        block_value = block.value_from_datadict(post_data, {}, "article")
        self.assertEqual(block_value[2].value, "heading 2")

    def test_searchable_content(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        value = block.to_python(
            [
                {
                    "type": "heading",
                    "value": "My title",
                },
                {
                    "type": "paragraph",
                    "value": "My first paragraph",
                },
                {
                    "type": "paragraph",
                    "value": "My second paragraph",
                },
            ]
        )

        content = block.get_searchable_content(value)

        self.assertEqual(
            content,
            [
                "My title",
                "My first paragraph",
                "My second paragraph",
            ],
        )

    def test_meta_default(self):
        """Test that we can specify a default value in the Meta of a StreamBlock"""

        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

            class Meta:
                default = [("heading", "A default heading")]

        # to access the default value, we retrieve it through a StructBlock
        # from a struct value that's missing that key
        class ArticleContainerBlock(blocks.StructBlock):
            author = blocks.CharBlock()
            article = ArticleBlock()

        block = ArticleContainerBlock()
        struct_value = block.to_python({"author": "Bob"})
        stream_value = struct_value["article"]

        self.assertIsInstance(stream_value, blocks.StreamValue)
        self.assertEqual(len(stream_value), 1)
        self.assertEqual(stream_value[0].block_type, "heading")
        self.assertEqual(stream_value[0].value, "A default heading")

    def test_constructor_default(self):
        """Test that we can specify a default value in the constructor of a StreamBlock"""

        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

            class Meta:
                default = [("heading", "A default heading")]

        # to access the default value, we retrieve it through a StructBlock
        # from a struct value that's missing that key
        class ArticleContainerBlock(blocks.StructBlock):
            author = blocks.CharBlock()
            article = ArticleBlock(default=[("heading", "A different default heading")])

        block = ArticleContainerBlock()
        struct_value = block.to_python({"author": "Bob"})
        stream_value = struct_value["article"]

        self.assertIsInstance(stream_value, blocks.StreamValue)
        self.assertEqual(len(stream_value), 1)
        self.assertEqual(stream_value[0].block_type, "heading")
        self.assertEqual(stream_value[0].value, "A different default heading")

    def test_callable_default(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        def callable_default():
            return [("heading", "A default heading from callable")]

        # to access the default value, we retrieve it through a StructBlock
        # from a struct value that's missing that key
        class ArticleContainerBlock(blocks.StructBlock):
            author = blocks.CharBlock()
            article = ArticleBlock(default=callable_default)

        block = ArticleContainerBlock()
        struct_value = block.to_python({"author": "Bob"})
        stream_value = struct_value["article"]

        self.assertIsInstance(stream_value, blocks.StreamValue)
        self.assertEqual(len(stream_value), 1)
        self.assertEqual(stream_value[0].block_type, "heading")
        self.assertEqual(stream_value[0].value, "A default heading from callable")

    def test_stream_value_equality(self):
        block = blocks.StreamBlock(
            [
                ("text", blocks.CharBlock()),
            ]
        )
        value1 = block.to_python([{"type": "text", "value": "hello"}])
        value2 = block.to_python([{"type": "text", "value": "hello"}])
        value3 = block.to_python([{"type": "text", "value": "goodbye"}])

        self.assertEqual(value1, value2)

        self.assertNotEqual(value1, value3)

    def test_adapt_considers_group_attribute(self):
        """If group attributes are set in Block Meta classes, make sure the blocks are grouped together"""

        class Group1Block1(blocks.CharBlock):
            class Meta:
                group = "group1"

        class Group1Block2(blocks.CharBlock):
            class Meta:
                group = "group1"

        class Group2Block1(blocks.CharBlock):
            class Meta:
                group = "group2"

        class Group2Block2(blocks.CharBlock):
            class Meta:
                group = "group2"

        class NoGroupBlock(blocks.CharBlock):
            pass

        block = blocks.StreamBlock(
            [
                ("b1", Group1Block1()),
                ("b2", Group1Block2()),
                ("b3", Group2Block1()),
                ("b4", Group2Block2()),
                ("ngb", NoGroupBlock()),
            ]
        )

        block.set_name("test_streamblock")
        js_args = StreamBlockAdapter().js_args(block)

        blockdefs_dict = dict(js_args[1])
        self.assertEqual(blockdefs_dict.keys(), {"", "group1", "group2"})

    def test_value_from_datadict(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()

        value = block.value_from_datadict(
            {
                "foo-count": "3",
                "foo-0-deleted": "",
                "foo-0-order": "2",
                "foo-0-type": "heading",
                "foo-0-id": "0000",
                "foo-0-value": "this is my heading",
                "foo-1-deleted": "1",
                "foo-1-order": "1",
                "foo-1-type": "heading",
                "foo-1-id": "0001",
                "foo-1-value": "a deleted heading",
                "foo-2-deleted": "",
                "foo-2-order": "0",
                "foo-2-type": "paragraph",
                "foo-2-id": "",
                "foo-2-value": "<p>this is a paragraph</p>",
            },
            {},
            prefix="foo",
        )

        self.assertEqual(len(value), 2)
        self.assertEqual(value[0].block_type, "paragraph")
        self.assertEqual(value[0].id, "")
        self.assertEqual(value[0].value, "<p>this is a paragraph</p>")

        self.assertEqual(value[1].block_type, "heading")
        self.assertEqual(value[1].id, "0000")
        self.assertEqual(value[1].value, "this is my heading")

    def check_get_prep_value(self, stream_data, is_lazy):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()

        value = blocks.StreamValue(block, stream_data, is_lazy=is_lazy)
        jsonish_value = block.get_prep_value(value)

        self.assertEqual(len(jsonish_value), 2)
        self.assertEqual(
            jsonish_value[0],
            {"type": "heading", "value": "this is my heading", "id": "0000"},
        )
        self.assertEqual(jsonish_value[1]["type"], "paragraph")
        self.assertEqual(jsonish_value[1]["value"], "<p>this is a paragraph</p>")
        # get_prep_value should assign a new (random and non-empty)
        # ID to this block, as it didn't have one already.
        self.assertTrue(jsonish_value[1]["id"])

        # Calling get_prep_value again should preserve existing IDs, including the one
        # just assigned to block 1
        jsonish_value_again = block.get_prep_value(value)
        self.assertEqual(jsonish_value[0]["id"], jsonish_value_again[0]["id"])
        self.assertEqual(jsonish_value[1]["id"], jsonish_value_again[1]["id"])

    def test_get_prep_value_not_lazy(self):
        stream_data = [
            ("heading", "this is my heading", "0000"),
            ("paragraph", "<p>this is a paragraph</p>"),
        ]
        self.check_get_prep_value(stream_data, is_lazy=False)

    def test_get_prep_value_is_lazy(self):
        stream_data = [
            {"type": "heading", "value": "this is my heading", "id": "0000"},
            {"type": "paragraph", "value": "<p>this is a paragraph</p>"},
        ]
        self.check_get_prep_value(stream_data, is_lazy=True)

    def check_get_prep_value_nested_streamblocks(self, stream_data, is_lazy):
        class TwoColumnBlock(blocks.StructBlock):
            left = blocks.StreamBlock([("text", blocks.CharBlock())])
            right = blocks.StreamBlock([("text", blocks.CharBlock())])

        block = TwoColumnBlock()

        value = {
            k: blocks.StreamValue(block.child_blocks[k], v, is_lazy=is_lazy)
            for k, v in stream_data.items()
        }
        jsonish_value = block.get_prep_value(value)

        self.assertEqual(len(jsonish_value), 2)
        self.assertEqual(
            jsonish_value["left"],
            [{"type": "text", "value": "some text", "id": "0000"}],
        )

        self.assertEqual(len(jsonish_value["right"]), 1)
        right_block = jsonish_value["right"][0]
        self.assertEqual(right_block["type"], "text")
        self.assertEqual(right_block["value"], "some other text")
        # get_prep_value should assign a new (random and non-empty)
        # ID to this block, as it didn't have one already.
        self.assertTrue(right_block["id"])

    def test_get_prep_value_nested_streamblocks_not_lazy(self):
        stream_data = {
            "left": [("text", "some text", "0000")],
            "right": [("text", "some other text")],
        }
        self.check_get_prep_value_nested_streamblocks(stream_data, is_lazy=False)

    def test_get_prep_value_nested_streamblocks_is_lazy(self):
        stream_data = {
            "left": [
                {
                    "type": "text",
                    "value": "some text",
                    "id": "0000",
                }
            ],
            "right": [
                {
                    "type": "text",
                    "value": "some other text",
                }
            ],
        }
        self.check_get_prep_value_nested_streamblocks(stream_data, is_lazy=True)

    def test_modifications_to_stream_child_id_are_saved(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )
        stream[1].id = "0003"
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0003"},
            ],
        )

    def test_modifications_to_stream_child_value_are_saved(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )
        stream[1].value = "earth"
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "earth", "id": "0002"},
            ],
        )

    def test_set_streamvalue_item(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )
        stream[1] = ("heading", "goodbye", "0003")
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "heading", "value": "goodbye", "id": "0003"},
            ],
        )

    def test_delete_streamvalue_item(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )
        del stream[0]
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "paragraph", "value": "world", "id": "0002"},
            ],
        )

    def test_insert_streamvalue_item(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )
        stream.insert(1, ("paragraph", "mutable", "0003"))
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "mutable", "id": "0003"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ],
        )

    def test_append_streamvalue_item(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )
        stream.append(("paragraph", "of warcraft", "0003"))
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
                {"type": "paragraph", "value": "of warcraft", "id": "0003"},
            ],
        )

    def test_streamvalue_raw_data(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.CharBlock()

        block = ArticleBlock()
        stream = block.to_python(
            [
                {"type": "heading", "value": "hello", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ]
        )

        self.assertEqual(
            stream.raw_data[0], {"type": "heading", "value": "hello", "id": "0001"}
        )
        stream.raw_data[0]["value"] = "bonjour"
        self.assertEqual(
            stream.raw_data[0], {"type": "heading", "value": "bonjour", "id": "0001"}
        )

        # changes to raw_data will be written back via get_prep_value...
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "bonjour", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ],
        )

        # ...but once the bound-block representation has been accessed, that takes precedence
        self.assertEqual(stream[0].value, "bonjour")
        stream.raw_data[0]["value"] = "guten tag"
        self.assertEqual(stream.raw_data[0]["value"], "guten tag")
        self.assertEqual(stream[0].value, "bonjour")
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "bonjour", "id": "0001"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ],
        )

        # Replacing a raw_data entry outright will propagate to the bound block, though
        stream.raw_data[0] = {"type": "heading", "value": "konnichiwa", "id": "0003"}
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "heading", "value": "konnichiwa", "id": "0003"},
                {"type": "paragraph", "value": "world", "id": "0002"},
            ],
        )
        self.assertEqual(stream[0].value, "konnichiwa")

        # deletions / insertions on raw_data will also propagate to the bound block representation
        del stream.raw_data[1]
        stream.raw_data.insert(
            0, {"type": "paragraph", "value": "hello kitty says", "id": "0004"}
        )
        raw_data = block.get_prep_value(stream)
        self.assertEqual(
            raw_data,
            [
                {"type": "paragraph", "value": "hello kitty says", "id": "0004"},
                {"type": "heading", "value": "konnichiwa", "id": "0003"},
            ],
        )

    def test_adapt_with_form_attrs(self):
        block = blocks.StreamBlock(
            [
                (b"heading", blocks.CharBlock()),
                (b"paragraph", blocks.CharBlock()),
            ],
            form_attrs={
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

        block.set_name("test_streamblock")
        js_args = StreamBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[3].get("attrs"),
            {
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

    def test_adapt_with_classname_via_kwarg(self):
        """form_classname from kwargs to be used as an additional class when rendering stream block"""

        block = blocks.StreamBlock(
            [
                (b"heading", blocks.CharBlock()),
                (b"paragraph", blocks.CharBlock()),
            ],
            form_classname="rocket-section",
        )

        block.set_name("test_streamblock")
        js_args = StreamBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[3],
            {
                "label": "Test streamblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "minNum": None,
                "maxNum": None,
                "blockCounts": {},
                "collapsed": False,
                "required": True,
                "classname": "rocket-section",
                "attrs": {},
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )

    def test_block_names(self):
        class ArticleBlock(blocks.StreamBlock):
            heading = blocks.CharBlock()
            paragraph = blocks.TextBlock()
            date = blocks.DateBlock()

        block = ArticleBlock()
        value = block.to_python(
            [
                {
                    "type": "heading",
                    "value": "My title",
                },
                {
                    "type": "paragraph",
                    "value": "My first paragraph",
                },
                {
                    "type": "paragraph",
                    "value": "My second paragraph",
                },
            ]
        )
        blocks_by_name = value.blocks_by_name()
        assert isinstance(blocks_by_name, blocks.StreamValue.BlockNameLookup)

        # unpack results to a dict of {block name: list of block values} for easier comparison
        result = {
            block_name: [block.value for block in blocks]
            for block_name, blocks in blocks_by_name.items()
        }
        self.assertEqual(
            result,
            {
                "heading": ["My title"],
                "paragraph": ["My first paragraph", "My second paragraph"],
                "date": [],
            },
        )

        paragraph_blocks = value.blocks_by_name(block_name="paragraph")
        # We can also access by indexing on the stream
        self.assertEqual(paragraph_blocks, value.blocks_by_name()["paragraph"])

        self.assertEqual(len(paragraph_blocks), 2)
        for block in paragraph_blocks:
            self.assertEqual(block.block_type, "paragraph")

        self.assertEqual(value.blocks_by_name(block_name="date"), [])
        self.assertEqual(value.blocks_by_name(block_name="invalid_type"), [])

        first_heading_block = value.first_block_by_name(block_name="heading")
        self.assertEqual(first_heading_block.block_type, "heading")
        self.assertEqual(first_heading_block.value, "My title")

        self.assertIs(value.first_block_by_name(block_name="date"), None)
        self.assertIs(value.first_block_by_name(block_name="invalid_type"), None)

        # first_block_by_name with no argument returns a dict-like lookup of first blocks per name
        first_blocks_by_name = value.first_block_by_name()
        first_heading_block = first_blocks_by_name["heading"]
        self.assertEqual(first_heading_block.block_type, "heading")
        self.assertEqual(first_heading_block.value, "My title")

        self.assertIs(first_blocks_by_name["date"], None)
        self.assertIs(first_blocks_by_name["invalid_type"], None)

    def test_adapt_with_classname_via_class_meta(self):
        """form_classname from meta to be used as an additional class when rendering stream block"""

        class ProfileBlock(blocks.StreamBlock):
            username = blocks.CharBlock()

            class Meta:
                form_classname = "profile-block-large"

        block = ProfileBlock()

        block.set_name("test_streamblock")
        js_args = StreamBlockAdapter().js_args(block)

        self.assertEqual(
            js_args[3],
            {
                "label": "Test streamblock",
                "description": "",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "minNum": None,
                "maxNum": None,
                "blockCounts": {},
                "collapsed": False,
                "required": True,
                "classname": "profile-block-large",
                "attrs": {},
                "strings": {
                    "DELETE": "Delete",
                    "DUPLICATE": "Duplicate",
                    "MOVE_DOWN": "Move down",
                    "MOVE_UP": "Move up",
                    "DRAG": "Drag",
                    "ADD": "Add",
                },
            },
        )


class TestNormalizeStreamBlock(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.simple_block = blocks.StreamBlock(
            [("number", blocks.IntegerBlock()), ("text", blocks.TextBlock())]
        )
        cls.recursive_block = blocks.StreamBlock(
            [
                (
                    "inner_stream",
                    blocks.StreamBlock(
                        [
                            ("number", blocks.IntegerBlock()),
                            ("text", blocks.TextBlock()),
                            ("inner_list", blocks.ListBlock(blocks.IntegerBlock)),
                        ]
                    ),
                ),
                ("struct", blocks.StructBlock([("bool", blocks.BooleanBlock())])),
                ("list", blocks.ListBlock(blocks.IntegerBlock)),
            ]
        )

    def test_normalize_empty_stream(self):
        values = [[], "", None]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(
                    self.simple_block.normalize(value),
                    blocks.StreamValue(self.simple_block, []),
                )

    def test_normalize_base_case(self):
        """
        Test normalize when trivially recursive, or already a StreamValue
        """
        value = [("number", 1), ("text", "ichiban")]
        stream_value = blocks.StreamValue(self.simple_block, value)
        self.assertEqual(stream_value, self.simple_block.normalize(value))
        self.assertEqual(stream_value, self.simple_block.normalize(stream_value))

    def test_normalize_recursive(self):
        """
        A stream block is normalized iff all of its sub-blocks are normalized.
        """
        values = (
            # A smart, "list of tuples" representation
            [
                ("struct", {"bool": True}),
                (
                    "inner_stream",
                    [
                        ("number", 1),
                        ("text", "one"),
                        ("inner_list", [0, 1, 1, 2, 3, 5]),
                    ],
                ),
                ("list", [0, 1, 1, 2, 3, 5]),
            ],
            # A json-ish representation - the serialized format
            [
                {"type": "struct", "value": {"bool": True}},
                {
                    "type": "inner_stream",
                    "value": [
                        {"type": "number", "value": 1},
                        {"type": "text", "value": "one"},
                        {
                            "type": "inner_list",
                            "value": [
                                # Unlike StreamBlock, ListBlock requires that its items
                                # have IDs, to distinguish the new serialization format
                                # from the old.
                                {"type": "item", "value": 0, "id": 1},
                                {"type": "item", "value": 1, "id": 2},
                                {"type": "item", "value": 2, "id": 3},
                            ],
                        },
                    ],
                },
                {
                    "type": "list",
                    "value": [
                        {"type": "item", "value": 0, "id": 1},
                        {"type": "item", "value": 1, "id": 2},
                        {"type": "item", "value": 2, "id": 3},
                    ],
                },
            ],
        )

        for value in values:
            with self.subTest(value=value):
                # Normalize the value.
                normalized = self.recursive_block.normalize(value)
                # Then check all of the sub-blocks have been normalized:
                # the StructBlock child
                self.assertIsInstance(normalized[0].value, blocks.StructValue)
                self.assertIsInstance(normalized[0].value["bool"], bool)
                # the nested StreamBlock child
                self.assertIsInstance(normalized[1].value, blocks.StreamValue)
                self.assertIsInstance(normalized[1].value[0].value, int)
                self.assertIsInstance(normalized[1].value[1].value, str)
                # the ListBlock child
                self.assertIsInstance(normalized[2].value[0], int)
                self.assertIsInstance(normalized[2].value, blocks.list_block.ListValue)
                # the inner ListBlock nested in the nested streamblock
                self.assertIsInstance(normalized[1].value[2].value[0], int)
                self.assertIsInstance(
                    normalized[1].value[2].value, blocks.list_block.ListValue
                )


class TestStructBlockWithFixtures(TestCase):
    fixtures = ["test.json"]

    def test_bulk_to_python(self):
        page_link_block = blocks.StructBlock(
            [
                ("page", blocks.PageChooserBlock(required=False)),
                ("link_text", blocks.CharBlock(default="missing title")),
            ]
        )

        with self.assertNumQueries(1):
            result = page_link_block.bulk_to_python(
                [
                    {"page": 2, "link_text": "page two"},
                    {"page": 3, "link_text": "page three"},
                    {"page": None, "link_text": "no page"},
                    {"page": 4},
                ]
            )

        result_types = [type(val) for val in result]
        self.assertEqual(result_types, [blocks.StructValue] * 4)

        result_titles = [val["link_text"] for val in result]
        self.assertEqual(
            result_titles, ["page two", "page three", "no page", "missing title"]
        )

        result_pages = [val["page"] for val in result]
        self.assertEqual(
            result_pages,
            [
                Page.objects.get(id=2),
                Page.objects.get(id=3),
                None,
                Page.objects.get(id=4),
            ],
        )

    def test_extract_references(self):
        block = blocks.StructBlock(
            [
                ("page", blocks.PageChooserBlock(required=False)),
                ("link_text", blocks.CharBlock(default="missing title")),
            ]
        )

        christmas_page = Page.objects.get(slug="christmas")

        self.assertListEqual(
            list(
                block.extract_references(
                    {"page": christmas_page, "link_text": "Christmas"}
                )
            ),
            [
                (Page, str(christmas_page.id), "page", "page"),
            ],
        )


class TestStreamBlockWithFixtures(TestCase):
    fixtures = ["test.json"]

    def test_bulk_to_python(self):
        stream_block = blocks.StreamBlock(
            [
                ("page", blocks.PageChooserBlock()),
                ("heading", blocks.CharBlock()),
            ]
        )

        # The naive implementation of bulk_to_python (calling to_python on each item) would perform
        # NO queries, as StreamBlock.to_python returns a lazy StreamValue that only starts calling
        # to_python on its children (and thus triggering DB queries) when its items are accessed.
        # This is a good thing for a standalone to_python call, because loading a model instance
        # with a StreamField in it will immediately call StreamField.to_python which in turn calls
        # to_python on the top-level StreamBlock, and we really don't want
        # SomeModelWithAStreamField.objects.get(id=1) to immediately trigger a cascading fetch of
        # all objects referenced in the StreamField.
        #
        # However, for bulk_to_python that's bad, as it means each stream in the list would end up
        # doing its own object lookups in isolation, missing the opportunity to group them together
        # into a single call to the child block's bulk_to_python. Therefore, the ideal outcome is
        # that we perform one query now (covering all PageChooserBlocks across all streams),
        # returning a list of non-lazy StreamValues.

        with self.assertNumQueries(1):
            results = stream_block.bulk_to_python(
                [
                    [
                        {"type": "heading", "value": "interesting pages"},
                        {"type": "page", "value": 2},
                        {"type": "page", "value": 3},
                    ],
                    [
                        {"type": "heading", "value": "pages written by dogs"},
                        {"type": "woof", "value": "woof woof"},
                    ],
                    [
                        {"type": "heading", "value": "boring pages"},
                        {"type": "page", "value": 4},
                    ],
                ]
            )

        # If bulk_to_python has indeed given us non-lazy StreamValues, then no further queries
        # should be performed when iterating over its child blocks.
        with self.assertNumQueries(0):
            block_types = [[block.block_type for block in stream] for stream in results]
        self.assertEqual(
            block_types,
            [
                ["heading", "page", "page"],
                ["heading"],
                ["heading", "page"],
            ],
        )

        with self.assertNumQueries(0):
            block_values = [[block.value for block in stream] for stream in results]
        self.assertEqual(
            block_values,
            [
                ["interesting pages", Page.objects.get(id=2), Page.objects.get(id=3)],
                ["pages written by dogs"],
                ["boring pages", Page.objects.get(id=4)],
            ],
        )

    def test_extract_references(self):
        block = blocks.StreamBlock(
            [
                ("page", blocks.PageChooserBlock()),
                ("heading", blocks.CharBlock()),
            ]
        )

        christmas_page = Page.objects.get(slug="christmas")
        saint_patrick_page = Page.objects.get(slug="saint-patrick")

        self.assertListEqual(
            list(
                block.extract_references(
                    block.to_python(
                        [
                            {
                                "id": "block1",
                                "type": "heading",
                                "value": "Some events that you might like",
                            },
                            {
                                "id": "block2",
                                "type": "page",
                                "value": christmas_page.id,
                            },
                            {
                                "id": "block3",
                                "type": "page",
                                "value": saint_patrick_page.id,
                            },
                        ]
                    )
                )
            ),
            [
                (Page, str(christmas_page.id), "page", "block2"),
                (Page, str(saint_patrick_page.id), "page", "block3"),
            ],
        )


class TestPageChooserBlock(TestCase):
    fixtures = ["test.json"]

    def test_serialize(self):
        """The value of a PageChooserBlock (a Page object) should serialize to an ID"""
        block = blocks.PageChooserBlock()
        christmas_page = Page.objects.get(slug="christmas")

        self.assertEqual(block.get_prep_value(christmas_page), christmas_page.id)

        # None should serialize to None
        self.assertIsNone(block.get_prep_value(None))

    def test_deserialize(self):
        """The serialized value of a PageChooserBlock (an ID) should deserialize to a Page object"""
        block = blocks.PageChooserBlock()
        christmas_page = Page.objects.get(slug="christmas")

        self.assertEqual(block.to_python(christmas_page.id), christmas_page)

        # None should deserialize to None
        self.assertIsNone(block.to_python(None))

    def test_adapt(self):
        from wagtail.admin.widgets.chooser import AdminPageChooser

        block = blocks.PageChooserBlock(
            help_text="pick a page, any page", description="A featured page"
        )

        block.set_name("test_pagechooserblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_pagechooserblock")
        self.assertIsInstance(js_args[1], AdminPageChooser)
        self.assertEqual(js_args[1].target_models, [Page])
        self.assertFalse(js_args[1].can_choose_root)
        self.assertEqual(
            js_args[2],
            {
                "label": "Test pagechooserblock",
                "description": "A featured page",
                "required": True,
                "icon": "doc-empty-inverse",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "helpText": "pick a page, any page",
                "classname": "w-field w-field--model_choice_field w-field--admin_page_chooser",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_adapt_with_target_model_string(self):
        block = blocks.PageChooserBlock(
            help_text="pick a page, any page", page_type="tests.SimplePage"
        )

        block.set_name("test_pagechooserblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].target_models, [SimplePage])

    def test_adapt_with_target_model_literal(self):
        block = blocks.PageChooserBlock(
            help_text="pick a page, any page", page_type=SimplePage
        )

        block.set_name("test_pagechooserblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].target_models, [SimplePage])

    def test_adapt_with_target_model_multiple_strings(self):
        block = blocks.PageChooserBlock(
            help_text="pick a page, any page",
            page_type=["tests.SimplePage", "tests.EventPage"],
        )

        block.set_name("test_pagechooserblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].target_models, [SimplePage, EventPage])

    def test_adapt_with_target_model_multiple_literals(self):
        block = blocks.PageChooserBlock(
            help_text="pick a page, any page", page_type=[SimplePage, EventPage]
        )

        block.set_name("test_pagechooserblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].target_models, [SimplePage, EventPage])

    def test_adapt_with_can_choose_root(self):
        block = blocks.PageChooserBlock(
            help_text="pick a page, any page", can_choose_root=True
        )

        block.set_name("test_pagechooserblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertTrue(js_args[1].can_choose_root)

    def test_form_response(self):
        block = blocks.PageChooserBlock()
        christmas_page = Page.objects.get(slug="christmas")

        value = block.value_from_datadict({"page": str(christmas_page.id)}, {}, "page")
        self.assertEqual(value, christmas_page)

        empty_value = block.value_from_datadict({"page": ""}, {}, "page")
        self.assertIsNone(empty_value)

    def test_clean(self):
        required_block = blocks.PageChooserBlock()
        nonrequired_block = blocks.PageChooserBlock(required=False)
        christmas_page = Page.objects.get(slug="christmas")

        self.assertEqual(required_block.clean(christmas_page), christmas_page)
        with self.assertRaises(ValidationError):
            required_block.clean(None)

        self.assertEqual(nonrequired_block.clean(christmas_page), christmas_page)
        self.assertIsNone(nonrequired_block.clean(None))

    def test_target_model_default(self):
        block = blocks.PageChooserBlock()
        self.assertEqual(block.target_model, Page)

    def test_target_model_string(self):
        block = blocks.PageChooserBlock(page_type="tests.SimplePage")
        self.assertEqual(block.target_model, SimplePage)

    def test_target_model_literal(self):
        block = blocks.PageChooserBlock(page_type=SimplePage)
        self.assertEqual(block.target_model, SimplePage)

    def test_target_model_multiple_strings(self):
        block = blocks.PageChooserBlock(
            page_type=["tests.SimplePage", "tests.EventPage"]
        )
        self.assertEqual(block.target_model, Page)

    def test_target_model_multiple_literals(self):
        block = blocks.PageChooserBlock(page_type=[SimplePage, EventPage])
        self.assertEqual(block.target_model, Page)

    def test_deconstruct_target_model_default(self):
        block = blocks.PageChooserBlock()
        self.assertEqual(
            block.deconstruct(), ("wagtail.blocks.PageChooserBlock", (), {})
        )

    def test_deconstruct_target_model_string(self):
        block = blocks.PageChooserBlock(page_type="tests.SimplePage")
        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.PageChooserBlock",
                (),
                {"page_type": ["tests.SimplePage"]},
            ),
        )

    def test_deconstruct_target_model_literal(self):
        block = blocks.PageChooserBlock(page_type=SimplePage)
        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.PageChooserBlock",
                (),
                {"page_type": ["tests.SimplePage"]},
            ),
        )

    def test_deconstruct_target_model_multiple_strings(self):
        block = blocks.PageChooserBlock(
            page_type=["tests.SimplePage", "tests.EventPage"]
        )
        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.PageChooserBlock",
                (),
                {"page_type": ["tests.SimplePage", "tests.EventPage"]},
            ),
        )

    def test_deconstruct_target_model_multiple_literals(self):
        block = blocks.PageChooserBlock(page_type=[SimplePage, EventPage])
        self.assertEqual(
            block.deconstruct(),
            (
                "wagtail.blocks.PageChooserBlock",
                (),
                {"page_type": ["tests.SimplePage", "tests.EventPage"]},
            ),
        )

    def test_bulk_to_python(self):
        page_ids = [2, 3, 4, 5]
        expected_pages = Page.objects.filter(pk__in=page_ids)
        block = blocks.PageChooserBlock()

        with self.assertNumQueries(1):
            pages = block.bulk_to_python(page_ids)

        self.assertSequenceEqual(pages, expected_pages)

    def test_bulk_to_python_distinct_instances(self):
        page_ids = [2, 2]
        block = blocks.PageChooserBlock()

        with self.assertNumQueries(1):
            pages = block.bulk_to_python(page_ids)

        # Ensure that the two retrieved pages are distinct instances
        self.assertIsNot(pages[0], pages[1])

    def test_extract_references(self):
        block = blocks.PageChooserBlock()
        christmas_page = Page.objects.get(slug="christmas")

        self.assertListEqual(
            list(block.extract_references(christmas_page)),
            [(Page, str(christmas_page.id), "", "")],
        )

        # None should not yield any references
        self.assertListEqual(list(block.extract_references(None)), [])


class TestStaticBlock(unittest.TestCase):
    def test_adapt_with_constructor(self):
        block = blocks.StaticBlock(
            admin_text="Latest posts - This block doesn't need to be configured, it will be displayed automatically",
            template="tests/blocks/posts_static_block.html",
        )

        block.set_name("posts_static_block")
        js_args = StaticBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "posts_static_block")
        self.assertEqual(
            js_args[1],
            {
                "text": "Latest posts - This block doesn't need to be configured, it will be displayed automatically",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "label": "Posts static block",
                "description": "",
                "attrs": {},
            },
        )

    def test_adapt_with_subclass(self):
        class PostsStaticBlock(blocks.StaticBlock):
            class Meta:
                admin_text = "Latest posts - This block doesn't need to be configured, it will be displayed automatically"
                template = "tests/blocks/posts_static_block.html"

        block = PostsStaticBlock()

        block.set_name("posts_static_block")
        js_args = StaticBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "posts_static_block")
        self.assertEqual(
            js_args[1],
            {
                "text": "Latest posts - This block doesn't need to be configured, it will be displayed automatically",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "label": "Posts static block",
                "description": "",
                "attrs": {},
            },
        )

    def test_adapt_with_subclass_displays_default_text_if_no_admin_text(self):
        class LabelOnlyStaticBlock(blocks.StaticBlock):
            class Meta:
                label = "Latest posts"

        block = LabelOnlyStaticBlock()

        block.set_name("posts_static_block")
        js_args = StaticBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "posts_static_block")
        self.assertEqual(
            js_args[1],
            {
                "text": "Latest posts: this block has no options.",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "label": "Latest posts",
                "description": "",
                "attrs": {},
            },
        )

    def test_adapt_with_subclass_displays_default_text_if_no_admin_text_and_no_label(
        self,
    ):
        class NoMetaStaticBlock(blocks.StaticBlock):
            pass

        block = NoMetaStaticBlock()

        block.set_name("posts_static_block")
        js_args = StaticBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "posts_static_block")
        self.assertEqual(
            js_args[1],
            {
                "text": "Posts static block: this block has no options.",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "label": "Posts static block",
                "description": "",
                "attrs": {},
            },
        )

    def test_adapt_works_with_mark_safe(self):
        block = blocks.StaticBlock(
            admin_text=mark_safe(
                "<b>Latest posts</b> - This block doesn't need to be configured, it will be displayed automatically"
            ),
            template="tests/blocks/posts_static_block.html",
        )

        block.set_name("posts_static_block")
        js_args = StaticBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "posts_static_block")
        self.assertEqual(
            js_args[1],
            {
                "html": "<b>Latest posts</b> - This block doesn't need to be configured, it will be displayed automatically",
                "icon": "placeholder",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "label": "Posts static block",
                "description": "",
                "attrs": {},
            },
        )

    def test_adapt_with_form_attrs(self):
        block = blocks.StaticBlock(
            admin_text="Latest posts - This block doesn't need to be configured, it will be displayed automatically",
            template="tests/blocks/posts_static_block.html",
            form_attrs={
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

        block.set_name("posts_static_block")
        js_args = StaticBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "posts_static_block")
        self.assertEqual(
            js_args[1].get("attrs"),
            {
                "data-controller": "w-custom",
                "data-action": "click->w-custom#doSomething",
            },
        )

    def test_get_default(self):
        block = blocks.StaticBlock()
        default_value = block.get_default()
        self.assertIsNone(default_value)

    def test_render(self):
        block = blocks.StaticBlock(template="tests/blocks/posts_static_block.html")
        result = block.render(None)
        self.assertEqual(result, "<p>PostsStaticBlock template</p>")

    def test_render_without_template(self):
        block = blocks.StaticBlock()
        result = block.render(None)
        self.assertEqual(result, "")

    def test_serialize(self):
        block = blocks.StaticBlock()
        result = block.get_prep_value(None)
        self.assertIsNone(result)

    def test_deserialize(self):
        block = blocks.StaticBlock()
        result = block.to_python(None)
        self.assertIsNone(result)

    def test_normalize(self):
        """
        StaticBlock.normalize always returns None, as a StaticBlock has no value
        """
        self.assertIsNone(blocks.StaticBlock().normalize(11))


class TestDateBlock(TestCase):
    def test_adapt(self):
        from wagtail.admin.widgets.datetime import AdminDateInput

        block = blocks.DateBlock()

        block.set_name("test_dateblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_dateblock")
        self.assertIsInstance(js_args[1], AdminDateInput)
        self.assertEqual(js_args[1].js_format, "Y-m-d")
        self.assertEqual(
            js_args[2],
            {
                "label": "Test dateblock",
                "description": "",
                "required": True,
                "icon": "date",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--date_field w-field--admin_date_input",
                "showAddCommentButton": True,
                "attrs": {},
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_adapt_with_format(self):
        block = blocks.DateBlock(format="%d.%m.%Y")

        block.set_name("test_dateblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].js_format, "d.m.Y")


class TestTimeBlock(TestCase):
    def test_adapt(self):
        from wagtail.admin.widgets.datetime import AdminTimeInput

        block = blocks.TimeBlock()

        block.set_name("test_timeblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_timeblock")
        self.assertIsInstance(js_args[1], AdminTimeInput)
        self.assertEqual(js_args[1].js_format, "H:i")
        self.assertEqual(
            js_args[2],
            {
                "label": "Test timeblock",
                "description": "",
                "required": True,
                "icon": "time",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--time_field w-field--admin_time_input",
                "showAddCommentButton": True,
                "attrs": {},
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_adapt_with_format(self):
        block = blocks.TimeBlock(format="%H:%M:%S")

        block.set_name("test_timeblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].js_format, "H:i:s")


class TestDateTimeBlock(TestCase):
    def test_adapt(self):
        from wagtail.admin.widgets.datetime import AdminDateTimeInput

        block = blocks.DateTimeBlock()

        block.set_name("test_datetimeblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[0], "test_datetimeblock")
        self.assertIsInstance(js_args[1], AdminDateTimeInput)
        self.assertEqual(js_args[1].js_format, "Y-m-d H:i")
        self.assertEqual(
            js_args[2],
            {
                "label": "Test datetimeblock",
                "description": "",
                "required": True,
                "icon": "date",
                "blockDefId": block.definition_prefix,
                "isPreviewable": block.is_previewable,
                "classname": "w-field w-field--date_time_field w-field--admin_date_time_input",
                "attrs": {},
                "showAddCommentButton": True,
                "strings": {"ADD_COMMENT": "Add Comment"},
            },
        )

    def test_adapt_with_format(self):
        block = blocks.DateTimeBlock(format="%d.%m.%Y %H:%M")

        block.set_name("test_datetimeblock")
        js_args = FieldBlockAdapter().js_args(block)

        self.assertEqual(js_args[1].js_format, "d.m.Y H:i")


class TestSystemCheck(TestCase):
    def test_name_cannot_contain_non_alphanumeric(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
                ("rich+text", blocks.RichTextBlock()),
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(
            errors[0].hint,
            "Block names should follow standard Python conventions for variable names: alphanumeric and underscores, and cannot begin with a digit",
        )
        self.assertEqual(errors[0].obj, block.child_blocks["rich+text"])

    def test_name_must_be_nonempty(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
                ("", blocks.RichTextBlock()),
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block name cannot be empty")
        self.assertEqual(errors[0].obj, block.child_blocks[""])

    def test_name_cannot_contain_spaces(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
                ("rich text", blocks.RichTextBlock()),
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block names cannot contain spaces")
        self.assertEqual(errors[0].obj, block.child_blocks["rich text"])

    def test_name_cannot_contain_dashes(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
                ("rich-text", blocks.RichTextBlock()),
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block names cannot contain dashes")
        self.assertEqual(errors[0].obj, block.child_blocks["rich-text"])

    def test_name_cannot_begin_with_digit(self):
        block = blocks.StreamBlock(
            [
                ("heading", blocks.CharBlock()),
                ("99richtext", blocks.RichTextBlock()),
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block names cannot begin with a digit")
        self.assertEqual(errors[0].obj, block.child_blocks["99richtext"])

    def test_system_checks_recurse_into_lists(self):
        failing_block = blocks.RichTextBlock()
        block = blocks.StreamBlock(
            [
                (
                    "paragraph_list",
                    blocks.ListBlock(
                        blocks.StructBlock(
                            [
                                ("heading", blocks.CharBlock()),
                                ("rich text", failing_block),
                            ]
                        )
                    ),
                )
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block names cannot contain spaces")
        self.assertEqual(errors[0].obj, failing_block)

    def test_system_checks_recurse_into_streams(self):
        failing_block = blocks.RichTextBlock()
        block = blocks.StreamBlock(
            [
                (
                    "carousel",
                    blocks.StreamBlock(
                        [
                            (
                                "text",
                                blocks.StructBlock(
                                    [
                                        ("heading", blocks.CharBlock()),
                                        ("rich text", failing_block),
                                    ]
                                ),
                            )
                        ]
                    ),
                )
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block names cannot contain spaces")
        self.assertEqual(errors[0].obj, failing_block)

    def test_system_checks_recurse_into_structs(self):
        failing_block_1 = blocks.RichTextBlock()
        failing_block_2 = blocks.RichTextBlock()
        block = blocks.StreamBlock(
            [
                (
                    "two_column",
                    blocks.StructBlock(
                        [
                            (
                                "left",
                                blocks.StructBlock(
                                    [
                                        ("heading", blocks.CharBlock()),
                                        ("rich text", failing_block_1),
                                    ]
                                ),
                            ),
                            (
                                "right",
                                blocks.StructBlock(
                                    [
                                        ("heading", blocks.CharBlock()),
                                        ("rich text", failing_block_2),
                                    ]
                                ),
                            ),
                        ]
                    ),
                )
            ]
        )

        errors = block.check()
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0].id, "wagtailcore.E001")
        self.assertEqual(errors[0].hint, "Block names cannot contain spaces")
        self.assertEqual(errors[0].obj, failing_block_1)
        self.assertEqual(errors[1].id, "wagtailcore.E001")
        self.assertEqual(errors[1].hint, "Block names cannot contain spaces")
        self.assertEqual(errors[1].obj, failing_block_2)


class TestTemplateRendering(TestCase):
    def test_render_with_custom_context(self):
        block = CustomLinkBlock()
        value = block.to_python({"title": "Torchbox", "url": "http://torchbox.com/"})
        context = {"classname": "important"}
        result = block.render(value, context)

        self.assertEqual(
            result, '<a href="http://torchbox.com/" class="important">Torchbox</a>'
        )

    @unittest.expectedFailure  # TODO(telepath)
    def test_render_with_custom_form_context(self):
        block = CustomLinkBlock()
        value = block.to_python({"title": "Torchbox", "url": "http://torchbox.com/"})
        result = block.render_form(value, prefix="my-link-block")

        self.assertIn('data-prefix="my-link-block"', result)
        self.assertIn("<p>Hello from get_form_context!</p>", result)


class TestIncludeBlockTag(TestCase):
    def test_include_block_tag_with_boundblock(self):
        """
        The include_block tag should be able to render a BoundBlock's template
        while keeping the parent template's context
        """
        block = blocks.CharBlock(template="tests/blocks/heading_block.html")
        bound_block = block.bind("bonjour")

        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": bound_block,
                "language": "fr",
            },
        )
        self.assertIn('<body><h1 lang="fr">bonjour</h1></body>', result)

    def test_include_block_tag_with_structvalue(self):
        """
        The include_block tag should be able to render a StructValue's template
        while keeping the parent template's context
        """
        block = SectionBlock()
        struct_value = block.to_python(
            {"title": "Bonjour", "body": "monde <i>italique</i>"}
        )

        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": struct_value,
                "language": "fr",
            },
        )

        self.assertIn(
            """<body><h1 lang="fr">Bonjour</h1>monde <i>italique</i></body>""", result
        )

    def test_include_block_tag_with_streamvalue(self):
        """
        The include_block tag should be able to render a StreamValue's template
        while keeping the parent template's context
        """
        block = blocks.StreamBlock(
            [
                (
                    "heading",
                    blocks.CharBlock(template="tests/blocks/heading_block.html"),
                ),
                ("paragraph", blocks.CharBlock()),
            ],
            template="tests/blocks/stream_with_language.html",
        )

        stream_value = block.to_python([{"type": "heading", "value": "Bonjour"}])

        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": stream_value,
                "language": "fr",
            },
        )

        self.assertIn(
            '<div class="heading" lang="fr"><h1 lang="fr">Bonjour</h1></div>', result
        )

    def test_include_block_tag_with_plain_value(self):
        """
        The include_block tag should be able to render a value without a render_as_block method
        by just rendering it as a string
        """
        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": 42,
            },
        )

        self.assertIn("<body>42</body>", result)

    def test_include_block_tag_with_filtered_value(self):
        """
        The block parameter on include_block tag should support complex values including filters,
        e.g. {% include_block foo|default:123 %}
        """
        block = blocks.CharBlock(template="tests/blocks/heading_block.html")
        bound_block = block.bind("bonjour")

        result = render_to_string(
            "tests/blocks/include_block_test_with_filter.html",
            {
                "test_block": bound_block,
                "language": "fr",
            },
        )
        self.assertIn('<body><h1 lang="fr">bonjour</h1></body>', result)

        result = render_to_string(
            "tests/blocks/include_block_test_with_filter.html",
            {
                "test_block": None,
                "language": "fr",
            },
        )
        self.assertIn("<body>999</body>", result)

    def test_include_block_tag_with_extra_context(self):
        """
        Test that it's possible to pass extra context on an include_block tag using
        {% include_block foo with classname="bar" %}
        """
        block = blocks.CharBlock(template="tests/blocks/heading_block.html")
        bound_block = block.bind("bonjour")

        result = render_to_string(
            "tests/blocks/include_block_with_test.html",
            {
                "test_block": bound_block,
                "language": "fr",
            },
        )
        self.assertIn(
            '<body><h1 lang="fr" class="important">bonjour</h1></body>', result
        )

    def test_include_block_tag_with_only_flag(self):
        """
        A tag such as {% include_block foo with classname="bar" only %}
        should not inherit the parent context
        """
        block = blocks.CharBlock(template="tests/blocks/heading_block.html")
        bound_block = block.bind("bonjour")

        result = render_to_string(
            "tests/blocks/include_block_only_test.html",
            {
                "test_block": bound_block,
                "language": "fr",
            },
        )
        self.assertIn('<body><h1 class="important">bonjour</h1></body>', result)

    def test_include_block_html_escaping(self):
        """
        Output of include_block should be escaped as per Django autoescaping rules
        """
        block = blocks.CharBlock()
        bound_block = block.bind(block.to_python("some <em>evil</em> HTML"))

        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": bound_block,
            },
        )
        self.assertIn("<body>some &lt;em&gt;evil&lt;/em&gt; HTML</body>", result)

        # {% autoescape off %} should be respected
        result = render_to_string(
            "tests/blocks/include_block_autoescape_off_test.html",
            {
                "test_block": bound_block,
            },
        )
        self.assertIn("<body>some <em>evil</em> HTML</body>", result)

        # The same escaping should be applied when passed a plain value rather than a BoundBlock -
        # a typical situation where this would occur would be rendering an item of a StructBlock,
        # e.g. {% include_block person_block.first_name %} as opposed to
        # {% include_block person_block.bound_blocks.first_name %}
        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": "some <em>evil</em> HTML",
            },
        )
        self.assertIn("<body>some &lt;em&gt;evil&lt;/em&gt; HTML</body>", result)

        result = render_to_string(
            "tests/blocks/include_block_autoescape_off_test.html",
            {
                "test_block": "some <em>evil</em> HTML",
            },
        )
        self.assertIn("<body>some <em>evil</em> HTML</body>", result)

        # Blocks that explicitly return 'safe HTML'-marked values (such as RawHTMLBlock) should
        # continue to produce unescaped output
        block = blocks.RawHTMLBlock()
        bound_block = block.bind(block.to_python("some <em>evil</em> HTML"))

        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": bound_block,
            },
        )
        self.assertIn("<body>some <em>evil</em> HTML</body>", result)

        # likewise when applied to a plain 'safe HTML' value rather than a BoundBlock
        result = render_to_string(
            "tests/blocks/include_block_test.html",
            {
                "test_block": mark_safe("some <em>evil</em> HTML"),
            },
        )
        self.assertIn("<body>some <em>evil</em> HTML</body>", result)


class TestOverriddenGetTemplateBlockTag(TestCase):
    def test_block_render_passes_the_value_argument_to_get_template(self):
        """verifies Block.render() passes the value to get_template"""

        class BlockChoosingTemplateBasedOnValue(blocks.Block):
            def get_template(self, value=None, context=None):
                if value == "HEADING":
                    return "tests/blocks/heading_block.html"

                return None  # using render_basic

        block = BlockChoosingTemplateBasedOnValue()

        html = block.render("Hello World")
        self.assertEqual(html, "Hello World")

        html = block.render("HEADING")
        self.assertEqual(html, "<h1>HEADING</h1>")


class TestValidationErrorAsJsonData(TestCase):
    def test_plain_validation_error(self):
        error = ValidationError("everything is broken")
        self.assertEqual(
            get_error_json_data(error), {"messages": ["everything is broken"]}
        )

    def test_validation_error_with_multiple_messages(self):
        error = ValidationError(
            [
                ValidationError("everything is broken"),
                ValidationError("even more broken than before"),
            ]
        )
        self.assertEqual(
            get_error_json_data(error),
            {"messages": ["everything is broken", "even more broken than before"]},
        )

    def test_structblock_validation_error(self):
        error = StructBlockValidationError(
            block_errors={
                "name": ErrorList(
                    [
                        ValidationError("This field is required."),
                    ]
                )
            },
            non_block_errors=ErrorList(
                [ValidationError("Either email or telephone number must be specified.")]
            ),
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    "name": {"messages": ["This field is required."]},
                },
                "messages": [
                    "Either email or telephone number must be specified.",
                ],
            },
        )

    def test_structblock_validation_error_with_no_block_errors(self):
        error = StructBlockValidationError(
            non_block_errors=[
                ValidationError("Either email or telephone number must be specified.")
            ]
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "messages": [
                    "Either email or telephone number must be specified.",
                ],
            },
        )

    def test_structblock_validation_error_with_no_non_block_errors(self):
        error = StructBlockValidationError(
            block_errors={
                "name": ValidationError("This field is required."),
            },
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    "name": {"messages": ["This field is required."]},
                },
            },
        )

    def test_streamblock_validation_error(self):
        error = StreamBlockValidationError(
            block_errors={
                2: ErrorList(
                    [
                        StructBlockValidationError(
                            non_block_errors=ErrorList(
                                [
                                    ValidationError(
                                        "Either email or telephone number must be specified."
                                    )
                                ]
                            )
                        )
                    ]
                ),
                4: ErrorList([ValidationError("This field is required.")]),
                6: ErrorList(
                    [
                        StructBlockValidationError(
                            block_errors={
                                "name": ErrorList(
                                    [ValidationError("This field is required.")]
                                ),
                            }
                        )
                    ]
                ),
            },
            non_block_errors=ErrorList(
                [
                    ValidationError("The minimum number of items is 2"),
                    ValidationError("The maximum number of items is 5"),
                ]
            ),
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    2: {
                        "messages": [
                            "Either email or telephone number must be specified."
                        ]
                    },
                    4: {"messages": ["This field is required."]},
                    6: {
                        "blockErrors": {
                            "name": {
                                "messages": ["This field is required."],
                            }
                        }
                    },
                },
                "messages": [
                    "The minimum number of items is 2",
                    "The maximum number of items is 5",
                ],
            },
        )

    def test_streamblock_validation_error_with_no_block_errors(self):
        error = StreamBlockValidationError(
            non_block_errors=[
                ValidationError("The minimum number of items is 2"),
            ],
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "messages": [
                    "The minimum number of items is 2",
                ],
            },
        )

    def test_streamblock_validation_error_with_no_non_block_errors(self):
        error = StreamBlockValidationError(
            block_errors={
                4: [ValidationError("This field is required.")],
                6: ValidationError("This field is required."),
            },
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    4: {"messages": ["This field is required."]},
                    6: {"messages": ["This field is required."]},
                }
            },
        )

    def test_listblock_validation_error_constructed_with_list(self):
        # test the pre-Wagtail-5.0 constructor format for ListBlockValidationError:
        # block_errors passed as a list with None for 'no error', and
        # a single-item ErrorList for validation errors
        error = ListBlockValidationError(
            block_errors=[
                None,
                ErrorList(
                    [
                        StructBlockValidationError(
                            non_block_errors=ErrorList(
                                [
                                    ValidationError(
                                        "Either email or telephone number must be specified."
                                    )
                                ]
                            )
                        )
                    ]
                ),
                ErrorList(
                    [
                        StructBlockValidationError(
                            block_errors={
                                "name": ErrorList(
                                    [ValidationError("This field is required.")]
                                ),
                            }
                        )
                    ]
                ),
            ],
            non_block_errors=ErrorList(
                [
                    ValidationError("The minimum number of items is 2"),
                    ValidationError("The maximum number of items is 5"),
                ]
            ),
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    1: {
                        "messages": [
                            "Either email or telephone number must be specified."
                        ]
                    },
                    2: {
                        "blockErrors": {
                            "name": {
                                "messages": ["This field is required."],
                            }
                        }
                    },
                },
                "messages": [
                    "The minimum number of items is 2",
                    "The maximum number of items is 5",
                ],
            },
        )

    def test_listblock_validation_error_constructed_with_dict(self):
        # test the Wagtail >=5.0 constructor format for ListBlockValidationError:
        # block_errors passed as a dict keyed by block index, where values can be
        # ValidationErrors and plain single-item lists as well as single-item ErrorLists
        error = ListBlockValidationError(
            block_errors={
                1: [
                    StructBlockValidationError(
                        non_block_errors=ErrorList(
                            [
                                ValidationError(
                                    "Either email or telephone number must be specified."
                                )
                            ]
                        )
                    )
                ],
                2: StructBlockValidationError(
                    block_errors={
                        "name": ErrorList([ValidationError("This field is required.")]),
                    }
                ),
            },
            non_block_errors=ErrorList(
                [
                    ValidationError("The minimum number of items is 2"),
                    ValidationError("The maximum number of items is 5"),
                ]
            ),
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    1: {
                        "messages": [
                            "Either email or telephone number must be specified."
                        ]
                    },
                    2: {
                        "blockErrors": {
                            "name": {
                                "messages": ["This field is required."],
                            }
                        }
                    },
                },
                "messages": [
                    "The minimum number of items is 2",
                    "The maximum number of items is 5",
                ],
            },
        )

    def test_listblock_validation_error_with_no_non_block_errors(self):
        error = ListBlockValidationError(
            block_errors={2: ValidationError("This field is required.")},
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "blockErrors": {
                    2: {"messages": ["This field is required."]},
                },
            },
        )

    def test_listblock_validation_error_with_no_block_errors(self):
        error = ListBlockValidationError(
            non_block_errors=[
                ValidationError("The minimum number of items is 2"),
            ]
        )
        self.assertEqual(
            get_error_json_data(error),
            {
                "messages": [
                    "The minimum number of items is 2",
                ],
            },
        )


class TestBlockDefinitionLookup(TestCase):
    def test_simple_lookup(self):
        lookup = BlockDefinitionLookup(
            {
                0: ("wagtail.blocks.CharBlock", [], {"required": True}),
                1: ("wagtail.blocks.RichTextBlock", [], {}),
            }
        )
        char_block = lookup.get_block(0)
        char_block.set_name("title")
        self.assertIsInstance(char_block, blocks.CharBlock)
        self.assertTrue(char_block.required)

        rich_text_block = lookup.get_block(1)
        self.assertIsInstance(rich_text_block, blocks.RichTextBlock)

        # A subsequent call to get_block with the same index should return a new instance;
        # this ensures that state changes such as set_name are independent of other blocks
        char_block_2 = lookup.get_block(0)
        char_block_2.set_name("subtitle")
        self.assertIsInstance(char_block, blocks.CharBlock)
        self.assertTrue(char_block.required)
        self.assertIsNot(char_block, char_block_2)
        self.assertEqual(char_block.name, "title")
        self.assertEqual(char_block_2.name, "subtitle")

    def test_structblock_lookup(self):
        lookup = BlockDefinitionLookup(
            {
                0: ("wagtail.blocks.CharBlock", [], {"required": True}),
                1: ("wagtail.blocks.RichTextBlock", [], {}),
                2: (
                    "wagtail.blocks.StructBlock",
                    [
                        [
                            ("title", 0),
                            ("description", 1),
                        ],
                    ],
                    {},
                ),
            }
        )
        struct_block = lookup.get_block(2)
        self.assertIsInstance(struct_block, blocks.StructBlock)
        title_block = struct_block.child_blocks["title"]
        self.assertIsInstance(title_block, blocks.CharBlock)
        self.assertTrue(title_block.required)
        description_block = struct_block.child_blocks["description"]
        self.assertIsInstance(description_block, blocks.RichTextBlock)

    def test_streamblock_lookup(self):
        lookup = BlockDefinitionLookup(
            {
                0: ("wagtail.blocks.CharBlock", [], {"required": True}),
                1: ("wagtail.blocks.RichTextBlock", [], {}),
                2: (
                    "wagtail.blocks.StreamBlock",
                    [
                        [
                            ("heading", 0),
                            ("paragraph", 1),
                        ],
                    ],
                    {},
                ),
            }
        )
        stream_block = lookup.get_block(2)
        self.assertIsInstance(stream_block, blocks.StreamBlock)
        title_block = stream_block.child_blocks["heading"]
        self.assertIsInstance(title_block, blocks.CharBlock)
        self.assertTrue(title_block.required)
        description_block = stream_block.child_blocks["paragraph"]
        self.assertIsInstance(description_block, blocks.RichTextBlock)

    def test_listblock_lookup(self):
        lookup = BlockDefinitionLookup(
            {
                0: ("wagtail.blocks.CharBlock", [], {"required": True}),
                1: ("wagtail.blocks.ListBlock", [0], {}),
            }
        )
        list_block = lookup.get_block(1)
        self.assertIsInstance(list_block, blocks.ListBlock)
        list_item_block = list_block.child_block
        self.assertIsInstance(list_item_block, blocks.CharBlock)
        self.assertTrue(list_item_block.required)

        # Passing a class as the child block is still valid; this is not converted
        # to a reference
        lookup = BlockDefinitionLookup(
            {
                0: ("wagtail.blocks.ListBlock", [blocks.CharBlock], {}),
            }
        )
        list_block = lookup.get_block(0)
        self.assertIsInstance(list_block, blocks.ListBlock)
        list_item_block = list_block.child_block
        self.assertIsInstance(list_item_block, blocks.CharBlock)
