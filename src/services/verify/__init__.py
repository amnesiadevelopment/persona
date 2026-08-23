"""In-browser verification: observe what a live profile ACTUALLY exposes.

persona's fingerprint tests today are substring checks over generated JS text
(``assert "987654" in js``) — they pass whether or not the override installed,
reached a Web Worker, or was honoured by the browser. This package is the
machine that actually looks: it runs a deterministic probe set inside a live,
already-running profile on either engine, and writes what it observes to a
canonical, diffable JSON snapshot.

    probes      the vector inventory, as data
    runner      realm execution (window directly; worker via a Blob Worker)
    snapshot    canonicalisation — byte-stable, no timestamps, no omissions
    diff        the comparator
    transport   the real dual-engine adapter (playwright imported lazily)
    cli         record / diff, against an already-running profile

It gates nothing. It produces evidence; deciding what a difference MEANS — the
host-leak gate, differential unlinkability, continuity across an engine update
— belongs to the slices that consume this artifact.

``transport`` is deliberately NOT imported here: it reaches for playwright and
the profile store, neither of which is available in every environment this
package must stay importable in. Import it directly where you need it.
"""

from .diff import (
    INCONCLUSIVE,
    NotASnapshot,
    diff_realms,
    diff_snapshots,
    format_diff,
    inconclusive_count,
)
from .probes import ALL_REALMS, PROBES, WINDOW, WORKER, Probe, probes_for_realm
from .runner import run_probes
from .schema_ledger import (
    SchemaLedgerViolation,
    check_emitted_header,
    generation_of,
    header_keys,
    mislabelled,
)
# Re-exported under SNAPSHOT_-qualified names, deliberately.
#
# There are two independent schema versions in this package and they are NOT
# interchangeable: ``snapshot.SCHEMA_VERSION`` governs what a live profile
# exposes, ``matrix.SCHEMA_VERSION`` what third-party checkers reported. Until
# PS-81 both read 1, so a bare package-level ``SCHEMA_VERSION`` was merely
# ambiguous. They now differ (matrix is 2), which turns that shadow into a
# wrong answer for anyone who reaches for the unqualified name — precisely the
# consumer this ticket exists to protect. So the ambiguous spellings are not
# offered at package level at all; ask the module you actually mean.
from .snapshot import (
    HEADER_GENERATIONS as SNAPSHOT_HEADER_GENERATIONS,
    SCHEMA_VERSION as SNAPSHOT_SCHEMA_VERSION,
    POST_WRITER_ANNOTATIONS,
    SNAPSHOT_BODY_KEY,
    build_snapshot,
    canonicalise,
    dumps,
    engine_build,
    load,
    write,
)

__all__ = [
    "ALL_REALMS",
    "INCONCLUSIVE",
    "POST_WRITER_ANNOTATIONS",
    "PROBES",
    "SNAPSHOT_BODY_KEY",
    "SNAPSHOT_HEADER_GENERATIONS",
    "SNAPSHOT_SCHEMA_VERSION",
    "SchemaLedgerViolation",
    "check_emitted_header",
    "generation_of",
    "header_keys",
    "mislabelled",
    "WINDOW",
    "WORKER",
    "NotASnapshot",
    "Probe",
    "build_snapshot",
    "canonicalise",
    "diff_realms",
    "diff_snapshots",
    "dumps",
    "engine_build",
    "format_diff",
    "inconclusive_count",
    "load",
    "probes_for_realm",
    "run_probes",
    "write",
]
