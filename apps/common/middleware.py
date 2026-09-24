import sentry_sdk


class SentryAPIResponseLogMiddleware:
    """Send unsuccessful API responses to Sentry Logs without their bodies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        status_code = response.status_code

        if request.path_info.startswith("/api/") and status_code >= 400:
            route = getattr(request.resolver_match, "route", "<unmatched-api-route>")
            log = (
                sentry_sdk.logger.error
                if status_code >= 500
                else sentry_sdk.logger.warning
            )
            log(
                "API request returned an unsuccessful response",
                attributes={
                    "api_method": request.method,
                    "api_route": route,
                    "http_status_code": status_code,
                },
            )

        return response
