#!/usr/bin/env python3
"""Fail when a Protocol stops describing its implementation (PS-165).

WHY THIS IS NOT A MYPY CHECK — read this before "simplifying" it.
=================================================================
The obvious way to gate protocol conformance is to let mypy do it:

    def _check(x: ProfileManager) -> IProfileManager:
        return x            # mypy errors if ProfileManager doesn't conform

That check was written and RUN against the exact drift this script exists to
catch (PS-165, at main b018988, where IProfileManager was 10 parameters behind
ProfileManager.add_profile and 8 behind update_profile).

THE PRECISE CLAIM, because this is load-bearing and you are invited to re-run
it: **with the duplicate `set_cookie_status` declaration removed and the
parameter drift left fully intact, the probe reports zero errors.**

Re-run it exactly that way or you will not see what this paragraph describes.
On pristine b018988 the probe is NOT silent — it fails for an unrelated reason,
the duplicate declaration two lines apart in IProfileManager:

    _probe.py:5: error: Incompatible return value type
        (got "ProfileManager", expected "IProfileManager")  [return-value]
    _probe.py:5: note: "ProfileManager" is missing following
        "IProfileManager" protocol member:
    _probe.py:5: note:     set_cookie_status-redefinition

That error is about the phantom `-redefinition` member, NOT about the drift.
Remove only the duplicate line — leaving add_profile 10 parameters behind and
update_profile 8 behind, verified by AST — and the probe goes silent. Which is
the stronger statement, and the one that matters: it ISOLATES the parameter
drift as the thing mypy is blind to. The check would have shipped green on a
tree everyone agreed was drifted.

The reason is structural, not a mypy bug. A Protocol is a MINIMUM: an
implementation may accept extra parameters as long as they are optional, and it
still substitutes correctly wherever the protocol is expected. Every drifted
parameter here was optional, so the drift ran in the direction protocol
subtyping explicitly TOLERATES. (Verified in the other direction too: adding a
parameter to the *protocol* that the impl lacks does make mypy red — so the
check works, it just cannot see this class of drift.)

That tolerance is correct for substitutability and useless for our purpose. We
do not care only whether ProfileManager can stand in for IProfileManager; we
care whether IProfileManager still DESCRIBES ProfileManager — because callers
hold protocol-typed references and call through them. When the protocol falls
behind, those call sites pass keyword arguments the protocol never declared. At
b018988 that produced 38 of the repo's 136 mypy errors from this one cause.

So the rule enforced here is deliberately STRICTER than protocol subtyping:

    every public parameter and every public method the implementation declares
    must also be declared on the protocol.

The comparison is done with the stdlib `ast` module — no import of the modules
under test, no mypy, no third-party dependency. It reads the source text, so it
holds its verdict regardless of which mypy version is pinned or whether the
project's runtime dependencies are installed.

WHAT THIS DOES NOT CHECK. Type annotations are compared by NAME only, not
resolved or compared for compatibility (that is mypy's job, and the repo-wide
advisory run covers it). This gate answers one question — has the protocol
fallen behind its implementation — and answers it exactly.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent

# (protocol name, protocol file, implementation name, implementation file)
#
# The pairs are listed EXPLICITLY rather than discovered, so that deleting a
# protocol from this list is a visible edit in a diff. A gate whose scope
# silently shrinks is the failure mode this whole ticket is about.
PAIRS: list[tuple[str, str, str, str]] = [
    (
        "IProfileManager",
        "src/interfaces/protocols.py",
        "ProfileManager",
        "src/services/profile/manager.py",
    ),
    (
        "IBrowserLauncher",
        "src/interfaces/protocols.py",
        "BrowserLauncher",
        "src/services/browser/launcher.py",
    ),
    (
        "IProxyService",
        "src/interfaces/protocols.py",
        "ProxyService",
        "src/services/proxy/service.py",
    ),
]


class Method:
    """The public shape of one method: what a caller may pass."""

    def __init__(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.name = node.name
        args = node.args
        # `self` is not part of what a caller passes.
        self.positional = [
            a.arg for a in (*args.posonlyargs, *args.args) if a.arg != "self"
        ]
        self.keyword_only = [a.arg for a in args.kwonlyargs]
        self.takes_var_kwargs = args.kwarg is not None
        self.takes_var_args = args.vararg is not None

    @property
    def accepts(self) -> set[str]:
        return set(self.positional) | set(self.keyword_only)


def _methods(path: Path, class_name: str) -> tuple[dict[str, Method], list[str]]:
    """Public methods of `class_name` in `path`, plus any names declared twice.

    A duplicate declaration is reported rather than silently collapsed: it is
    always a mistake, and on a Protocol it creates a phantom member that type
    checkers surface under a confusing name ("...-redefinition").
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, Method] = {}
    duplicates: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_"):
                continue  # private: not part of the described surface
            if item.name in found:
                duplicates.append(item.name)
                continue
            found[item.name] = Method(item)
    return found, duplicates


