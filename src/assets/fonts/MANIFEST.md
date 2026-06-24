# persona bundled fonts (per-OS sets)

Isolated via per-profile FONTCONFIG_FILE so the browser sees ONLY these,
independent of the host. Different os_type exposes a different set, so the
font fingerprint and canvas hash vary by spoofed OS. Shipped inside the
release binary (PyInstaller bundles src/assets), not committed to git.

- common/   — Noto Sans/Serif CJK (no-tofu for zh/ja/ko), shared by all OS
- windows/  — Arimo (Arial), Tinos (Times New Roman), Cousine (Courier): croscore, metric-compatible with Windows defaults
- macos/    — Noto Sans + DejaVu Serif (distinct rendering)
- linux/    — DejaVu Sans/Serif/Mono (authentic Linux set)

Sources: fonts-croscore, fonts-noto-cjk, fonts-noto-core, fonts-dejavu
(all free / OFL / Apache-2.0).
