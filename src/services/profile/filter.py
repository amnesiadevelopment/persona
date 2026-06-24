from ...models.profile import Profile


def filter_profiles(profiles: list[Profile], query: str) -> list[Profile]:
    """Return profiles whose name, proxy, or OS contains the query (case-
    insensitive). An empty or whitespace-only query returns all profiles.
    """
    q = query.strip().lower()
    if not q:
        return profiles
    return [
        p
        for p in profiles
        if q in p.name.lower()
        or q in (p.proxy or "").lower()
        or q in p.os_type.lower()
        or any(q in tag.lower() for tag in p.tags)
    ]


def all_tags(profiles: list[Profile]) -> list[str]:
    """All distinct tags across profiles, sorted."""
    tags: set[str] = set()
    for p in profiles:
        tags.update(p.tags)
    return sorted(tags)


def filter_by_tag(profiles: list[Profile], tag: str) -> list[Profile]:
    """Profiles carrying the exact tag (case-insensitive). Empty tag = all."""
    if not tag:
        return profiles
    t = tag.lower()
    return [p for p in profiles if any(x.lower() == t for x in p.tags)]
