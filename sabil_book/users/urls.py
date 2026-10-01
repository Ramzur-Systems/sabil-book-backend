from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import CurrentUserView
from .views import LoginView
from .views import LogoutView
from .views import RegisterView

app_name = "auth"
urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", TokenRefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", CurrentUserView.as_view(), name="me"),
]
