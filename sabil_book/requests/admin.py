from django.contrib import admin

from .models import Request


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "customer",
        "category",
        "visibility",
        "selected_provider",
        "direct_status",
        "budget",
        "status",
    ]
    list_filter = ["category", "visibility", "direct_status", "status"]
    readonly_fields = [
        "moderation_flags",
        "created_at",
        "updated_at",
        "published_at",
        "closed_at",
    ]
    search_fields = ["title", "description", "customer__email", "customer__name"]
