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
        or q in (p.notes or "").lower()
        or any(q in tag.lower() for tag in p.tags)
    ]


def all_tags(profiles: list[Profile]) -> list[str]:
    """All distinct tags across profiles, sorted, de-duplicated case-
    insensitively so "Work" and "work" are one tag — the chip cloud counted
    them separately while filtering matched both (audit5 LOW). Keeps the first
    spelling seen (in sorted order) as the canonical display form."""
    canon: dict[str, str] = {}
    for p in profiles:
        for tag in p.tags:
            canon.setdefault(tag.lower(), tag)
    return [canon[k] for k in sorted(canon)]


def filter_by_tag(profiles: list[Profile], tag: str) -> list[Profile]:
    """Profiles carrying the exact tag (case-insensitive). Empty tag = all."""
    if not tag:
        return profiles
    t = tag.lower()
    return [p for p in profiles if any(x.lower() == t for x in p.tags)]
