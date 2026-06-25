from src.services.browser.launcher import is_engine_noise


def test_gtk_a11y_noise_is_filtered():
    noisy = [
        "(chrome:537611): dbind-WARNING **: 23:07:36.529: AT-SPI: Error "
        "retrieving accessibility bus address",
        "(flet:537611): Atk-CRITICAL **: atk_socket_embed: assertion failed",
        "Gdk-Message: 23:07:36.775: Unable to load  from the cursor theme",
        "- [pid=123] some internal chatter",
    ]
    for m in noisy:
        assert is_engine_noise(m), f"should be filtered: {m!r}"


def test_real_messages_are_kept():
    keep = [
        "Browser started!",
        "[Test IE] imported 12 cookies",
        "Engine installed: v1.1.0",
        "Session ended: Test IE",
    ]
    for m in keep:
        assert not is_engine_noise(m), f"should be kept: {m!r}"
