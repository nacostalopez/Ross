"""Every route's guards, checked from the app itself rather than route by route.

A new route that forgets get_owned_store would let one account read or change
another account's store; one that forgets a role check would let a viewer
write; one left without authentication would be open to anyone. These tests
walk the registered routes and fail on any of the three, so the mistake is
caught when the route is added.
"""

from fastapi.routing import APIRoute

from app.main import app

# Routes anyone can call, each with its own reason: signing in, the public
# landing, and provider callbacks that check a signature or re-fetch the data.
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/billing/plans"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/demo"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("POST", "/accounts/invites/accept"),
    ("POST", "/demo-requests"),
    ("POST", "/billing/webhooks/mercadopago"),
    ("POST", "/connectors/mercadolibre/notifications"),
    ("POST", "/connectors/mercadopago/notifications"),
    ("POST", "/connectors/shopify/webhook/{store_id}"),
    ("POST", "/connectors/tiendanube/webhook/{store_id}"),
}

# Writes that only touch the signed-in user's own things, so any role may make them.
PERSONAL_WRITES = {
    ("POST", "/auth/logout"),
    ("PUT", "/dashboard/layout"),
    ("PUT", "/notification-preferences"),
    ("POST", "/push/subscribe"),
    ("DELETE", "/push/subscribe"),
    ("DELETE", "/push/subscriptions/{subscription_id}"),
}


def _routes():
    found = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            found += [r for r in route.original_router.routes if isinstance(r, APIRoute)]
    return found


def _dependency_names(dependant, names=None):
    names = set() if names is None else names
    for sub in dependant.dependencies:
        names.add(getattr(sub.call, "__qualname__", repr(sub.call)))
        _dependency_names(sub, names)
    return names


def _takes_store_id(route):
    return any(param.name == "store_id" for param in route.dependant.path_params + route.dependant.query_params)


def _endpoints():
    for route in _routes():
        names = _dependency_names(route.dependant)
        for method in sorted(route.methods - {"HEAD"}):
            yield method, route, names


def test_the_walk_finds_the_whole_api():
    # If routers stop being discoverable this way, the checks below would pass by checking nothing.
    assert len(list(_endpoints())) > 90


def test_only_the_known_public_routes_skip_authentication():
    unexpected = {
        (method, route.path)
        for method, route, names in _endpoints()
        if not any("get_current_user" in name for name in names)
    } - PUBLIC
    assert not unexpected, f"Routes without authentication: {sorted(unexpected)}"


def test_every_signed_in_route_on_a_store_checks_it_belongs_to_the_account():
    missing = sorted(
        (method, route.path)
        for method, route, names in _endpoints()
        if (method, route.path) not in PUBLIC
        and _takes_store_id(route)
        and not any("get_owned_store" in name for name in names)
    )
    assert not missing, f"Routes that take a store_id without get_owned_store: {missing}"


def test_every_write_checks_the_role():
    missing = sorted(
        (method, route.path)
        for method, route, names in _endpoints()
        if method != "GET"
        and (method, route.path) not in PUBLIC | PERSONAL_WRITES
        and not any("require_role" in name or "require_store_role" in name for name in names)
    )
    assert not missing, f"Writes without a role check: {missing}"
