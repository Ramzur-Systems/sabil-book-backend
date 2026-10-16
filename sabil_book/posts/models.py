from django.db import models
from django.utils.translation import gettext_lazy as _

from sabil_book.users.models import ProviderProfile


class Post(models.Model):
    author = models.ForeignKey(
        ProviderProfile,
        on_delete=models.CASCADE,
        related_name="posts",
        verbose_name=_("author"),
    )
    text = models.TextField(_("text"), max_length=2000)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["author", "-created_at"])]

    def __str__(self) -> str:
        return f"Post #{self.pk} by {self.author_id}"


class PostImage(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name=_("post"),
    )
    image = models.ImageField(_("image"), upload_to="posts/%Y/%m/%d/")
    position = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "position"],
                name="posts_unique_image_position",
            ),
        ]

    def __str__(self) -> str:
        return f"Image {self.position} for post #{self.post_id}"
