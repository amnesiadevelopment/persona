"""Realm execution: run the inventory through an injected ``evaluate``.

``evaluate`` is a ``Callable[[str], Any]`` that runs one JS expression in a
live profile and returns its resolved value. Both persona engines already
provide exactly that (chromium over CDP, firefox over its juggler eval hook)
and both AWAIT a returned Promise — see ``transport``. That single fact is why
an asynchronous probe (a Worker round-trip, an offline audio render) needs no
per-engine harness: it is just one expression string.

Three realms today:

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

``child_frame``
    A same-origin child browsing context, entered by **indexed access**
    (``self[N]``) rather than through the ``contentWindow`` accessor. That
    distinction is the entire reason this realm exists and is not an
    implementation detail to be tidied away: PS-193 measured a masking chain
    that hooked the ``contentWindow`` getter while a real checker took its
    phantom frame by index — a path the accessor hook never sees — and the
    unperturbed buffer was handed straight out while every check stayed green.
    A harness that reached this realm through the accessor would reproduce
    exactly that blind spot and return a clean reading over the live defect.

    Batched like the worker, for the same reason: the child-eligible subset is
    compiled *inside* the child realm using that realm's own ``Function``
    constructor, so the probes close over the CHILD's intrinsics rather than
    the top realm's. The frame is removed in a ``finally`` — the realm is
    transient, exactly as the Worker is terminated — and the harness is bounded
    by a timeout so a wedged child fails the run instead of hanging it.

Completeness is the contract: :func:`run_probes` returns an entry for EVERY
probe that declares the realm. An unobtainable reading is recorded as an error
— it is inconclusive, and inconclusive is never a pass.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .probes import CHILD_FRAME, PROBES, WINDOW, WORKER, Probe, probes_for_realm

# How long the page-side harness waits for the Worker to answer, in ms. The
# worker runs the whole worker-eligible subset, including a WebGL context
# acquisition, so this is generous — it is a hang guard, not a latency budget.
WORKER_TIMEOUT_MS = 30_000

# The same hang guard for the child-frame harness. A child realm can wedge in
# ways a Worker cannot (a frame that never finishes loading), so the bound is
# not optional — an unanswered child fails the run rather than hanging it, and
# a failed run is recorded as an error, which is never a pass.
CHILD_FRAME_TIMEOUT_MS = 30_000

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


def child_frame_source(probes: "tuple[Probe, ...] | list[Probe]") -> str:
    """The JS body compiled INSIDE the child realm for ``probes``.

    Returned as a function BODY, not an expression: the caller compiles it with
    the child realm's own ``Function`` constructor, so every intrinsic the
    probes touch is the child's. Compiling it in the top realm and passing the
    result down would defeat the point — the probes would close over the
    parent's globals and this realm would report the parent's readings.

    Resolves to a JSON **string** of ``{id: {v}|{e}}``. A string crosses the
    realm boundary as a primitive, so nothing depends on structured cloning or
    on the two realms sharing an ``Object`` identity.
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
        "return Promise.all(ids.map(function(id){"
        "return Promise.resolve().then(TASKS[id]).then("
        "function(v){out[id]={v:(v===undefined?null:v)};},"
        "function(e){out[id]={e:__pvErr(e)};});"
        "})).then(function(){return JSON.stringify(out);});"
    )


