from rest_framework.pagination import CursorPagination


class MessageCursorPagination(CursorPagination):
    # Cursor (not offset) pagination: new messages keep arriving while a
    # client pages through history, which would make page numbers drift.
    page_size = 30
    ordering = "date_time"
