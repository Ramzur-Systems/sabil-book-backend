from django import forms
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from sabil_book.admin_utils import badge

from .models import Request
from .services import InvalidTransitionError
from .services import moderate_request


class RejectionForm(forms.Form):
    reason = forms.CharField(
        label=_("Rejection reason"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Shown to the customer."),
    )


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "title",
        "customer",
        "category",
        "budget",
        "status_badge",
        "moderation_flags",
        "created_at",
    ]
    list_filter = ["status", "category", "created_at"]
    list_select_related = ["customer"]
    search_fields = ["title", "description", "customer__email", "customer__name"]
    date_hierarchy = "created_at"
    actions = ["approve_requests", "reject_requests"]

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj: Request) -> str:
        colors = {
            Request.RequestStatus.MANUAL_REVIEW: "amber",
            Request.RequestStatus.PUBLISHED: "green",
            Request.RequestStatus.REJECTED: "red",
            Request.RequestStatus.CLOSED: "gray",
            Request.RequestStatus.DRAFT: "blue",
        }
        return badge(obj.get_status_display(), colors.get(obj.status, "gray"))

    def has_add_permission(self, request) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        # Status changes must go through the moderation services, not the form.
        return [field.name for field in self.model._meta.fields]  # noqa: SLF001

    def _moderate(self, request, queryset, *, approve: bool, reason: str = "") -> None:
        done = 0
        for pk in queryset.values_list("pk", flat=True):
            try:
                moderate_request(pk, approve=approve, reason=reason)
            except InvalidTransitionError:
                self.message_user(
                    request,
                    _("Request #%(pk)s is not awaiting manual review; skipped.")
                    % {"pk": pk},
                    messages.WARNING,
                )
            except ObjectDoesNotExist:
                self.message_user(
                    request,
                    _("Request #%(pk)s no longer exists; skipped.") % {"pk": pk},
                    messages.WARNING,
                )
            else:
                done += 1
        if done:
            if approve:
                message = ngettext(
                    "%(count)d request approved.",
                    "%(count)d requests approved.",
                    done,
                )
            else:
                message = ngettext(
                    "%(count)d request rejected.",
                    "%(count)d requests rejected.",
                    done,
                )
            self.message_user(request, message % {"count": done}, messages.SUCCESS)

    @admin.action(
        description=_("Approve selected requests (publish)"),
        permissions=["change"],
    )
    def approve_requests(self, request, queryset):
        self._moderate(request, queryset, approve=True)

    @admin.action(
        description=_("Reject selected requests"),
        permissions=["change"],
    )
    def reject_requests(self, request, queryset):
        if "apply" in request.POST:
            form = RejectionForm(request.POST)
            if form.is_valid():
                self._moderate(
                    request,
                    queryset,
                    approve=False,
                    reason=form.cleaned_data["reason"],
                )
                return None
        else:
            form = RejectionForm()
        context = {
            **self.admin_site.each_context(request),
            "title": _("Reject requests"),
            "opts": self.model._meta,  # noqa: SLF001
            "queryset": queryset,
            "form": form,
            "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
        }
        return TemplateResponse(
            request,
            "admin/requests/reject_requests.html",
            context,
        )
