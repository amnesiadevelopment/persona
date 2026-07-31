<p align="center">
  <img src="src/assets/icon.png" width="120" alt="persona logo" />
</p>

<h1 align="center">persona</h1>

<p align="center">
  A local, single-operator anti-detect browser manager.<br/>
  Run many accounts by hand, each with its own fingerprint — without linking them.
</p>

<p align="center">
  <a href="https://github.com/amnesiadevelopment/persona/releases/latest"><img alt="latest release" src="https://img.shields.io/github/v/release/amnesiadevelopment/persona?color=97ca00&label=release"></a>
  <img alt="platforms" src="https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-333">
  <img alt="engines" src="https://img.shields.io/badge/engines-Chromium%20%C2%B7%20Firefox-333">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

<p align="center">
  <a href="https://github.com/amnesiadevelopment/persona/releases/latest"><b>⬇ Download for Windows · macOS · Linux</b></a>
</p>

---

persona gives you a desktop UI for creating browser personas — each with a
deterministic fingerprint, its own proxy, cookies, bookmarks, OS spoofing, and
optional mTLS client certificate — so you can work multiple accounts by hand
without them being linked.

Each persona seeds its fingerprint from the profile name, so the same persona
looks identical across restarts while distinct personas look unrelated.

## Two engines

persona launches one of two browser engines per profile:

- **Chromium** — [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium),
  an ungoogled-chromium build whose fingerprint (canvas, WebGL, audio, fonts,
  hardware, platform) is seeded deterministically. Extensions add the last-mile
  spoofs (locale, audio/WebGL entropy, mobile device signals).
- **Firefox** — a patched Firefox 150+ where the spoofing is done at the C++
  level (no CDP/webdriver tells). Best when you want the most invisible profile.

## Download

Grab the latest build for your OS from the
[latest release](https://github.com/amnesiadevelopment/persona/releases/latest):

- **Linux** — `persona-x86_64.AppImage`
  ```sh
  chmod +x persona-x86_64.AppImage
  ./persona-x86_64.AppImage
  ```
  Or one command: `curl -fsSL https://raw.githubusercontent.com/amnesiadevelopment/persona/main/install.sh | sh`
- **Windows** — `persona-windows-setup.exe` (per-user install, no admin prompt).
- **macOS** — `persona-macos.dmg`.

The UI runtime is bundled; each browser engine downloads itself on first use.
persona updates itself in place — most updates swap only the app code and take
seconds.

## Features

- **Per-persona fingerprint** — canvas, WebGL, audio, fonts, hardware, and
  platform are spoofed deterministically from the profile name: stable across
  restarts, distinct (and mutually unlinkable) between personas.
- **Two engines per profile** — pick Chromium or the patched Firefox per persona
  (see above).
- **Geo follows the proxy** — locale, timezone, and geolocation track the
  proxy's country/coordinates so they match the exit IP. With no proxy the
  identity is pinned to a coherent US-English one (the host locale never leaks).
- **Named proxy store** — SOCKS5/HTTP proxies as reusable entities with a geo/IP
  check and one-click rotation; assigned to profiles by name. DNS and WebRTC are
  kept inside the tunnel, and QUIC is disabled behind a proxy so realtime apps
  (Google Sheets/Docs) don't hang.
- **mTLS client certificates** — add a `.p12`/`.pfx` in the Certificates tab and
  assign it to a profile to log into an admin site. The certificate is presented
  **only** to that site's address — never to any other site, so it can't become
  a fingerprint that identifies you elsewhere — and it stays inside persona, never
  imported into the OS certificate store. Available on every OS with the Chromium
  engine; Firefox certificates are Linux-only for now.
- **Resolution decoupling & HiDPI** — a profile can report any screen resolution
  while the real window stays native and readable, with no window-size tell.
- **Mobile personas** — Android/iOS profiles built from real device presets
  (UA, screen, touch, Client-Hints).
- **Per-OS bundled fonts** — an isolated font set per spoofed OS so the font
  fingerprint is identical on any host and CJK text renders without tofu.
- **Cookie import/export**, **bookmarks & pools**, **profile import/export**,
  **search-engine choice**, and **SSH-through-a-profile's-proxy**.
- **Local automation API (opt-in)** — a local server, off by default, exposes
  profile management and (per profile) a CDP port, so an MCP client such as
  Claude can create, edit, and drive personas. No browser is reachable until you
  turn the server on.
- **Quality-of-life** — dark theme, profile search, taskbar labelling, and a
  header tag (persona@windows / persona@mac / persona@linux) so a shared
  screenshot says which machine it came from.

## Limitations (honest)

persona aims for a clean, consistent, human-like profile — not magic invisibility.

- **The Chromium engine's spoofing is partly JS-layer.** Geo/locale and the
  audio/WebGL entropy are MAIN-world overrides, which a thorough checker (e.g.
  pixelscan) can flag as "masking detected". The Firefox engine spoofs at the
  C++ level, which is invisible — prefer it when that matters.
- **Software rendering in a VM.** With no GPU, WebGL comes from a software
  renderer: the vendor/renderer strings are spoofed plausibly and personas are
  mutually unlinkable, but the output doesn't match a specific real GPU.
- **Real anti-fraud is layered.** Passing a single fingerprint checker is not the
  same as passing systems that score TLS + IP + headers + behaviour together.
  Proxy quality and human-like behaviour matter as much as the fingerprint.

## Development

```bash
python -m venv .venv
.venv/bin/pip install --prefer-binary -r requirements.txt
.venv/bin/python -m src.main
```

## Configuration

Configuration is via environment variables (or a `.env` file).

| Variable | Default | Description |
| --- | --- | --- |
| `PERSONA_PROFILES_FILE` | `profiles.json` | Profile metadata store. |
| `PERSONA_PROXIES_FILE` | `proxies.json` | Proxy store. |
| `PERSONA_BOOKMARKS_FILE` | `bookmarks.json` | Bookmark/pool store. |
| `PERSONA_CERTS_FILE` | `certificates.json` | Certificate store. |
| `PERSONA_DATA_DIR` | `persona_data` | Per-profile browser data. |
| `PERSONA_LOG_DIR` | `logs` | Daily log files. |
| `PERSONA_LOG_LEVEL` | `INFO` | Python log level. |
| `PERSONA_PROXY_TIMEOUT` | `10` | Proxy check timeout, seconds. |
| `PERSONA_API_HOST` | `127.0.0.1` | Local API host. |
| `PERSONA_API_PORT` | `8000` | Local API port. |

All runtime data lives under `~/.persona/` and is never touched by (un)install.

## Security

See [`SECURITY.md`](.github/SECURITY.md) for how to report a vulnerability
privately.

## License

MIT — see [`LICENSE`](LICENSE).