def child_frame_expression(probes: "tuple[Probe, ...] | list[Probe]") -> str:
    """Page-side expression that creates a same-origin child realm, runs
    ``probes`` inside it, and resolves to its result map — or to
    ``{"__harness_error": "..."}`` if the realm could not be entered.

    **The child is reached by INDEXED access (``self[N]``), never through the
    ``contentWindow`` accessor.** This is the load-bearing line of the whole
    realm and must not be "simplified" to the accessor: PS-193 measured a real
    checker taking its frame by index while persona's chain hooked the
    ``contentWindow`` getter, so the accessor path saw nothing, the hook never
    fired, and an unperturbed WebGL buffer went out under a green check. A
    harness that entered this realm through the accessor would be blind to
    precisely the defect this realm was added to observe.

    The frame is removed in a ``finally`` — on the success path, the error path
    AND the timeout path — so a probe run leaves no realm behind.
    """
    body = json.dumps(child_frame_source(probes))
    return (
        "(function(){"
        + _JS_FMT_ERR
        + "if(typeof document==='undefined'||!document.createElement){"
        "return {__harness_error:"
        "'TypeError: this realm has no document to host a child frame'};}"
        "var root=document.body||document.documentElement;"
        "if(!root){return {__harness_error:"
        "'TypeError: no document element to host a child realm'};}"
        "var frame=null,timer=null;"
        "var cleanup=function(){"
        "try{if(timer!==null){clearTimeout(timer);timer=null;}}catch(e){}"
        "try{if(frame&&frame.parentNode){frame.parentNode.removeChild(frame);}}catch(e){}"
        "};"
        "try{"
        "frame=document.createElement('iframe');"
        # Same-origin is required: a cross-origin child cannot be read at all,
        # and this realm exists to be READ. `allow-scripts` + `allow-same-origin`
        # is the minimum that yields a scriptable same-origin realm.
        "frame.setAttribute('sandbox','allow-same-origin allow-scripts');"
        "frame.setAttribute('aria-hidden','true');"
        "frame.style.display='none';"
        "root.appendChild(frame);"
        "}catch(e){cleanup();return {__harness_error:__pvErr(e)};}"
        "var p=new Promise(function(resolve){"
        "var settle=function(r){resolve(r);};"
        "timer=setTimeout(function(){settle({__harness_error:"
        "'TimeoutError: child frame did not answer within "
        + str(CHILD_FRAME_TIMEOUT_MS) + "ms'});},"
        + str(CHILD_FRAME_TIMEOUT_MS) + ");"
        "try{"
        # --- INDEXED ACCESS. Not `frame.contentWindow`. See the docstring. ---
        # `self.length` is the number of child browsing contexts; the one just
        # appended is the last. Reading it by index goes through the
        # WindowProxy's indexed getter, which is a different path from the
        # HTMLIFrameElement.prototype.contentWindow accessor.
        "var idx=self.length-1;"
        "if(!(idx>=0)){settle({__harness_error:"
        "'RangeError: no child browsing context after append'});return;}"
        "var w=self[idx];"
        "if(!w){settle({__harness_error:"
        "'TypeError: indexed access yielded no child realm'});return;}"
        "var mk=w.Function;"
        "if(typeof mk!=='function'){settle({__harness_error:"
        "'TypeError: child realm exposes no Function constructor'});return;}"
        "Promise.resolve(new mk(" + body + ").call(w)).then("
        "function(s){"
        "try{settle(JSON.parse(s));}"
        "catch(e){settle({__harness_error:"
        "'ProtocolError: child realm returned an unparsable reply'});}"
        "},function(e){settle({__harness_error:__pvErr(e)});});"
        "}catch(e){settle({__harness_error:__pvErr(e)});}"
        "});"
        # `.finally` is a reserved-word property in older parsers; bracket
        # access keeps the expression safe to ship as a string.
        "return p['finally'](cleanup);"
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


def run_child_frame_realm(evaluate: Evaluate) -> dict:
    """Evaluate every child-frame-realm probe inside one same-origin child realm.

    Batched like the worker, and failing like it: a realm that could not be
    ENTERED yields an error against every probe that declared it, never an
    absence and never a value. That rule is the point — an unreachable realm
    that recorded nothing would compare as agreement downstream, which is the
    exact failure mode this realm was added to make impossible.
    """
    wanted = probes_for_realm(CHILD_FRAME)
    out: dict[str, dict] = {}
    if not wanted:
        return out
    try:
        raw = evaluate(child_frame_expression(wanted))
    except Exception as exc:
        return {p.id: _err(exc) for p in wanted}

    if not isinstance(raw, dict):
        reason = {
            "error": (
                "ProtocolError: child frame harness returned "
                f"{type(raw).__name__}, expected an object"
            )
        }
        return {p.id: dict(reason) for p in wanted}

    harness_error = raw.get("__harness_error")
    if harness_error:
        reason = {"error": f"ChildFrameHarness: {harness_error}"}
        return {p.id: dict(reason) for p in wanted}

    for probe in wanted:
        if probe.id not in raw:
            out[probe.id] = {
                "error": (
                    "MissingResult: the child frame harness returned no entry "
                    f"for {probe.id!r}"
                )
            }
            continue
        out[probe.id] = _as_entry(raw[probe.id], probe_id=probe.id)
    return out


_REALM_RUNNERS: "dict[str, Callable[[Evaluate], dict]]" = {
    WINDOW: run_window_realm,
    WORKER: run_worker_realm,
    CHILD_FRAME: run_child_frame_realm,
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
    "CHILD_FRAME_TIMEOUT_MS",
    "Evaluate",
    "PROBES",
    "WORKER_TIMEOUT_MS",
    "child_frame_expression",
    "child_frame_source",
    "run_child_frame_realm",
    "run_probes",
    "run_window_realm",
    "run_worker_realm",
    "window_expression",
    "worker_expression",
    "worker_source",
]
