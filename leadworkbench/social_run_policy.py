"""Identity of a social operation, independent of storage and display labels."""
import json

from .social_connections import DEFAULTS


def execution_signature(settings):
    """Legacy snapshots use the same defaults as newly submitted operations."""
    if settings.get("profile_only"):
        operation = "profile"
    elif settings.get("platform_search"):
        operation = "platform_search"
    elif settings.get("read_only"):
        operation = "read"
    else:
        operation = "discover"
    return json.dumps(
        {
            "operation": operation,
            "searxng_url": settings.get("searxng_url", ""),
            "connections": {**DEFAULTS, **settings.get("connections", {})},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
