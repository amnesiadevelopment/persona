"""The SCANNER that makes :mod:`launch_perimeter`'s list load-bearing.

A list nobody checks is the "check that cannot fail" PS-8's own caution names.
This module walks the launch surface and reports every site that writes outside
a profile's own directory, so :data:`~.launch_perimeter.PERIMETER_ARTIFACTS`
can be compared against what the tree actually does. The comparison is the
deliverable; the list is its input.

WHY AN AST WALK AND NOT A GREP
-------------------------------
The distinction the six historical defects turn on is a KEYWORD ARGUMENT:

    tempfile.mkstemp(prefix="persona-mtls-nsspw-")                  # PS-57, the defect
    tempfile.mkstemp(prefix="persona-mtls-nsspw-", dir=<profile>)   # PS-57, the fix

Those two lines share every token a grep would match on. `dir=` is what moves
the file from the host's shared temp dir into the perimeter, and only a parse
can see it. The same is true of the platform guards: a call wrapped in
``if _platform.supports_linux_desktop_integration():`` is a Linux-only artifact
and the identical call outside that ``if`` is an unguarded one — a difference
in the enclosing block, which no line-oriented probe can read.

WHAT COUNTS AS A WRITE SITE
----------------------------
Three families, each a shape one of the six actually had:

* ``host-home``  — ``os.path.expanduser("~...")``: a path under the OPERATOR's
  home, outside ``PERSONA_HOME`` entirely. PS-16's desktop entry.
* ``host-temp``  — a ``tempfile`` factory with no ``dir=``, or a ``/tmp``
  literal: the host's shared temp dir. PS-57 and PS-129.
* ``home-store`` — a ``core.config`` constant that resolves inside
  ``PERSONA_HOME`` but outside any one profile's directory. PS-234's
  ``known_hosts`` shape, and ``running_sessions.json``'s.

⚠️ IT REPORTS SITES, NOT DEFECTS. A site being found says only that this line
writes outside a profile directory — which every entry in the inventory does,
by definition. The FINDING is a site the inventory cannot account for, and that
comparison belongs to the check, not to this scanner. Keeping them apart is
what lets the scanner be tested on its own terms.

⛔ STATIC, SO IT SEES SITES AND NOT RUNS. A write reached only through a
dynamically-built attribute, an ``exec``, or a third-party library's own file
handling is invisible here. That bound is admitted in ``UNCOVERED_SURFACES``
rather than left to be discovered — the same reason ``LAUNCH_SURFACE`` states
its own narrowness.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass

from .launch_perimeter import (
    HOME_SCOPED_STORES,
    LAUNCH_SURFACE,
    TEMPFILE_FACTORIES,
)

#: A path under the operator's own home, outside ``PERSONA_HOME``.
KIND_HOST_HOME = "host-home"
#: The host's shared temporary directory.
KIND_HOST_TEMP = "host-temp"
#: Inside ``PERSONA_HOME``, outside any one profile's directory.
KIND_HOME_STORE = "home-store"

#: The predicate whose truth makes a site Linux-only. Read from the enclosing
#: ``if`` rather than assumed, because the identical call outside that guard is
#: an unguarded host write — which is the difference PS-16 turned on.
_LINUX_GUARDS = frozenset(
    {"supports_linux_desktop_integration", "needs_fork_launch", "IS_LINUX"}
)


@dataclass(frozen=True)
class WriteSite:
    """One place on the launch surface that writes outside a profile dir."""

    #: Repo-relative path.
    path: str
    #: Line, for a human reading the report. The MATCH is on ``site``.
    lineno: int
    #: The enclosing function, or ``<module>``.
    symbol: str
    #: One of the KIND_* constants.
    kind: str
    #: The source fragment that matched, for the report.
    detail: str
    #: True when the site sits under a Linux-only guard.
    linux_guarded: bool

    @property
    def site(self) -> str:
        """``path.py:symbol`` — the coordinate the inventory is keyed by."""
        return f"{self.path}:{self.symbol}"


class _Scanner(ast.NodeVisitor):
    """Collect write sites in one module.

    Tracks the enclosing function and whether we are inside a Linux-only guard,
    because both are properties of a site's CONTEXT that the line itself cannot
    carry.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.sites: list[WriteSite] = []
        self._symbols: list[str] = ["<module>"]
        self._guard_depth = 0

    # -- context ------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._symbols.append(node.name)
        self.generic_visit(node)
        self._symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_If(self, node: ast.If) -> None:
        guarded = _mentions_linux_guard(node.test)
        if guarded:
            self._guard_depth += 1
        for child in node.body:
            self.visit(child)
        if guarded:
            self._guard_depth -= 1
        # The `else` arm is NOT inside the guard — a write there runs on the
        # platforms the test excluded, which is the opposite claim.
        for child in node.orelse:
            self.visit(child)

    # -- the three families -------------------------------------------------

    def _add(self, node: ast.AST, kind: str, detail: str) -> None:
        self.sites.append(
            WriteSite(
                path=self.path,
                lineno=getattr(node, "lineno", 0),
                symbol=self._symbols[-1],
                kind=kind,
                detail=detail,
                linux_guarded=self._guard_depth > 0,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        name = _called_name(node.func)
        if name == "expanduser":
            arg = node.args[0] if node.args else None
            shown = repr(arg.value) if isinstance(arg, ast.Constant) else "<dynamic>"
            self._add(node, KIND_HOST_HOME, f"expanduser({shown})")
        elif name in TEMPFILE_FACTORIES:
            # ⭐ THE `dir=` KEYWORD IS THE WHOLE DISTINCTION — see the module
            # docstring. With it the file lands inside the perimeter and is not
            # a write site at all; without it, the host's shared temp dir.
            if not any(kw.arg == "dir" for kw in node.keywords):
                self._add(node, KIND_HOST_TEMP, f"tempfile.{name}() with no dir=")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # ⭐ LOADS ONLY, AND THE CONTEXT IS THE WHOLE DISCRIMINATOR. `core.config`
        # DEFINES these names (`BOOKMARKS_FILE = _under_home(...)`), which is a
        # Store, and every CONSUMER reads them, which is a Load. Counting the
        # definition would report the module that declares the store paths as
        # eight separate out-of-perimeter write sites — noise that would have to
        # be silenced by a per-file exclusion, and a per-file exclusion on the
        # one module that owns every path is exactly the blind spot this scan
        # must not have. Reading the ctx costs nothing and needs no exception.
        if node.id in HOME_SCOPED_STORES and isinstance(node.ctx, ast.Load):
            self._add(node, KIND_HOME_STORE, f"config.{node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in ("config", "cfg")
            and node.attr in HOME_SCOPED_STORES
        ):
            self._add(node, KIND_HOME_STORE, f"config.{node.attr}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value.startswith(("/tmp", "/var/tmp")):
            self._add(node, KIND_HOST_TEMP, f"literal {node.value!r}")


def _called_name(func: ast.expr) -> "str | None":
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _mentions_linux_guard(test: ast.expr) -> bool:
    """Does this ``if`` test gate its body on being Linux?

    Answered by walking the test for any of the three predicates the codebase
    actually uses, rather than by matching one spelling: the same fact is
    written ``_platform.IS_LINUX``, ``supports_linux_desktop_integration()``
    and ``needs_fork_launch()`` in different places, and a scanner that knew
    only one of them would read the other two as unguarded.
    """
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and node.id in _LINUX_GUARDS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _LINUX_GUARDS:
            return True
    return False


def scan_module(path: str, *, root: str = "") -> list[WriteSite]:
    """Every out-of-perimeter write site in one module.

    ``path`` is repo-relative and stays that way on the returned sites, so a
    site's coordinate does not depend on where the scan was run from.
    """
    full = os.path.join(root, path) if root else path
    with open(full, encoding="utf-8") as fh:
        source = fh.read()
    scanner = _Scanner(path)
    scanner.visit(ast.parse(source, filename=full))
    return scanner.sites


def scan_launch_surface(
    *, root: str = "", surface: "tuple[str, ...] | None" = None
) -> list[WriteSite]:
    """Every out-of-perimeter write site across the whole launch surface.

    A module that cannot be read is a REFUSAL rather than an empty result: a
    surface that silently shrank to nothing would report "no write sites" and
    read as a clean tree, which is the vacuous green this whole subsystem
    exists to refuse. The caller (the check) turns that into CANNOT_RUN.
    """
    modules = LAUNCH_SURFACE if surface is None else surface
    if not modules:
        raise FileNotFoundError(
            "the launch surface is empty, so this scan would report no write "
            "sites over nothing at all — indistinguishable from a clean tree."
        )
    found: list[WriteSite] = []
    for rel in modules:
        full = os.path.join(root, rel) if root else rel
        if not os.path.isfile(full):
            raise FileNotFoundError(
                f"{rel} is on the launch surface but is not present at "
                f"{full!r}. A module that moved or was renamed silently "
                "removes itself from this scan, so it is reported rather than "
                "skipped."
            )
        found.extend(scan_module(rel, root=root))
    return found


def repo_root() -> str:
    """The repository root, derived from this file's own location."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


# --- the reach half ---------------------------------------------------------
#
# ⭐ WHY THIS EXISTS AT ALL, and it is worth reading before the code: the write
# scan above CANNOT catch three of the six historical defects. PS-16 stranded
# the old name's desktop entry by OMITTING one call — it added no write site
# and removed none, so a gate watching writes alone goes green over it. That
# was not reasoned; the gate was built, PS-16 was reverted, and it passed.
#
# An artifact outside the perimeter has TWO properties worth checking: that it
# is enumerated, and that something still reaches it. This is the second.


@dataclass(frozen=True)
class CallEdge:
    """One ``caller -> callee`` call inside one module."""

    path: str
    caller: str
    callee: str
    lineno: int

    @property
    def edge(self) -> str:
        """``path.py:caller->callee`` — how a removal site is written."""
        return f"{self.path}:{self.caller}->{self.callee}"


class _CallGraph(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.edges: list[CallEdge] = []
        self._symbols: list[str] = ["<module>"]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._symbols.append(node.name)
        self.generic_visit(node)
        self._symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        callee = _called_name(node.func)
        if callee:
            self.edges.append(
                CallEdge(
                    path=self.path,
                    caller=self._symbols[-1],
                    callee=callee,
                    lineno=node.lineno,
                )
            )
        self.generic_visit(node)


def call_edges(path: str, *, root: str = "") -> list[CallEdge]:
    """Every ``caller -> callee`` edge in one module.

    ⛔ IT PROVES THE CALL IS THERE, NOT THAT IT WORKS. A reach path present but
    broken — wrong argument, wrong order, an exception swallowed on the way —
    satisfies this. What it catches is the path that was DELETED or never
    joined, which is the shape PS-16 actually had, and the bound is stated in
    ``UNCOVERED_SURFACES`` rather than implied.
    """
    full = os.path.join(root, path) if root else path
    with open(full, encoding="utf-8") as fh:
        source = fh.read()
    graph = _CallGraph(path)
    graph.visit(ast.parse(source, filename=full))
    return graph.edges


def missing_removal_sites(
    declared: "tuple[str, ...]", *, root: str = ""
) -> list[str]:
    """Which declared ``path:caller->callee`` removal sites no longer exist.

    A declared site naming a file that cannot be read is REPORTED as missing
    rather than skipped: a removal path in a module that was renamed away is
    exactly as gone as one that was deleted, and skipping it would let a whole
    file disappear in silence.
    """
    wanted: dict[str, list[str]] = {}
    for site in declared:
        path, _, edge = site.partition(":")
        wanted.setdefault(path, []).append(edge)

    missing: list[str] = []
    for path, edges in wanted.items():
        full = os.path.join(root, path) if root else path
        if not os.path.isfile(full):
            missing.extend(f"{path}:{e} (module not found)" for e in edges)
            continue
        try:
            present = {e.edge for e in call_edges(path, root=root)}
        except SyntaxError:  # pragma: no cover - defensive
            missing.extend(f"{path}:{e} (module unparseable)" for e in edges)
            continue
        for edge in edges:
            if f"{path}:{edge}" not in present:
                missing.append(f"{path}:{edge}")
    return sorted(missing)


__all__ = [
    "KIND_HOME_STORE",
    "KIND_HOST_HOME",
    "KIND_HOST_TEMP",
    "CallEdge",
    "WriteSite",
    "call_edges",
    "missing_removal_sites",
    "repo_root",
    "scan_launch_surface",
    "scan_module",
]
