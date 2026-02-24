"""Lightweight middleware to block runaway polling from stale browser tabs."""

from django.http import HttpResponse

# Stale run IDs whose browser tabs are spamming 404s.
# Add entries here; remove when the tabs are finally closed.
BLOCKED_RUN_IDS = {
    "20260224_061414_Etoposide___Cisplatin_100pt_126d",
}


class BlockStalePollingMiddleware:
    """Return empty 204 immediately for known-dead run polling requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith("/api/day/") or path.startswith("/api/sim/"):
            for run_id in BLOCKED_RUN_IDS:
                if run_id in path:
                    return HttpResponse(status=204)
        return self.get_response(request)
