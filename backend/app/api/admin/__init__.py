"""Admin sub-package.

Routers in this package are mounted under ``/api/admin/*`` and all
require ``Depends(get_current_admin)``. Adding a new admin router:
register it in ``app.main.create_app`` next to the existing ones —
there is no implicit auto-discovery.
"""
