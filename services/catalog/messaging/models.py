from django.db import models


class OutboxMessage(models.Model):
    """A message waiting to be published to RabbitMQ (transactional outbox).

    It is written in the same database transaction as the change it describes,
    so "the change happened" and "the message exists" can never disagree.
    The relay (`manage.py relay_outbox`) publishes it later and sets published_at.
    """

    routing_key = models.CharField(max_length=100)
    payload = models.JSONField()
    # W3C trace context ({"traceparent": ...}) of the change that wrote the
    # message, so the relay and the consumer continue that same trace later.
    trace_context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            # The relay only ever looks for unpublished messages.
            models.Index(
                fields=["id"],
                name="outbox_pending_idx",
                condition=models.Q(published_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        state = "published" if self.published_at else "pending"
        return f"#{self.pk} {self.routing_key} ({state})"
