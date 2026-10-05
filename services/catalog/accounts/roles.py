from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

ORGANIZER = "organizer"
BUYER = "buyer"


def is_organizer(user: AbstractBaseUser | AnonymousUser) -> bool:
    return user.is_authenticated and user.groups.filter(name=ORGANIZER).exists()


def get_roles(user: AbstractBaseUser) -> list[str]:
    # Every registered user can buy; organizers are granted the extra role by an admin.
    roles = [BUYER]
    if is_organizer(user):
        roles.append(ORGANIZER)
    return roles
