from django.utils.html import format_html


def badge(label: str, color: str = "gray") -> str:
    return format_html('<span class="badge badge-{}">{}</span>', color, label)
