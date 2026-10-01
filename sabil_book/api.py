from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import status
from rest_framework.response import Response

if TYPE_CHECKING:
    from .exceptions import InvalidTransitionError


def transition_error(exc: InvalidTransitionError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
