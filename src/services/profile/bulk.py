from ...utils.validation import validate_profile_name
from .coherence import IncoherentProfile


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
        # A coherence rule refuses by RAISING, and this function's contract is
        # to refuse individually and keep going: `skipped` is its designed
        # refusal channel, already used for a bad name and for add_profile
        # returning falsy. Letting the exception propagate would abort the batch
        # MID-LOOP — names earlier in the list are created and persisted while
        # the return value never arrives, so the caller (ui/actions/profile.py
        # on_create, whose `str | None` return is ITS error channel) gets
        # neither the result dict nor a message. The whole batch shares one
        # os_type, so a storage refusal (PS-187) skips every name rather than
        # some; that is correct, and it is reported instead of thrown.
        try:
            accepted = manager.add_profile(
                name, proxy, os_type, search_engine=search_engine, tags=tags
            )
        except IncoherentProfile:
            skipped.append(name)
            continue
        if accepted:
            created.append(name)
        else:
            skipped.append(name)
    return {"created": created, "skipped": skipped}
