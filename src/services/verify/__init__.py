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

from .diff import diff_realms, diff_snapshots, format_diff
from .probes import ALL_REALMS, PROBES, WINDOW, WORKER, Probe, probes_for_realm
from .runner import run_probes
from .snapshot import SCHEMA_VERSION, build_snapshot, canonicalise, dumps, load, write

__all__ = [
    "ALL_REALMS",
    "PROBES",
    "SCHEMA_VERSION",
    "WINDOW",
    "WORKER",
    "Probe",
    "build_snapshot",
    "canonicalise",
    "diff_realms",
    "diff_snapshots",
    "dumps",
    "format_diff",
    "load",
    "probes_for_realm",
    "run_probes",
    "write",
]
