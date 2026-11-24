from django.db import transaction


def on_commit_robust(func, *args):
    """Run func(*args) after commit; log, never raise, if it fails.

    Do not pass functools.partial to on_commit(robust=True) directly: Django
    formats its error log with func.__qualname__, which partial objects lack,
    so a failing callback raises AttributeError instead of being logged.
    """

    def callback():
        func(*args)

    callback.__qualname__ = getattr(func, "__qualname__", repr(func))
    transaction.on_commit(callback, robust=True)
