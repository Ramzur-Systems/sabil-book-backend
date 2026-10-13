import factory
from factory.django import DjangoModelFactory

from sabil_book.posts.models import Post
from sabil_book.users.tests.factories import ProviderProfileFactory


class PostFactory(DjangoModelFactory[Post]):
    author = factory.SubFactory(ProviderProfileFactory)
    text = factory.Faker("paragraph")

    class Meta:
        model = Post
