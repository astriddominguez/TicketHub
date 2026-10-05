"""Keep other services informed: any change that affects sales writes a snapshot.

Signals fire for every way of saving (API, admin, shell), and run inside the same
transaction as the change (ATOMIC_REQUESTS / the admin's own transaction).
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .messages import enqueue_event_snapshot
from .models import Event, EventZone


@receiver(post_save, sender=Event)
def event_saved(sender, instance: Event, **kwargs) -> None:
    enqueue_event_snapshot(instance.pk)


@receiver(post_save, sender=EventZone)
@receiver(post_delete, sender=EventZone)
def event_zone_changed(sender, instance: EventZone, **kwargs) -> None:
    enqueue_event_snapshot(instance.event_id)
