"""Per-profile fontconfig that makes the browser see ONLY persona's bundled
fonts for the profile's OS, ignoring whatever the host has installed. This
keeps the font fingerprint identical on every host, distinct per spoofed OS,
and lets CJK render from fonts we ship — without depending on system fonts.

Named families a site requests by name (Arial, Times New Roman, Courier New,
…) are mapped onto bundled metric-compatible clones (Arimo, Tinos, Cousine) so
text laid out against those families keeps the right advance widths. Without
the mapping fontconfig falls through to DejaVu Sans, which is wider and breaks
metric-sensitive layouts (e.g. Google Sheets columns shift).
"""

import os
import pathlib
import sys

_FONTS_SUBDIR = os.path.join("assets", "fonts")
_COMMON = "common"
_OS_DIRS = {"windows": "windows", "macos": "macos", "linux": "linux"}

_CONF_TEMPLATE = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>{common}</dir>
  <dir>{osdir}</dir>
  <cachedir>{cachedir}</cachedir>
  <!-- Don't fall back to system fonts: the bundled set is the whole world. -->
  <config>
    <rescan><int>30</int></rescan>
  </config>

  <!-- CJK requests resolve to the bundled Noto CJK faces (no tofu). -->
  <match target="pattern">
    <test name="lang" compare="contains"><string>zh</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans CJK SC</string>
    </edit>
  </match>
  <match target="pattern">
    <test name="lang" compare="contains"><string>ja</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans CJK JP</string>
    </edit>
  </match>
  <match target="pattern">
    <test name="lang" compare="contains"><string>ko</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans CJK KR</string>
    </edit>
  </match>

  <!-- Named families a site asks for by name map to bundled metric clones,
       else they fall through to DejaVu Sans (wider) and break column layout. -->
{named_matches}
  <!-- Latin generics fall back to whatever the OS set ships, then CJK. -->
  <alias>
    <family>sans-serif</family>
    <prefer>{sans_prefs}</prefer>
  </alias>
  <alias>
    <family>serif</family>
    <prefer>{serif_prefs}</prefer>
  </alias>
  <alias>
    <family>monospace</family>
    <prefer>{mono_prefs}</prefer>
  </alias>

  <!-- Emoji: the bundled Noto Color Emoji backs every emoji request, and is
       appended to every generic so emoji embedded in normal text render
       instead of tofu. The platform emoji families resolve to it too. -->
  <alias>
    <family>emoji</family>
    <prefer><family>Noto Color Emoji</family></prefer>
  </alias>
  <match target="pattern">
    <test name="family"><string>Apple Color Emoji</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Color Emoji</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>Segoe UI Emoji</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Color Emoji</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>Segoe UI Symbol</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Color Emoji</string></edit>
  </match>
</fontconfig>
"""

_NAMED_MATCH = """\
  <match target="pattern">
    <test name="family"><string>{requested}</string></test>
    <edit name="family" mode="assign" binding="strong"><string>{clone}</string></edit>
  </match>"""

_OS_FAMILIES = {
    "windows": {
        "sans": ["Arimo", "DejaVu Sans"],
        "serif": ["Tinos", "DejaVu Serif"],
        "mono": ["Cousine", "DejaVu Sans Mono"],
    },
    "macos": {
        "sans": ["Noto Sans", "DejaVu Sans"],
        "serif": ["DejaVu Serif"],
        "mono": ["DejaVu Sans Mono"],
    },
    "linux": {
        "sans": ["DejaVu Sans"],
        "serif": ["DejaVu Serif"],
        "mono": ["DejaVu Sans Mono"],
    },
}
_CJK_SANS = ["Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK KR"]
_CJK_SERIF = ["Noto Serif CJK SC"]

# Map the families real sites request by name onto the bundled clone that
# carries matching metrics. Arimo/Tinos/Cousine are the metric-compatible
# clones of Arial/Times New Roman/Courier New. macOS exposes Noto Sans.
_SANS_CLONE = {"windows": "Arimo", "macos": "Noto Sans", "linux": "DejaVu Sans"}
_SERIF_CLONE = {"windows": "Tinos", "macos": "DejaVu Serif", "linux": "DejaVu Serif"}
_MONO_CLONE = {
    "windows": "Cousine",
    "macos": "DejaVu Sans Mono",
    "linux": "DejaVu Sans Mono",
}
_SANS_NAMED = [
    "Arial", "Arial Black", "Helvetica", "Helvetica Neue", "Verdana",
    "Tahoma", "Segoe UI", "Calibri", "Roboto", "Liberation Sans",
]
_SERIF_NAMED = [
    "Times New Roman", "Times", "Georgia", "Liberation Serif",
]
_MONO_NAMED = [
    "Courier New", "Courier", "Consolas", "Liberation Mono",
]


def _named_matches(os_key: str) -> str:
    blocks = []
    for fam in _SANS_NAMED:
        blocks.append(
            _NAMED_MATCH.format(requested=fam, clone=_SANS_CLONE[os_key])
        )
    for fam in _SERIF_NAMED:
        blocks.append(
            _NAMED_MATCH.format(requested=fam, clone=_SERIF_CLONE[os_key])
        )
    for fam in _MONO_NAMED:
        blocks.append(
            _NAMED_MATCH.format(requested=fam, clone=_MONO_CLONE[os_key])
        )
    return "\n".join(blocks)


def bundled_fonts_dir() -> str:
    """Absolute path to persona's shipped fonts directory, in dev and when
    frozen by PyInstaller (where assets land under sys._MEIPASS/src/assets).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return os.path.join(meipass, "src", _FONTS_SUBDIR)
    here = pathlib.Path(__file__).resolve()
    src_root = here.parents[2]
    return str(src_root / _FONTS_SUBDIR)


_EMOJI = "Noto Color Emoji"


def _prefs(families: list[str], cjk: list[str]) -> str:
    # Emoji last so a glyph missing from the text faces still renders in colour.
    items = [f"<family>{f}</family>" for f in families + cjk + [_EMOJI]]
    return "".join(items)




def build_font_config(profile_dir: str, os_type: str = "linux") -> str:
    """Write a fontconfig under the profile dir exposing only the bundled fonts
    for `os_type` (plus shared CJK) and return its path. Different OS types
    expose different font sets, and named families are mapped to their bundled
    metric clones so layout stays correct.
    """
    os_key = os_type if os_type in _OS_DIRS else "linux"
    base = bundled_fonts_dir()
    common_dir = os.path.join(base, _COMMON)
    os_dir = os.path.join(base, _OS_DIRS[os_key])

    profile = pathlib.Path(profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    cachedir = profile / ".fontcache"
    cachedir.mkdir(parents=True, exist_ok=True)

    fams = _OS_FAMILIES[os_key]
    conf = profile / "fonts.conf"
    conf.write_text(
        _CONF_TEMPLATE.format(
            common=common_dir,
            osdir=os_dir,
            cachedir=str(cachedir),
            named_matches=_named_matches(os_key),
            sans_prefs=_prefs(fams["sans"], _CJK_SANS),
            serif_prefs=_prefs(fams["serif"], _CJK_SERIF),
            mono_prefs=_prefs(fams["mono"], []),
        ),
        encoding="utf-8",
    )
    return str(conf)
