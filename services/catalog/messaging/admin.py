from django.contrib import admin

from .models import OutboxMessage


@admin.register(OutboxMessage)
class OutboxMessageAdmin(admin.ModelAdmin):
    """Read-only window into the outbox: useful to see what was (or wasn't) sent."""

    list_display = ["id", "routing_key", "created_at", "published_at"]
    list_filter = ["routing_key", ("published_at", admin.EmptyFieldListFilter)]
    readonly_fields = ["routing_key", "payload", "created_at", "published_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
