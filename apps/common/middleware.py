import logging

import sentry_sdk

logger = logging.getLogger(__name__)


class SentryAPIResponseLogMiddleware:
    """Send unsuccessful API responses to Sentry Logs without their bodies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        status_code = response.status_code

        if request.path_info.startswith("/api/") and status_code >= 400:
            route = getattr(request.resolver_match, "route", "<unmatched-api-route>")
            message = "API request returned an unsuccessful response"
            attributes = {
                "api_method": request.method,
                "api_route": route,
                "http_status_code": status_code,
            }
            sentry_logger = getattr(sentry_sdk, "logger", None)

            if sentry_logger is not None:
                log = (
                    sentry_logger.error if status_code >= 500 else sentry_logger.warning
                )
                log(message, attributes=attributes)
            else:
                logger.log(
                    logging.ERROR if status_code >= 500 else logging.WARNING,
                    "%s (api_method=%s, api_route=%s, http_status_code=%s)",
                    message,
                    attributes["api_method"],
                    attributes["api_route"],
                    status_code,
                )

        return response
