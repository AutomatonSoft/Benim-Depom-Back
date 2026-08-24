import re

from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import SimpleRateThrottle

from apps.common.permissions import is_manager


class IPRateThrottle(SimpleRateThrottle):
    """Rate limit for unauthenticated requests by client IP."""

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)

        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }


class UserRateThrottle(SimpleRateThrottle):
    """Rate limit for authenticated requests by user ID."""

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None

        return self.cache_format % {
            "scope": self.scope,
            "ident": str(request.user.pk),
        }


class RegistrationRateThrottle(IPRateThrottle):
    scope = "registration"


class EmailVerificationRateThrottle(IPRateThrottle):
    scope = "email_verification"


class EmailVerificationResendRateThrottle(IPRateThrottle):
    scope = "email_verification_resend"


class LoginRateThrottle(IPRateThrottle):
    scope = "login"

    def parse_rate(self, rate):
        """Support DRF rates and explicit short windows such as ``10/15m``."""
        if rate is None:
            return None, None

        num_requests, period = rate.split("/", maxsplit=1)
        match = re.fullmatch(r"(?P<count>\d+)(?P<unit>[smhd])", period)
        if match:
            seconds_per_unit = {
                "s": 1,
                "m": 60,
                "h": 60 * 60,
                "d": 24 * 60 * 60,
            }
            return (
                int(num_requests),
                int(match.group("count")) * seconds_per_unit[match.group("unit")],
            )

        return super().parse_rate(rate)


class AiGenerationRateThrottle(UserRateThrottle):
    scope = "ai_generation"


class ImageUploadRateThrottle(UserRateThrottle):
    scope = "image_upload"


class ManagerMutationRateThrottle(UserRateThrottle):
    """
    Limits only write actions of manager/admin users.
    GET/HEAD/OPTIONS and seller actions are not affected.
    """

    scope = "manager_mutation"

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        if not is_manager(request.user):
            return True

        return super().allow_request(request, view)


class ManagerMutationThrottleMixin:
    """Applies manager mutation rate limiting to write HTTP methods only."""

    manager_mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}

    def get_throttles(self):
        throttles = super().get_throttles()

        already_configured = any(
            isinstance(throttle, ManagerMutationRateThrottle) for throttle in throttles
        )

        if (
            self.request.method in self.manager_mutation_methods
            and not already_configured
        ):
            throttles.append(ManagerMutationRateThrottle())

        return throttles
