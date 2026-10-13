from importlib import import_module

from django.apps import AppConfig


class PostsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sabil_book.posts"

    def ready(self) -> None:
        import_module("sabil_book.posts.signals")
