def humanize_since(then: float, now: float) -> str:
    """Human-readable 'time since' label, e.g. '5m ago', '23h ago'.

    Returns 'never' for a zero/missing timestamp.
    """
    if not then:
        return "never"
    delta = int(now - then)
    if delta < 0:
        delta = 0
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"
