from http import HTTPStatus

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from sabil_book.posts.models import Post
from sabil_book.posts.models import PostImage
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
    assert list_response.data["results"][0]["id"] == post.pk
    assert detail_response.status_code == HTTPStatus.OK
    assert detail_response.data["author"] == post.author_id


@pytest.mark.django_db
def test_public_posts_are_newest_first_and_can_be_filtered_by_provider(api_client):
    oldest = PostFactory()
    newest = PostFactory(author=oldest.author)
    other = PostFactory()

    response = api_client.get(reverse("api:post-list"), {"provider": oldest.author_id})

    assert response.status_code == HTTPStatus.OK
    assert [post["id"] for post in response.data["results"]] == [newest.pk, oldest.pk]
    assert other.pk not in [post["id"] for post in response.data["results"]]


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


@pytest.mark.django_db
def test_non_author_gets_403_before_validation(api_client):
    post = PostFactory()
    api_client.force_authenticate(ProviderProfileFactory().user)

    response = api_client.patch(
        reverse("api:post-detail", args=[post.pk]),
        {"text": "x" * 2001},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["²", "abc", "0"])
def test_invalid_provider_filter_is_a_400(api_client, value):
    response = api_client.get(reverse("api:post-list"), {"provider": value})

    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.django_db
def test_edit_cannot_add_images(api_client):
    post = PostFactory()
    api_client.force_authenticate(post.author.user)

    response = api_client.patch(
        reverse("api:post-detail", args=[post.pk]),
        {"image_files": [uploaded_image()]},
        format="multipart",
    )

    assert response.status_code == HTTPStatus.OK
    assert post.images.count() == 0


@pytest.mark.django_db
def test_oversized_image_is_rejected(api_client, monkeypatch):
    monkeypatch.setattr("sabil_book.posts.serializers.MAX_IMAGE_BYTES", 10)
    api_client.force_authenticate(ProviderProfileFactory().user)

    response = api_client.post(
        reverse("api:post-list"),
        {"text": "Big", "image_files": [uploaded_image()]},
        format="multipart",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert not Post.objects.exists()


@pytest.mark.django_db
def test_failed_create_removes_files_already_saved(api_client, monkeypatch):
    api_client.force_authenticate(ProviderProfileFactory().user)
    real_create = PostImage.objects.create
    saved_names = []

    def create_then_fail(**kwargs):
        if saved_names:
            msg = "storage error"
            raise OSError(msg)
        obj = real_create(**kwargs)
        saved_names.append(obj.image.name)
        return obj

    monkeypatch.setattr(PostImage.objects, "create", create_then_fail)
    api_client.raise_request_exception = False

    response = api_client.post(
        reverse("api:post-list"),
        {
            "text": "Two",
            "image_files": [uploaded_image("a.gif"), uploaded_image("b.gif")],
        },
        format="multipart",
    )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert not Post.objects.exists()
    assert saved_names
    assert not default_storage.exists(saved_names[0])


@pytest.mark.django_db
def test_admin_inline_gives_new_images_distinct_positions(admin_client):
    post = PostFactory()
    prefix = "images"
    data = {
        "author": post.author.pk,
        "text": post.text,
        f"{prefix}-TOTAL_FORMS": "2",
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
        f"{prefix}-0-image": uploaded_image("a.gif"),
        f"{prefix}-1-image": uploaded_image("b.gif"),
    }

    response = admin_client.post(
        reverse("admin:posts_post_change", args=[post.pk]),
        data,
    )

    assert response.status_code == HTTPStatus.FOUND
    positions = list(
        PostImage.objects.filter(post=post).values_list("position", flat=True),
    )
    assert positions == [0, 1]
