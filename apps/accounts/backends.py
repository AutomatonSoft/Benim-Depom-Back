from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import User
from .phone import normalize_phone


class PhoneOrStaffEmailBackend(ModelBackend):
    """Sellers sign in by phone; staff retain their email login."""

    def authenticate(
        self, request, username=None, password=None, phone=None, email=None, **kwargs
    ):
        if password is None:
            return None
        if username and not phone and not email:
            if "@" in username:
                email = username
            else:
                phone = username
        user = None
        if phone and not email:
            try:
                user = User.objects.filter(phone=normalize_phone(phone)).first()
            except ValidationError:
                pass
        elif email and not phone:
            user = User.objects.filter(
                Q(role__in=[User.Role.MANAGER, User.Role.ADMIN])
                | Q(is_staff=True)
                | Q(is_superuser=True),
                email__iexact=email.strip(),
            ).first()
        if user is None:
            User().set_password(password)
        elif user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
