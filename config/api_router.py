from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from sabil_book.requests.views import BrowseRequestViewSet
from sabil_book.requests.views import ModerationRequestViewSet
from sabil_book.requests.views import RequestViewSet
from sabil_book.users.views import ProviderProfileViewSet
from sabil_book.users.views import UserViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet)
router.register("providers", ProviderProfileViewSet, basename="provider")
router.register("requests", RequestViewSet, basename="request")
router.register("browse/requests", BrowseRequestViewSet, basename="browse")
router.register(
    "moderation/requests",
    ModerationRequestViewSet,
    basename="request-moderation",
)


app_name = "api"
urlpatterns = router.urls
