"""Onboarding step definitions and navigation, kept free of Flet so the flow
can be unit-tested. The UI component renders these steps.
"""

_FEATURES = [
    {
        "icon": "fingerprint",
        "label": "Per-profile fingerprint",
        "desc": "Each profile gets its own consistent browser fingerprint.",
    },
    {
        "icon": "lan",
        "label": "Proxy support (HTTP/SOCKS5)",
        "desc": "Bind any profile to its own proxy, with auth.",
    },
    {
        "icon": "public",
        "label": "Geo follows the proxy",
        "desc": "Timezone, locale and geolocation match the proxy's location.",
    },
    {
        "icon": "groups",
        "label": "Unlimited local profiles",
        "desc": "Create as many identities as you need, all stored locally.",
    },
    {
        "icon": "sell",
        "label": "Tags & groups",
        "desc": "Organize and bulk-manage profiles by tag.",
    },
    {
        "icon": "cookie",
        "label": "Cookie import & export",
        "desc": "Move sessions in and out per profile.",
    },
    {
        "icon": "smart_toy",
        "label": "Claude control (opt-in)",
        "desc": "Drive selected profiles from Claude over MCP — off by default.",
    },
    {
        "icon": "code",
        "label": "Open source",
        "desc": "No black-box binaries; the engine is OSS.",
    },
]


def steps() -> list[dict]:
    """Ordered onboarding steps. Welcome first, engine download last."""
    return [
        {
            "id": "welcome",
            "title": "Welcome to persona",
            "subtitle": "An open-source anti-detect browser for managing many "
            "identities at once.",
            "features": _FEATURES,
        },
        {
            "id": "engine",
            "title": "Setting things up",
            "subtitle": "Downloading the browser engine. This one-time setup "
            "runs in the background — you can skip ahead.",
        },
    ]


def next_index(i: int) -> int:
    return min(i + 1, len(steps()) - 1)


def prev_index(i: int) -> int:
    return max(i - 1, 0)


def is_last(i: int) -> bool:
    return i >= len(steps()) - 1
