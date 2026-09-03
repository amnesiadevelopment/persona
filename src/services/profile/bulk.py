from ...core.strings import get_string
from ...utils.validation import validate_profile_name
from .coherence import IncoherentProfile


def paste_tokens(text: str) -> list[str]:
    """Every non-blank row of a paste, stripped, in the order pasted.

    THE ONE TOKENISATION, deliberately shared. ``parse_names`` and
    ``duplicate_names`` answer two halves of the same question — which names
    the batch will use, and which rows it silently dropped — so they have to
    agree about what a "row" IS. They used to re-implement the same
    split/strip/skip-blank loop side by side, which meant nothing held them
    together: a later edit to one (say, accepting semicolons as a separator)
    would have made ``duplicate_names`` report repeats against a different
    tokenisation than the one the batch actually ran, and the operator would
    have been told about a repeat that never happened. Both now derive from
    this, so they cannot drift.
    """
    return [part.strip() for part in text.replace(",", "\n").split("\n") if part.strip()]


def parse_names(text: str) -> list[str]:
    """The names the batch will attempt, de-duplicated, in first-seen order."""
    # dict.fromkeys is order-preserving de-duplication — the same result the
    # hand-rolled seen-set loop produced, now over the shared tokenisation.
    return list(dict.fromkeys(paste_tokens(text)))


def duplicate_names(text: str) -> list[str]:
    """The names a paste REPEATS, once each, in first-repeat order.

    ``parse_names`` drops a repeat silently — it is the right thing for the
    batch (creating "alpha" twice is one profile either way) but it means
    ``created + skipped`` can be fewer than the rows the operator pasted, with
    nothing said. This makes the difference nameable so the dialog and the
    Activity Log can account for it. Blank rows are deliberately NOT reported:
    a blank line is not a name the operator asked for, so counting it back at
    them is noise.

    NOTE the unit: this returns each repeated NAME once, not one entry per
    dropped row — "a, a, a" reports ``["a"]``, not ``["a", "a"]``. The count
    the operator needs is "which names did I list twice", not "how many
    keystrokes were wasted".
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for name in paste_tokens(text):
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    return dupes


def bulk_create(
    manager,
    names: list[str],
    proxy: str = "",
    os_type: str = "windows",
    search_engine: str = "duckduckgo",
    tags: list[str] | None = None,
) -> dict:
    """Create many profiles, refusing individually and reporting WHY.

    Returns ``{"created": [...], "skipped": [...], "reasons": {name: why}}``.

    ``reasons`` is ADDITIVE and ``skipped`` is unchanged: ``skipped`` stays a
    flat ``list[str]`` because it is PS-187's designed refusal channel and
    ``tests/test_ps187_os_type_write_doors.py`` asserts its shape directly.
    Reshaping it into tuples was measured RED and is out of scope; the reason
    rides alongside instead.

    Every name in ``skipped`` has an entry in ``reasons`` — the two are only
    ever written together, through :func:`_refuse`, so the invariant is
    structural rather than a rule the next editor has to remember.
    """
    created: list[str] = []
    skipped: list[str] = []
    reasons: dict[str, str] = {}
    seen: set[str] = set()

    def _refuse(name: str, why: str) -> None:
        skipped.append(name)
        reasons[name] = why

    for raw in names:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        valid, why = validate_profile_name(name)
        if not valid:
            # The reason validate_profile_name already computed — e.g. "Name
            # contains invalid characters: /". It used to be bound to `_` and
            # dropped one line later.
            _refuse(name, why)
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
        except IncoherentProfile as e:
            # The full refusal sentence, not the fact that one happened. This
            # is the case that matters most: the whole batch shares one
            # os_type, so a storage refusal skips EVERY name — and as an
            # integer that is "skipped 50" with no cause anywhere.
            _refuse(name, str(e))
            continue
        if accepted:
            created.append(name)
        else:
            # add_profile returns a bare bool, but the cause is unambiguous
            # here: the only falsy return is `name in self.profiles`
            # (manager.py). Routed through core/strings like the delete lane
            # rather than hard-coded in the service.
            #
            # `bulk_create_exists`, NOT `profile_exists`: this reason reaches
            # the Activity Log, and log_console.severity() matches "ready"
            # inside "already" — "Profile already exists!" would paint a GREEN
            # SUCCESS dot on a refusal. See core/strings.py.
            _refuse(name, get_string("bulk_create_exists"))
    return {"created": created, "skipped": skipped, "reasons": reasons}
