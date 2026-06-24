from ...utils.validation import validate_profile_name


def parse_names(text: str) -> list[str]:
    raw = text.replace(",", "\n").split("\n")
    seen: set[str] = set()
    names: list[str] = []
    for part in raw:
        name = part.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def bulk_create(
    manager,
    names: list[str],
    proxy: str = "",
    os_type: str = "windows",
    search_engine: str = "duckduckgo",
    tags: list[str] | None = None,
) -> dict:
    created: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        valid, _ = validate_profile_name(name)
        if not valid:
            skipped.append(name)
            continue
        if manager.add_profile(
            name, proxy, os_type, search_engine=search_engine, tags=tags
        ):
            created.append(name)
        else:
            skipped.append(name)
    return {"created": created, "skipped": skipped}
