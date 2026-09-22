from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

<<<<<<< HEAD
from sabil_book.users.views import ProviderProfileViewSet
from sabil_book.users.views import UserViewSet
=======
from sabil_book.requests.views import BrowseRequestViewSet
from sabil_book.requests.views import ModerationRequestViewSet
from sabil_book.requests.views import RequestViewSet
from sabil_book.users.api.views import UserViewSet
>>>>>>> a47a5cac16a7c71854040dee35b281bf044d57db

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet)
<<<<<<< HEAD
router.register("providers", ProviderProfileViewSet, basename="provider")
=======
router.register("requests", RequestViewSet, basename="request")
router.register("browse/requests", BrowseRequestViewSet, basename="browse")
router.register(
    "moderation/requests",
    ModerationRequestViewSet,
    basename="request-moderation",
)
>>>>>>> a47a5cac16a7c71854040dee35b281bf044d57db


app_name = "api"
urlpatterns = router.urls
