import json
import os
import pathlib

CONTENT_SCRIPT = """\
const PREFIX = {prefix};
function apply() {{
  if (!document.title.startsWith(PREFIX)) {{
    document.title = PREFIX + document.title;
  }}
}}
apply();
const head = document.head || document.documentElement;
new MutationObserver(apply).observe(head, {{
  subtree: true, childList: true, characterData: true,
}});
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-title",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["title.js"],
            "run_at": "document_start",
            "all_frames": False,
        }
    ],
}


def build_title_extension(profile_name: str, base_dir: str) -> str:
    """Generate a tiny unpacked extension that prefixes the window/tab title
    with the profile name, so the taskbar (which shows the window title) tells
    personas apart. The lxqt-panel taskbar ignores a .desktop Name= for the
    button label and uses the window title, so the title itself must carry it.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"[{profile_name}] "
    (ext_dir / "title.js").write_text(
        CONTENT_SCRIPT.format(prefix=json.dumps(prefix)), encoding="utf-8"
    )
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
