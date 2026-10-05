from rest_framework.permissions import BasePermission

from .roles import is_organizer


class IsOrganizer(BasePermission):
    message = "Only organizers can manage events."

    def has_permission(self, request, view) -> bool:
        return is_organizer(request.user)
