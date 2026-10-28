# your_project/adaptive_middleware.py

from django.conf import settings


class AdaptiveAssetsMiddleware:
    """
    Cache static assets to improve repeat visits on slow connections.
    Does not modify views, URLs, or HTML responses.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.static_url = settings.STATIC_URL

    def __call__(self, request):
        response = self.get_response(request)

        if (
            response.status_code == 200
            and request.method == "GET"
            and request.path.startswith(self.static_url)
        ):
            response["Cache-Control"] = "public, max-age=86400"

        return response