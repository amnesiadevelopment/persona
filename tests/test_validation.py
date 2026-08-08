"""Name and proxy validation are the security foundation — profile names become
filesystem paths and proxy strings reach argv, so every rejection branch matters."""
import pytest

from src.utils.validation import validate_profile_name, validate_proxy_format


@pytest.mark.parametrize("name,ok", [
    ("normal", True),
    ("with space", True),
    ("dash-and_underscore", True),
    ("", False),                    # empty
    ("x" * 65, False),              # too long
    ("a/b", False),                 # slash (path sep)
    ("a\\b", False),                # backslash (Windows path sep)
    ("a:b", False),                 # colon (drive / stream)
    ("a<b", False), ("a>b", False), ('a"b', False),
    ("a|b", False), ("a?b", False), ("a*b", False),
    (" leading", False),           # leading space
    ("trailing ", False),          # trailing space
    ("CON", False), ("con", False),  # reserved (case-insensitive)
    ("PRN", False), ("NUL", False), ("COM1", False), ("LPT9", False),
    ("CON.txt", False), ("nul.log", False),  # reserved even with an extension
    ("name.", False),              # Windows strips a trailing dot -> lost dir
    ("name ", False),              # trailing space (same class; also space rule)
    ("a\x00b", False),             # NUL byte
    ("tab\tname", False),          # control char (0x09)
    ("..", False),                 # ends with a dot -> illegal on Windows (the
                                   # path-escape guard in ProfileManager._data_path
                                   # still backstops it too).
    ("a.b", True),                 # a dot mid-name is fine
])
def test_validate_profile_name(name, ok):
    valid, msg = validate_profile_name(name)
    assert valid is ok, f"{name!r}: {msg}"
    if not ok:
        assert msg  # a reason is always given


@pytest.mark.parametrize("proxy,ok", [
    ("", True),                                   # empty = no proxy
    ("1.2.3.4:8080", True),
    ("socks5://1.2.3.4:1080", True),
    ("http://user:pass@host.com:3128", True),
    ("socks5://user:pass@gate.decodo.com:10000", True),
    # audit3 low: kept in sync with the launch parser
    ("socks5://user:pass@gate_us.smartproxy.com:7000", True),  # underscore host
    ("socks5h://user:pass@gate.decodo.com:10000", True),       # socks5h scheme
    ("nonsense", False),                          # no host:port
    ("1.2.3.4", False),                           # missing port
    ("1.2.3.4:0", False),                         # port 0
    ("1.2.3.4:70000", False),                     # port > 65535
])
def test_validate_proxy_format(proxy, ok):
    valid, msg = validate_proxy_format(proxy)
    assert valid is ok, f"{proxy!r}: {msg}"
