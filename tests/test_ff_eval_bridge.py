"""The FF engine has no CDP (juggler pipe), so the MCP browser tools can't
attach the way they do for chromium. A module-level eval registry lets the FF
launch worker publish a callable that runs JS on its live page; MCP looks it up
for firefox profiles and drives them the same as chromium.
"""
import src.services.browser.invisible_launch as il


def test_registry_starts_empty_and_round_trips():
    il._ff_eval_registry.clear()
    assert il.get_ff_eval("nope") is None

    calls = []
    il.register_ff_eval("prof", lambda expr: calls.append(expr) or "ok")
    fn = il.get_ff_eval("prof")
    assert fn is not None
    assert fn("1+1") == "ok"
    assert calls == ["1+1"]

    il.unregister_ff_eval("prof")
    assert il.get_ff_eval("prof") is None


def test_unregister_is_idempotent():
    il._ff_eval_registry.clear()
    il.unregister_ff_eval("never")  # no raise
    il.register_ff_eval("x", lambda e: None)
    il.unregister_ff_eval("x")
    il.unregister_ff_eval("x")  # again, no raise
    assert il.get_ff_eval("x") is None
