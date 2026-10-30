from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import PostImage


@receiver(post_delete, sender=PostImage)
def delete_post_image_file(sender, instance: PostImage, **kwargs) -> None:
    if instance.image:
        # Only drop the file once the row deletion is durable.
        transaction.on_commit(lambda: instance.image.delete(save=False))
