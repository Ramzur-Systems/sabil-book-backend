from django.db import transaction
from rest_framework import serializers

from .models import Post
from .models import PostImage

MAX_POST_IMAGES = 10


class PostImageSerializer(serializers.ModelSerializer[PostImage]):
    class Meta:
        model = PostImage
        fields = ["id", "image", "position"]
        read_only_fields = fields


class PostSerializer(serializers.ModelSerializer[Post]):
    images = PostImageSerializer(many=True, read_only=True)
    image_files = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
        max_length=MAX_POST_IMAGES,
    )

    class Meta:
        model = Post
        fields = [
            "id",
            "author",
            "text",
            "images",
            "image_files",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "author", "images", "created_at", "updated_at"]

    def create(self, validated_data: dict) -> Post:
        image_files = validated_data.pop("image_files", [])
        with transaction.atomic():
            post = Post.objects.create(**validated_data)
            for position, image in enumerate(image_files):
                PostImage.objects.create(post=post, image=image, position=position)
        return post

    def validate(self, attrs: dict) -> dict:
        if self.instance is not None and "image_files" in attrs:
            msg = "Images can only be added when creating a post."
            raise serializers.ValidationError({"image_files": msg})
        return attrs
