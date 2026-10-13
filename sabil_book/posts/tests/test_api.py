from http import HTTPStatus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from sabil_book.posts.models import Post
from sabil_book.posts.tests.factories import PostFactory
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory

GALLERY_IMAGE_COUNT = 2


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def uploaded_image(name: str = "post.gif") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        (
            b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9"
            b"\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02"
            b"\x02L\x01\x00;"
        ),
        content_type="image/gif",
    )


@pytest.mark.django_db
def test_anonymous_user_can_list_and_retrieve_posts(api_client):
    post = PostFactory()

    list_response = api_client.get(reverse("api:post-list"))
    detail_response = api_client.get(reverse("api:post-detail", args=[post.pk]))

    assert list_response.status_code == HTTPStatus.OK
    assert list_response.data[0]["id"] == post.pk
    assert detail_response.status_code == HTTPStatus.OK
    assert detail_response.data["author"] == post.author_id


@pytest.mark.django_db
def test_public_posts_are_newest_first_and_can_be_filtered_by_provider(api_client):
    oldest = PostFactory()
    newest = PostFactory(author=oldest.author)
    other = PostFactory()

    response = api_client.get(reverse("api:post-list"), {"provider": oldest.author_id})

    assert response.status_code == HTTPStatus.OK
    assert [post["id"] for post in response.data] == [newest.pk, oldest.pk]
    assert other.pk not in [post["id"] for post in response.data]


@pytest.mark.django_db
def test_provider_can_create_a_post_with_an_optional_ordered_gallery(api_client):
    provider = ProviderProfileFactory()
    api_client.force_authenticate(provider.user)

    response = api_client.post(
        reverse("api:post-list"),
        {
            "text": "A post with a gallery",
            "image_files": [uploaded_image("one.gif"), uploaded_image("two.gif")],
        },
        format="multipart",
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.data["author"] == provider.pk
    assert response.data["text"] == "A post with a gallery"
    assert [image["position"] for image in response.data["images"]] == [0, 1]
    gallery_count = Post.objects.get(pk=response.data["id"]).images.count()
    assert gallery_count == GALLERY_IMAGE_COUNT


@pytest.mark.django_db
def test_post_text_is_required_and_limited_to_2000_characters(api_client):
    provider = ProviderProfileFactory()
    api_client.force_authenticate(provider.user)

    blank = api_client.post(reverse("api:post-list"), {"text": "   "})
    too_long = api_client.post(reverse("api:post-list"), {"text": "x" * 2001})

    assert blank.status_code == HTTPStatus.BAD_REQUEST
    assert "text" in blank.data
    assert too_long.status_code == HTTPStatus.BAD_REQUEST
    assert "text" in too_long.data


@pytest.mark.django_db
def test_non_provider_cannot_create_posts(api_client):
    api_client.force_authenticate(UserFactory())

    response = api_client.post(reverse("api:post-list"), {"text": "No profile"})

    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.django_db
def test_only_author_can_edit_post_text_or_delete_post(api_client):
    post = PostFactory()
    other_provider = ProviderProfileFactory()
    api_client.force_authenticate(other_provider.user)
    url = reverse("api:post-detail", args=[post.pk])

    attempted_edit = api_client.patch(url, {"text": "Attempted edit"})
    assert attempted_edit.status_code == HTTPStatus.FORBIDDEN
    assert api_client.delete(url).status_code == HTTPStatus.FORBIDDEN

    api_client.force_authenticate(post.author.user)
    update = api_client.patch(url, {"text": "Updated text"})
    delete = api_client.delete(url)

    assert update.status_code == HTTPStatus.OK
    assert update.data["text"] == "Updated text"
    assert delete.status_code == HTTPStatus.NO_CONTENT
    assert not Post.objects.filter(pk=post.pk).exists()
