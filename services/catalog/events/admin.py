from django.contrib import admin

from .models import Event, EventZone, Venue, Zone


class ZoneInline(admin.TabularInline):
    model = Zone
    extra = 1


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "address"]
    search_fields = ["name", "city"]
    list_filter = ["city"]
    inlines = [ZoneInline]


class EventZoneInline(admin.TabularInline):
    model = EventZone
    extra = 1

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Zone.__str__ shows the venue name: load it in the same query (avoid N+1).
        if db_field.name == "zone":
            kwargs["queryset"] = Zone.objects.select_related("venue")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "venue", "starts_at", "status", "organizer"]
    list_filter = ["status", "venue__city"]
    search_fields = ["title", "venue__name"]
    list_select_related = ["venue", "organizer"]
    date_hierarchy = "starts_at"
    inlines = [EventZoneInline]

    def has_delete_permission(self, request, obj=None):
        # Only drafts can be deleted; published events may have sold tickets.
        if obj is not None and obj.status != Event.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request, action_location=admin.ActionLocation.CHANGE_LIST):
        # The bulk "delete selected" action skips the per-object check above.
        actions = super().get_actions(request, action_location=action_location)
        actions.pop("delete_selected", None)
        return actions
