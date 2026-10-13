from django.contrib import admin

from .models import Post
from .models import PostImage


class PostImageInline(admin.TabularInline):
    model = PostImage
    extra = 0


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["id", "author", "created_at", "updated_at"]
    list_select_related = ["author", "author__user"]
    readonly_fields = ["created_at", "updated_at"]
    search_fields = ["text", "author__user__email", "author__user__name"]
    inlines = [PostImageInline]
