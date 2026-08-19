"""Realm execution: run the inventory through an injected ``evaluate``.

``evaluate`` is a ``Callable[[str], Any]`` that runs one JS expression in a
live profile and returns its resolved value. Both persona engines already
provide exactly that (chromium over CDP, firefox over its juggler eval hook)
and both AWAIT a returned Promise — see ``transport``. That single fact is why
an asynchronous probe (a Worker round-trip, an offline audio render) needs no
per-engine harness: it is just one expression string.

Two realms today:

``window``
    One ``evaluate`` call per probe. Per-probe isolation means a probe that
    throws — or a transport that fails on that one call — is recorded as an
    error against that probe and nothing else.

``worker``
    Necessarily batched: one Blob ``Worker`` is built, the worker-eligible
    subset of the inventory runs inside it, and the results come back over a
    single ``postMessage``. The Worker is terminated in a ``finally`` so a
    probe run leaves nothing behind, and the whole harness is bounded by a
    timeout so a wedged worker fails the run instead of hanging it.

Completeness is the contract: :func:`run_probes` returns an entry for EVERY
probe that declares the realm. An unobtainable reading is recorded as an error
— it is inconclusive, and inconclusive is never a pass.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .probes import PROBES, WINDOW, WORKER, Probe, probes_for_realm

# How long the page-side harness waits for the Worker to answer, in ms. The
# worker runs the whole worker-eligible subset, including a WebGL context
# acquisition, so this is generous — it is a hang guard, not a latency budget.
WORKER_TIMEOUT_MS = 30_000

# Marker embedded in every wrapper so a caller (and the test suite's fake
# ``evaluate``) can tell which probe an expression belongs to without parsing
# JavaScript. A comment is inert in every realm.
_MARKER = "/*probe:{id}*/"

Evaluate = Callable[[str], Any]

# --- JS building blocks -----------------------------------------------------

# "<ClassName>: <message>" for any thrown value, including non-Errors.
_JS_FMT_ERR = (
    "function __pvErr(e){"
    "try{"
    "var n=(e&&e.constructor&&e.constructor.name)?e.constructor.name:"
    "((e&&e.name)?e.name:(typeof e));"
    "var m=(e&&e.message!==undefined)?e.message:String(e);"
    "return n+': '+m;"
    "}catch(x){return 'Error: <unstringifiable throw>';}"
    "}"
)


def window_expression(probe: Probe) -> str:
    """Wrap one probe for a single-expression window-realm evaluation.

    Resolves to ``{"v": <value>}`` or ``{"e": "<Class>: <msg>"}`` — never
    rejects, so one bad probe cannot take the run down.
    """
    return (
        "(function(){"
        + _MARKER.format(id=probe.id)
        + _JS_FMT_ERR
        + "try{return Promise.resolve(("
        + probe.expr
        + ")).then(function(v){return {v:v};},function(e){return {e:__pvErr(e)};});}"
        "catch(e){return {e:__pvErr(e)};}"
        "})()"
    )


def worker_source(probes: "tuple[Probe, ...] | list[Probe]") -> str:
    """The JS body of the harness Worker for ``probes``.

    Each probe becomes a task function; the worker runs them all, collects
    ``{v}``/``{e}`` per id, and posts the map back once every task settles.
    """
    tasks = ",".join(
        json.dumps(p.id)
        + ":function(){"
        + _MARKER.format(id=p.id)
        + "return ("
        + p.expr
        + ");}"
        for p in probes
    )
    return (
        _JS_FMT_ERR
        + "var TASKS={" + tasks + "};"
        "var out={};"
        "var ids=Object.keys(TASKS);"
        "var pending=ids.length;"
        "if(!pending){self.postMessage(out);}else{"
        "ids.forEach(function(id){"
        "var done=function(){if(--pending===0){self.postMessage(out);}};"
        "try{"
        "Promise.resolve(TASKS[id]()).then("
        "function(v){out[id]={v:v};done();},"
        "function(e){out[id]={e:__pvErr(e)};done();});"
        "}catch(e){out[id]={e:__pvErr(e)};done();}"
        "});}"
    )


def worker_expression(probes: "tuple[Probe, ...] | list[Probe]") -> str:
    """Page-side expression that spins up the harness Worker and resolves to
    its result map, or to ``{"__harness_error": "..."}`` if it never answers.

    The Worker is terminated and its blob URL revoked in a ``.finally`` — on
    the success path, the error path AND the timeout path.
    """
    src = json.dumps(worker_source(probes))
    return (
        "(function(){"
        + _JS_FMT_ERR
        + "if(typeof Worker!=='function'){"
        "return {__harness_error:'TypeError: Worker is not available in this realm'};}"
        "var url=null,w=null;"
        "try{url=URL.createObjectURL(new Blob([" + src + "],"
        "{type:'application/javascript'}));}"
        "catch(e){return {__harness_error:__pvErr(e)};}"
        "var timer=null;"
        "var p=new Promise(function(resolve){"
        "var settle=function(r){if(timer!==null){clearTimeout(timer);timer=null;}resolve(r);};"
        "timer=setTimeout(function(){"
        "settle({__harness_error:'TimeoutError: worker did not answer within "
        + str(WORKER_TIMEOUT_MS) + "ms'});},"
        + str(WORKER_TIMEOUT_MS) + ");"
        "try{"
        "w=new Worker(url);"
        "w.onmessage=function(ev){settle(ev.data);};"
        "w.onerror=function(ev){settle({__harness_error:'WorkerError: '+"
        "((ev&&ev.message)?ev.message:'worker failed to start')});};"
        "w.onmessageerror=function(){settle({__harness_error:"
        "'DataCloneError: worker result was not structured-cloneable'});};"
        "}catch(e){settle({__harness_error:__pvErr(e)});}"
        "});"
        # `.finally` is a reserved-word property in older parsers; bracket
        # access keeps the expression safe to ship as a string.
        "return p['finally'](function(){"
        "try{if(w)w.terminate();}catch(e){}"
        "try{if(url)URL.revokeObjectURL(url);}catch(e){}"
        "});"
        "})()"
    )


def _as_entry(raw: Any, *, probe_id: str) -> dict:
    """Normalise one ``{v}``/``{e}`` reply into a snapshot entry."""
    if isinstance(raw, dict):
        if "e" in raw:
            return {"error": str(raw["e"])}
        if "v" in raw:
            return {"value": raw["v"]}
    return {
        "error": (
            "ProtocolError: probe "
            f"{probe_id!r} returned a malformed reply ({type(raw).__name__})"
        )
    }


def _err(exc: BaseException) -> dict:
    return {"error": f"{type(exc).__name__}: {exc}"}


def run_window_realm(evaluate: Evaluate) -> dict:
    """Evaluate every window-realm probe, one ``evaluate`` call each."""
    out: dict[str, dict] = {}
    for probe in probes_for_realm(WINDOW):
        try:
            raw = evaluate(window_expression(probe))
        except Exception as exc:  # transport failed for THIS probe only
            out[probe.id] = _err(exc)
            continue
        out[probe.id] = _as_entry(raw, probe_id=probe.id)
    return out


def run_worker_realm(evaluate: Evaluate) -> dict:
    """Evaluate every worker-realm probe inside one Blob Worker."""
    wanted = probes_for_realm(WORKER)
    out: dict[str, dict] = {}
    if not wanted:
        return out
    try:
        raw = evaluate(worker_expression(wanted))
    except Exception as exc:
        return {p.id: _err(exc) for p in wanted}

    if not isinstance(raw, dict):
        reason = {
            "error": (
                "ProtocolError: worker harness returned "
                f"{type(raw).__name__}, expected an object"
            )
        }
        return {p.id: dict(reason) for p in wanted}

    harness_error = raw.get("__harness_error")
    if harness_error:
        reason = {"error": f"WorkerHarness: {harness_error}"}
        return {p.id: dict(reason) for p in wanted}

    for probe in wanted:
        if probe.id not in raw:
            out[probe.id] = {
                "error": (
                    "MissingResult: the worker harness returned no entry for "
                    f"{probe.id!r}"
                )
            }
            continue
        out[probe.id] = _as_entry(raw[probe.id], probe_id=probe.id)
    return out


_REALM_RUNNERS: "dict[str, Callable[[Evaluate], dict]]" = {
    WINDOW: run_window_realm,
    WORKER: run_worker_realm,
}


def run_probes(
    evaluate: Evaluate, realms: "tuple[str, ...] | list[str]" = (WINDOW, WORKER)
) -> dict:
    """Run the inventory in each of ``realms``.

    Returns ``{realm: {probe_id: {"value": ...} | {"error": "..."}}}``. Every
    probe declaring a requested realm is present in the result — a probe is
    never silently dropped, because a missing reading is inconclusive and
    inconclusive is never a pass.
    """
    unknown = [r for r in realms if r not in _REALM_RUNNERS]
    if unknown:
        raise ValueError(f"unknown realm(s): {sorted(unknown)}")

    results: dict[str, dict] = {}
    for realm in realms:
        realm_out = _REALM_RUNNERS[realm](evaluate)
        # Belt and braces: whatever the realm runner did or failed to do, the
        # inventory defines the key set.
        for probe in probes_for_realm(realm):
            realm_out.setdefault(
                probe.id,
                {
                    "error": (
                        "MissingResult: no reading was produced for "
                        f"{probe.id!r} in realm {realm!r}"
                    )
                },
            )
        results[realm] = realm_out
    return results


__all__ = [
    "Evaluate",
    "PROBES",
    "WORKER_TIMEOUT_MS",
    "run_probes",
    "run_window_realm",
    "run_worker_realm",
    "window_expression",
    "worker_expression",
    "worker_source",
]
