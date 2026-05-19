import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from wagtail.api.v3.models import ApiToken
from wagtail.models import Page
from wagtail.test.testapp.models import SimplePage


def _payload(response):
    return json.loads(response.content.decode("utf-8"))


@override_settings(WAGTAILAPI_LIMIT_MAX=20)
class V3PagesReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = Page.objects.get(pk=2)
        cls.live = cls.root.add_child(
            instance=SimplePage(title="Hello", slug="hello", content="hi")
        )
        cls.live.save_revision().publish()

    def test_list_anonymous(self):
        response = self.client.get("/api/v3/pages/")
        self.assertEqual(response.status_code, 200)
        body = _payload(response)
        self.assertIn("meta", body)
        self.assertIn("total_count", body["meta"])
        self.assertGreaterEqual(body["meta"]["total_count"], 1)
        # Listing default fields should at least include id and a meta block.
        first = body["items"][0]
        self.assertIn("id", first)
        self.assertIn("meta", first)
        self.assertIn("type", first["meta"])

    def test_detail_anonymous(self):
        response = self.client.get(f"/api/v3/pages/{self.live.pk}/")
        self.assertEqual(response.status_code, 200)
        body = _payload(response)
        self.assertEqual(body["id"], self.live.pk)
        self.assertEqual(body["title"], "Hello")

    def test_type_filter(self):
        response = self.client.get("/api/v3/pages/?type=tests.SimplePage")
        self.assertEqual(response.status_code, 200)
        body = _payload(response)
        for item in body["items"]:
            self.assertEqual(item["meta"]["type"], "tests.SimplePage")

    def test_fields_underscore(self):
        response = self.client.get(f"/api/v3/pages/{self.live.pk}/?fields=_,title")
        self.assertEqual(response.status_code, 200)
        body = _payload(response)
        # Only the title field should remain at top level.
        self.assertEqual(set(body.keys()), {"title"})


class V3PagesWriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = Page.objects.get(pk=2)
        User = get_user_model()
        cls.user = User.objects.create_superuser(
            username="apitester", email="api@example.com", password="x"
        )
        cls.token = ApiToken.objects.create(user=cls.user, label="test")

    def _auth_headers(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token.key}"}

    def test_anonymous_create_is_rejected(self):
        response = self.client.post(
            "/api/v3/pages/",
            data=json.dumps({"type": "tests.SimplePage", "parent": self.root.pk, "title": "x"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (401, 403))

    def test_create_with_token(self):
        response = self.client.post(
            "/api/v3/pages/",
            data=json.dumps(
                {
                    "type": "tests.SimplePage",
                    "parent": self.root.pk,
                    "title": "Hello via API",
                    "slug": "hello-via-api",
                    "content": "Created over v3",
                }
            ),
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = _payload(response)
        new_id = body["id"]
        self.assertTrue(Page.objects.filter(pk=new_id).exists())

    def test_patch_publish_delete(self):
        # Create a draft.
        create = self.client.post(
            "/api/v3/pages/",
            data=json.dumps(
                {
                    "type": "tests.SimplePage",
                    "parent": self.root.pk,
                    "title": "Draft",
                    "slug": "draft",
                    "content": "v1",
                }
            ),
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(create.status_code, 201, create.content)
        page_id = _payload(create)["id"]

        # Patch.
        patch = self.client.patch(
            f"/api/v3/pages/{page_id}/",
            data=json.dumps({"title": "Patched"}),
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(patch.status_code, 200, patch.content)
        self.assertEqual(_payload(patch)["title"], "Patched")

        # Publish.
        publish = self.client.post(
            f"/api/v3/pages/{page_id}/publish/",
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(publish.status_code, 200, publish.content)
        self.assertTrue(Page.objects.get(pk=page_id).live)

        # Delete.
        delete = self.client.delete(
            f"/api/v3/pages/{page_id}/", **self._auth_headers()
        )
        self.assertEqual(delete.status_code, 204)
        self.assertFalse(Page.objects.filter(pk=page_id).exists())
