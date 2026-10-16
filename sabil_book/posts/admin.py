from django.contrib import admin
from django.db.models import Max
from django.forms import BaseInlineFormSet

from .models import Post
from .models import PostImage


class PostImageFormSet(BaseInlineFormSet):
    """Assign each new image the next free position instead of the default 0."""

    def save_new(self, form, commit=True):  # noqa: FBT002
        obj = super().save_new(form, commit=False)
        if not hasattr(self, "_next_position"):
            last = PostImage.objects.filter(post=self.instance).aggregate(
                last=Max("position"),
            )["last"]
            self._next_position = 0 if last is None else last + 1
        obj.position = self._next_position
        self._next_position += 1
        if commit:
            obj.save()
        return obj


class PostImageInline(admin.TabularInline):
    model = PostImage
    formset = PostImageFormSet
    extra = 0
    exclude = ["position"]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["id", "author", "created_at", "updated_at"]
    list_select_related = ["author", "author__user"]
    readonly_fields = ["created_at", "updated_at"]
    search_fields = ["text", "author__user__email", "author__user__name"]
    inlines = [PostImageInline]