def check_pair(
    protocol_name: str,
    protocol_path: str,
    impl_name: str,
    impl_path: str,
    root: Path = DEFAULT_ROOT,
) -> list[str]:
    """Return a list of human-readable conformance failures (empty == clean)."""
    p_file = root / protocol_path
    i_file = root / impl_path

    for f, label in ((p_file, protocol_name), (i_file, impl_name)):
        if not f.is_file():
            return [f"{label}: source file is missing: {f}"]

    protocol, p_dupes = _methods(p_file, protocol_name)
    impl, i_dupes = _methods(i_file, impl_name)

    problems: list[str] = []

    if not protocol:
        problems.append(
            f"{protocol_name}: declares no public methods — either the class was "
            f"renamed or {protocol_path} no longer contains it. A protocol that "
            f"describes nothing conforms to everything, so this is a failure, "
            f"not a pass."
        )
        return problems
    if not impl:
        problems.append(
            f"{impl_name}: declares no public methods — either the class was "
            f"renamed or {impl_path} no longer contains it."
        )
        return problems

    for name in p_dupes:
        problems.append(
            f"{protocol_name}.{name} is declared more than once in "
            f"{protocol_path}. Delete the duplicate: on a Protocol this creates "
            f"a phantom member and makes conformance errors read as "
            f"'{name}-redefinition'."
        )
    for name in i_dupes:
        problems.append(
            f"{impl_name}.{name} is declared more than once in {impl_path}. "
            f"The later definition silently replaces the earlier one."
        )

    for name, i_method in impl.items():
        p_method = protocol.get(name)
        if p_method is None:
            # Not every impl method belongs on the protocol — a protocol is
            # allowed to describe a deliberate SUBSET, and ProfileManager
            # legitimately keeps several methods (save_profiles, clear_proxy,
            # restore_profile, ...) off IProfileManager.
            #
            # So absence is NOT checked here, deliberately: this gate cannot
            # tell "intentionally not on the protocol" from "should be on the
            # protocol and was forgotten". What settles that is whether the
            # method is CALLED through a protocol-typed reference, which is a
            # property of the call sites rather than of these two files — and
            # mypy already reports exactly that, as `attr-defined`, in the
            # repo-wide advisory run. (At PS-165 it reported TEN such call
            # sites, measured at b018988: nine naming IProfileManager — eight
            # distinct methods, `assign_tag` twice — and one naming
            # IBrowserLauncher.shutdown_all. All ten were real and all ten
            # methods were added to their protocol. Note the tenth was missed
            # on the first sweep because that sweep filtered by protocol NAME
            # rather than by defect class; grep the class of error, not the
            # class you expect to find it on:
            #     mypy ... | grep -E 'error: "I[A-Za-z]+" has no attribute')
            #
            # This gate therefore owns the half it can decide from signatures
            # alone: a method present on BOTH must not have drifted.
            continue

        missing = [a for a in i_method.positional if a not in p_method.accepts]
        missing_kw = [a for a in i_method.keyword_only if a not in p_method.accepts]

        if missing and not p_method.takes_var_args:
            problems.append(
                f"{protocol_name}.{name} has fallen behind {impl_name}.{name}: "
                f"the implementation accepts {missing!r} and the protocol does "
                f"not declare {'it' if len(missing) == 1 else 'them'}. A caller "
                f"holding an {protocol_name} cannot pass "
                f"{'this argument' if len(missing) == 1 else 'these arguments'} "
                f"without a type error, even though the call succeeds at "
                f"runtime. Add {'it' if len(missing) == 1 else 'them'} to "
                f"{protocol_path}."
            )
        if missing_kw and not p_method.takes_var_kwargs:
            problems.append(
                f"{protocol_name}.{name} is missing keyword-only parameter(s) "
                f"{missing_kw!r} declared by {impl_name}.{name}."
            )

        # Order matters: callers do pass positionally through protocol-typed
        # references, so a protocol whose positions disagree with the impl's
        # binds arguments to the wrong parameters. (This is the PS-157 class.)
        shared = [a for a in p_method.positional if a in set(i_method.positional)]
        impl_order = [a for a in i_method.positional if a in set(shared)]
        if shared != impl_order:
            problems.append(
                f"{protocol_name}.{name} declares its parameters in a DIFFERENT "
                f"ORDER than {impl_name}.{name}: protocol {shared!r} vs "
                f"implementation {impl_order!r}. A positional call through the "
                f"protocol would bind arguments to the wrong parameters."
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=(
            "Repository root to check (default: this script's repo). Tests use "
            "this to check a THROWAWAY COPY of the tree: verifying that the "
            "gate goes red requires inducing drift, and doing that in the "
            "working tree risks leaving it corrupted if the run is interrupted."
        ),
    )
    args = parser.parse_args(argv)

    all_problems: list[str] = []
    for protocol_name, protocol_path, impl_name, impl_path in PAIRS:
        found = check_pair(
            protocol_name, protocol_path, impl_name, impl_path, root=args.root
        )
        status = "DRIFTED" if found else "ok"
        print(f"[{status}] {protocol_name} <- {impl_name}")
        all_problems.extend(found)

    if not all_problems:
        print(f"\nProtocol conformance: clean ({len(PAIRS)} pairs checked).")
        return 0

    print(f"\nProtocol conformance: {len(all_problems)} problem(s).\n")
    for problem in all_problems:
        print(f"  * {problem}\n")
    print(
        "These are TYPE-level defects, not runtime bugs: the calls reach a\n"
        "concrete implementation that does have these parameters. What has\n"
        "broken is the protocol's ability to describe its implementation — so\n"
        "it can no longer catch a real signature mismatch."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
