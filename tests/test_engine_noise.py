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


def test_benign_chromium_error_lines_are_filtered():
    benign = [
        # Windows USB enumeration probing properties the host doesn't have
        "[21324:9448:0715/120000.123:ERROR:components\\device_event_log\\"
        "device_event_log_impl.cc:202] [12:00:00.123] USB: "
        "usb_service_win.cc:108 SetupDiGetDeviceProperty"
        "({{A45C254E-DF1C-4EFD-8020-67D146A850E0}}, 6) failed: "
        "Элемент не "
        "найден. (0x490)",
        # EV-cert OID metadata decode chatter
        "[21324:9448:0715/120000.123:ERROR:net\\cert\\"
        "ev_root_ca_metadata.cc:161] Failed to decode OID: 0",
        "[123:456:0715/120000.123:ERROR:net/cert/ev_root_ca_metadata.cc:161]"
        " Failed to decode OID: 0",
        # net_error -100 = ERR_CONNECTION_CLOSED: connection dropped
        # mid-handshake (closed tab, cancelled prefetch), not a TLS failure
        "[12345:67890:0715/120000.123:ERROR:net/socket/"
        "ssl_client_socket_impl.cc:926] handshake failed; returned -1, "
        "SSL error code 1, net_error -100",
        # Linux VA-API probing without a usable GPU
        "[12345:12345:ERROR:vaapi_wrapper.cc(1616)] vaInitialize failed: "
        "unknown libva error",
    ]
    for m in benign:
        assert is_engine_noise(m), f"should be filtered: {m!r}"


def test_benign_shutdown_and_fontconfig_lines_are_filtered():
    # Chromium shutdown chatter: the parent polls a content child that already
    # exited, so the zygote GetTerminationStatus send fails — expected on close,
    # not a persona failure. And fontconfig "Cannot load default config file" is
    # benign chromium-child noise when the host fontconfig isn't ideal (the
    # engine spoofs fonts itself); the page still renders. Both are file-log
    # only, never a red Activity Log line (#212).
    benign = [
        "[951814:951814:0724/131544.095515:ERROR:content/common/zygote/"
        "zygote_communication_linux.cc:291] Failed to send GetTerminationStatus "
        "message to zygote",
        "Fontconfig error: Cannot load default config file: File not found",
        "Fontconfig error: Cannot load default config file: No such file or "
        "directory",
    ]
    for m in benign:
        assert is_engine_noise(m), f"should be filtered: {m!r}"


def test_benign_dbus_lines_are_filtered():
    # Chromium in a headless/VM session with no session D-Bus floods stderr with
    # bus-connect and NameHasOwner failures. They're expected where there's no
    # desktop bus and the browser runs fine; on a real host with a bus they don't
    # appear. File-log only, never a red Activity Log line.
    benign = [
        "[3171652:3171669:0726/195434.638314:ERROR:dbus/bus.cc:405] Failed to "
        "connect to the bus: Could not parse server address: Unknown address type "
        '(examples of valid types are "tcp" and on UNIX "unix")',
        "[3171652:3171652:0726/195434.755830:ERROR:dbus/object_proxy.cc:572] "
        "Failed to call method: org.freedesktop.DBus.NameHasOwner: "
        "object_path= /org/freedesktop/DBus: unknown error type:",
    ]
    for m in benign:
        assert is_engine_noise(m), f"should be filtered: {m!r}"


def test_benign_webrtc_p2p_lines_are_filtered():
    # A fingerprint scanner's WebRTC test (iphey) drives ICE gathering with no
    # reachable STUN/TURN, so chromium's P2P stack logs name-resolution and
    # TURN-socket failures at ERROR level. They are expected probe chatter, not
    # a persona failure — file-log only, never a red Activity Log line (#212).
    benign = [
        "[12345:67890:0716/120000.123:ERROR:socket_manager.cc(120)] "
        "Failed to resolve address example.com: net_error -105",
        "[12345:67890:0716/120000.123:ERROR:turn_port.cc(456)] "
        "Failed to create TURN client socket",
        "[12345:67890:0716/120000.123:ERROR:stun_port.cc(200)] "
        "Jingle:Port[...] UDP send of 0 bytes failed with error 1",
    ]
    for m in benign:
        assert is_engine_noise(m), f"should be filtered: {m!r}"


def test_real_error_lines_still_surface():
    real = [
        # any handshake net_error other than -100 is a real TLS problem
        "[12345:67890:0715/120000.123:ERROR:net/socket/"
        "ssl_client_socket_impl.cc:926] handshake failed; returned -1, "
        "SSL error code 1, net_error -101",
        "[12345:67890:0715/120000.123:ERROR:net/socket/"
        "ssl_client_socket_impl.cc:926] handshake failed; returned -1, "
        "SSL error code 1, net_error -1002",
        # -100 elsewhere in the net stack is not the benign handshake shape
        "[1:2:ERROR:net/socket/ssl_client_socket_impl.cc:926] "
        "certificate verification failed, net_error -100",
        "[1:1:0715/120000.123:ERROR:gpu_process_host.cc(999)] "
        "GPU process exited unexpectedly: exit_code=139",
        "[1:1:ERROR:cert_verify_proc.cc(345)] "
        "CertVerifyProc failed: ERR_CERT_DATE_INVALID",
    ]
    for m in real:
        assert not is_engine_noise(m), f"should be kept: {m!r}"
