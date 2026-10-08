from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.db import connection
from django.http import HttpResponse
from django.urls import include, path

def health(request):
    """For container and deploy checks: 200 when the app can reach its database, 503 otherwise."""
    try:
        with connection.cursor() as cursor: cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("database unavailable\n", status=503, content_type="text/plain")
    return HttpResponse("ok\n", content_type="text/plain")

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("tracker.urls")),
]
