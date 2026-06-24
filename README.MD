<p align="center">
  <img src="src/assets/icon.png" width="128" alt="persona logo" />
</p>

# persona

persona is a local, single-operator anti-detect browser manager. It gives you a
desktop UI for creating browser personas — each with a deterministic
fingerprint, its own proxy, cookies, bookmarks, and OS spoofing — so you can
work multiple accounts by hand without them being linked.

It launches [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium)
(an ungoogled-chromium build) and seeds a per-persona fingerprint from the
profile name, so the same persona looks identical across restarts while distinct
personas look unrelated.

## Download

**Linux (x86_64)** — one command:

```sh
curl -fsSL https://raw.githubusercontent.com/amnesiadevelopment/persona/main/install.sh | sh
```

This downloads the latest release binary, installs it to `~/.local/bin/persona`,
and you launch it with `persona`. Nothing else to install — the UI runtime and
fonts are bundled inside the binary, and the browser engine downloads itself on
first run.

Prefer a direct download? Grab `persona-linux-x86_64` from the
[latest release](https://github.com/amnesiadevelopment/persona/releases/latest), then:

```sh
chmod +x persona-linux-x86_64
./persona-linux-x86_64
```

Or download the **AppImage** (`persona-x86_64.AppImage`) for a self-contained
desktop app with its own menu icon:

```sh
chmod +x persona-x86_64.AppImage
./persona-x86_64.AppImage
```

> Windows and macOS builds are planned. For now persona targets Linux x86_64.

## Features

- **Per-persona fingerprint** — canvas, WebGL, audio, fonts, hardware, and
  platform are spoofed deterministically from the profile name: stable across
  restarts, distinct (and mutually unlinkable) between personas. Audio and
  WebGL pixel readback get per-profile entropy from MAIN-world extensions so two
  same-OS personas don't share a hash.
- **Geo follows the proxy** — locale, timezone, and `navigator.geolocation`
  track the proxy's country/coordinates so they match the exit IP.
- **Named proxy store** — SOCKS5/HTTP proxies as reusable entities with a
  geo/IP check; assigned to profiles by name; a local relay bridges
  username/password SOCKS5 that Chromium can't pass directly. DNS and WebRTC are
  kept inside the tunnel.
- **Per-OS bundled fonts** — an isolated font set per spoofed OS (Windows/macOS/
  Linux) so the font fingerprint is identical on any host and CJK text renders
  without tofu.
- **Cookie import/export** — file-based (Cookie-Editor JSON), with a persistent
  per-profile import status.
- **Bookmarks & pools** — reusable bookmarks grouped into pools, attachable to
  profiles and written to the bookmarks bar on launch.
- **First-run onboarding** — a short walkthrough plus an engine download with a
  live progress bar.
- **Local automation API (opt-in)** — a local server, off by default, exposes
  profile management and (when enabled per profile) a CDP port, so an MCP client
  such as Claude can create, edit, and drive personas. No browser is reachable
  until you turn the server on.
- **Quality-of-life** — dark theme, default search engine, profile search by
  name/proxy/OS, and taskbar labelling (each window shows its persona name).

## Limitations (honest)

persona aims for a clean, consistent, human-like profile — not magic invisibility.

- **The browser runs software rendering.** With no GPU (e.g. inside a VM) WebGL
  pixels come from a software renderer. The vendor/renderer strings are spoofed
  plausibly and personas are mutually unlinkable, but the output does not match a
  specific real GPU. Looking like specific hardware needs a real GPU or an
  engine-level (C++) patch.
- **Spoofing is JS-layer where the engine doesn't cover it.** Geo/locale/title
  and the audio/WebGL entropy are MAIN-world overrides, which a thorough checker
  (e.g. pixelscan) can flag as "masking detected". Engine-level (C++) spoofing is
  invisible; that is a deliberate non-goal here.
- **Real anti-fraud is layered.** Passing a single fingerprint checker is not the
  same as passing systems that score TLS + IP + headers + behaviour together.
  Proxy quality and human-like behaviour matter as much as the fingerprint.

## Installation

```bash
python -m venv .venv
.venv/bin/pip install --prefer-binary -r requirements.txt
# fetch the bundled fonts (not stored in the repo):
./download-fonts.sh
```

fingerprint-chromium is expected at `~/fpchrome/fpchrome.AppImage` (persona can
download it for you on first run).

## Running

```bash
.venv/bin/python -m src.main
```

## Configuration

Configuration is via environment variables (or a `.env` file).

| Variable | Default | Description |
| --- | --- | --- |
| `PERSONA_PROFILES_FILE` | `profiles.json` | Profile metadata store. |
| `PERSONA_PROXIES_FILE` | `proxies.json` | Proxy store. |
| `PERSONA_BOOKMARKS_FILE` | `bookmarks.json` | Bookmark/pool store. |
| `PERSONA_DATA_DIR` | `persona_data` | Per-profile browser data. |
| `PERSONA_LOG_DIR` | `logs` | Daily log files. |
| `PERSONA_LOG_LEVEL` | `INFO` | Python log level. |
| `PERSONA_PROXY_TIMEOUT` | `10` | Proxy check timeout, seconds. |
| `PERSONA_API_HOST` | `127.0.0.1` | Local API host. |
| `PERSONA_API_PORT` | `8000` | Local API port. |

## Layout

- `persona_data/<profile-name>/` — browser data per profile (never committed)
- `logs/persona_YYYYMMDD.log` — daily log file

## License

MIT — see [`LICENSE`](LICENSE).
