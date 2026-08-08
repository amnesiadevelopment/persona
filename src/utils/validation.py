import re

_INVALID_CHARS = '<>:"/\\|?*'
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_PROXY_PATTERN = re.compile(
    # Kept in sync with the launch parser (proxy_parser): allow underscores in
    # hostnames (real provider gateways like gate_us.smartproxy.com use them) and
    # the socks5h scheme, so a proxy that connects at runtime isn't rejected on
    # save (a paste-then-save that contradicts itself).
    r"^(?:(?P<scheme>https?|socks[45]h?)://)?"
    r"(?:(?P<user>[^:@]+):(?P<pass>[^@]+)@)?"
    r"(?P<host>[a-zA-Z0-9._-]+|\d{1,3}(?:\.\d{1,3}){3})"
    r":(?P<port>\d{1,5})$",
)


def validate_profile_name(name: str) -> tuple[bool, str]:
    if not name:
        return False, "Profile name cannot be empty"

    if len(name) > 64:
        return False, "Profile name must be 64 characters or less"

    found_invalid = [c for c in name if c in _INVALID_CHARS]
    if found_invalid:
        return False, f"Name contains invalid characters: {', '.join(found_invalid)}"

    # Control characters (0x00-0x1F) are illegal in Windows filenames and would
    # produce a broken data-dir path.
    if any(ord(c) < 0x20 for c in name):
        return False, "Name cannot contain control characters"

    if name != name.strip():
        return False, "Name cannot start or end with spaces"

    # Windows silently strips a trailing dot/space from a filename, so a "name."
    # profile would live in a "name" dir and never be found again.
    if name.endswith((".", " ")):
        return False, "Name cannot end with a dot or space"

    # A reserved device name is reserved even with an extension (CON.txt, NUL.log
    # all map to the device), so test the base name before the first dot.
    base = name.split(".", 1)[0]
    if base.upper() in _RESERVED_NAMES:
        return False, f"'{name}' uses a reserved system name"

    return True, ""


def validate_proxy_format(proxy_str: str) -> tuple[bool, str]:
    if not proxy_str:
        return True, ""

    match = _PROXY_PATTERN.match(proxy_str)
    if not match:
        return False, "Invalid proxy format. Use: [scheme://][user:pass@]host:port"

    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        return False, f"Port must be between 1 and 65535, got {port}"

    return True, ""
