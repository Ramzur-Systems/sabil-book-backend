from django.db import transaction
from rest_framework import serializers

from .models import Post
from .models import PostImage

MAX_POST_IMAGES = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def validate_image_size(image) -> None:
    if image.size > MAX_IMAGE_BYTES:
        msg = f"Each image must be at most {MAX_IMAGE_BYTES // (1024 * 1024)} MB."
        raise serializers.ValidationError(msg)


class PostImageSerializer(serializers.ModelSerializer[PostImage]):
    class Meta:
        model = PostImage
        fields = ["id", "image", "position"]
        read_only_fields = fields


class PostUpdateSerializer(serializers.ModelSerializer[Post]):
    """Editing a post only changes its text; images are fixed at creation."""

    images = PostImageSerializer(many=True, read_only=True)

    class Meta:
        model = Post
        fields = ["id", "author", "text", "images", "created_at", "updated_at"]
        read_only_fields = ["id", "author", "images", "created_at", "updated_at"]


class PostSerializer(PostUpdateSerializer):
    image_files = serializers.ListField(
        child=serializers.ImageField(validators=[validate_image_size]),
        write_only=True,
        required=False,
        max_length=MAX_POST_IMAGES,
    )

    class Meta(PostUpdateSerializer.Meta):
        fields = [*PostUpdateSerializer.Meta.fields, "image_files"]

    def create(self, validated_data: dict) -> Post:
        image_files = validated_data.pop("image_files", [])
        saved: list[PostImage] = []
        try:
            with transaction.atomic():
                post = Post.objects.create(**validated_data)
                for position, image in enumerate(image_files):
                    saved.append(
                        PostImage.objects.create(
                            post=post,
                            image=image,
                            position=position,
                        ),
                    )
        except Exception:
            # The rollback drops the rows but not the files already in storage.
            for post_image in saved:
                post_image.image.delete(save=False)
            raise
        return post
