from django.contrib.auth.models import AbstractUser, AnonymousUser

ORGANIZER = "organizer"
BUYER = "buyer"


def is_organizer(user: AbstractUser | AnonymousUser) -> bool:
    return user.is_authenticated and user.groups.filter(name=ORGANIZER).exists()


def get_roles(user: AbstractUser) -> list[str]:
    # Every registered user can buy; organizers are granted the extra role by an admin.
    roles = [BUYER]
    if is_organizer(user):
        roles.append(ORGANIZER)
    return roles
