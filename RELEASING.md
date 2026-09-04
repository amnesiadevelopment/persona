# Releasing persona and Personium

`amnesiadevelopment/persona` publishes **two different kinds of release from one
repository**: the persona **application** and the Personium **browser engine**.
The application and the engine only work as a pair, so they are versioned and
published together — but the two updaters that consume them are separate, run
unattended on operator machines, and **must never be able to select each
other's release**.

This file is the scheme that keeps them apart. Read it before cutting either
kind of release. Getting it wrong is not cosmetic — see
[Why this matters](#why-this-matters) below.

---

## The scheme, in one table

|                | persona (application)            | Personium (browser engine)               |
| -------------- | -------------------------------- | ---------------------------------------- |
| Tag            | `v<X.Y.Z>` — e.g. `v3.0.2`       | `personium-<chromium version>` — e.g. `personium-152.0.7977.75` |
| Marked as      | ordinary release                 | **prerelease**                            |
| Linux asset    | `persona-x86_64.AppImage`        | `personium-<version>-linux-x86_64.AppImage` |
| Windows asset  | `persona-windows-setup.exe`      | `personium-<version>-windows-x86_64.zip`  |
| macOS asset    | `persona-macos.dmg`              | `personium-<version>-macos-x86_64.dmg`    |
| Discovered by  | the `releases/latest` redirect   | `git/matching-refs/tags/personium-`, then that tag's release |
| Consumed by    | `src/services/app_update/updater.py` | `src/services/engine/updater.py`      |

### Application releases

* Tag **`v<APP_VERSION>`**, matching `APP_VERSION` in
  `src/services/app_update/updater.py` exactly — `release.yml`'s preflight job
  fails the release if they disagree.
* Published as an **ordinary** (non-prerelease) release, because the app
  updater resolves the current version through the rate-limit-free
  `https://github.com/amnesiadevelopment/persona/releases/latest` redirect.
  **There is exactly one `releases/latest` pointer per repository and the
  application owns it.**
* Assets are produced by `.github/workflows/release.yml` and their names are
  fixed per platform (`app_update/updater.asset_name()`), plus the
  `checksums.txt` / `*.sha256` sidecars the self-updater verifies against.

### Engine (Personium) releases

* Tag **`personium-<chromium version>`** — the `personium-` prefix over the
  bare Chromium version, e.g. `personium-152.0.7977.75`. The prefix makes the
  release unmistakable against the app's `vX.Y.Z` tags, and the version after
  it keeps engine releases sortable among themselves.
* Published as a **prerelease**. This is what keeps engine releases off the
  `releases/latest` pointer the application updater reads. *Verified
  empirically, 2026-09-04*: `neovim/neovim`'s `nightly` prerelease was 11 days
  newer than `v0.12.5`, and both the `releases/latest` redirect and
  `/releases/latest` in the API still resolved to `v0.12.5`. GitHub's
  documented exclusion of prereleases from "latest" holds.
* Assets **must** be named `personium-<version>-<os>-x86_64.<ext>` exactly as
  in the table above. Publish a `digest`-bearing asset — GitHub computes the
  `digest` field the engine updater verifies against, so nothing extra is
  needed, but an asset the API reports no digest for **will be refused**
  (`EngineUnverifiable`; see PS-49). That is deliberate and is not to be
  relaxed.
* A release must **list its assets**. There is no predictable-URL fallback any
  more: an engine release whose asset list carries nothing for the running OS
  is refused, not guessed at (see [The removed fallback](#the-removed-fallback)).
* **Push the tag, then publish the release.** Discovery reads tags first (see
  below), so a `personium-` tag that exists with no release behind it is a tag
  the updater will try and fail to resolve. It descends past it to the next
  engine release rather than reporting nothing, but only up to five tags — so
  do not leave a run of engine tags without releases.
* macOS engine builds are deferred, but the macOS asset **rule** above is
  already in force, so a later macOS release needs no change to persona.

### How the engine updater finds a release

Not by walking `/releases`. It asks
`api.github.com/repos/amnesiadevelopment/persona/git/matching-refs/tags/personium-`,
which the **server** filters by that prefix, takes the highest version among
the tags it returns, and then reads that one release through
`/releases/tags/personium-<version>` — the same by-tag request the rollback path
makes, so an install and a rollback cannot select different assets.

The obvious alternative is wrong here, and it is worth recording why so nobody
"simplifies" it back. The Firefox updater enumerates `/releases?per_page=30` and
filters client-side, and that is fine on an upstream where every release is a
candidate. On **this** repository the releases list is sorted
newest-created-first across **both kinds** of release, and GitHub offers no
server-side "prereleases only" filter — so an engine release competes for those
30 slots against every application release. Measured 2026-09-04: 94 releases
over 65 days (1.45/day), so an engine prerelease published today drops off page
1 after a **median of 17 days**, after which every installed persona reports
"could not reach GitHub releases" while GitHub is perfectly reachable. Raising
`per_page` moves that wall (~65 days at 100) rather than removing it.

The tag-ref endpoint has no such wall: it is a genuine server-side prefix match
(`tags/v` on this repo returns exactly the 98 `v*` tags), it is **unpaginated**
and ignores `per_page` (`rails/rails` answers 552 refs and `python/cpython` 649
in one document, with no `Link` header), and an unmatched prefix answers
`200 []`. It is also **cheaper**: a ref is ~411 bytes and only engine tags are
counted, so the document is 5 bytes today and ~20 KB after fifty engine
releases, plus one ~26 KB release document — against the 493 KB the releases
list had reached. That matters because this check runs hourly and unattended
over a connection persona is designed to route through Tor.

Two unauthenticated calls per check, against a 60/hour per-IP allowance.
**Do not add a token** to "solve" a rate limit — see PS-216.

---

## Why this matters

Both updaters read the same repository, and before PS-305 they could not tell
each other's releases apart. Two concrete failures, both reproduced against the
tree at `b1f00e9`:

1. **The engine would have installed the application.** The engine's Linux
   asset rule was `name.endswith("x86_64.AppImage")`, and the app's own Linux
   asset is `persona-x86_64.AppImage`. A Linux engine install would have
   selected the persona AppImage and installed the *application* as the browser
   engine.

2. **Worse: an engine release would have been read as a newer application.**
   The app updater compares release tags with `is_newer`, imported from the
   engine updater. With the app at `3.0.2` and an engine at `152.0.7977.75`,
   `is_newer("152.0.7977.75", "3.0.2")` is `True` and the reverse is `False` —
   so every installed persona would have been offered the engine as an app
   update, and once a client recorded that version it could never see a real
   app release as newer again. A **one-way trap** on the operator's machine.

The scheme above is defended in code by **three independent guards**, so a
release marked wrongly by hand still cannot cross the line:

* **The engine's tag filter** — `engine/updater.is_engine_tag()`. Only a
  `personium-`-tagged release is a candidate; application releases are ignored
  by the engine updater whether or not they are prereleases.
* **The engine's asset rule** — `engine/updater._asset_matches()` requires the
  `personium-` filename prefix **and** the per-OS suffix. Each anchor alone
  excludes every application asset, so neither is load-bearing by itself.
* **The application's tag filter** — `app_update/updater.is_app_release_tag()`.
  Only a `vX.Y.Z`-shaped tag is an application release. This is deliberately
  *independent of the prerelease marking*: if an engine release is published as
  an ordinary release by mistake and takes the `releases/latest` pointer, the
  app updater still refuses it rather than offering it to operators as an
  application update.

Discovery adds a fourth, **structural** layer on the engine side: because the
tag-ref endpoint filters by `personium-` on the server, an application release
is not merely outranked in the document the engine updater reads — it is not in
it at all. That is a consequence of the discovery mechanism rather than a guard
in its own right, which is why the three above are still each required to hold
alone.

`tests/test_release_channel_separation.py` presents both kinds of release
together and asserts each updater selects its own and rejects the other's. It
fails if either selection rule is loosened, and
`test_engine_is_found_behind_a_hundred_application_releases` fails if discovery
goes back to reading a bounded page of the releases list.

---

## The removed fallback

`engine/updater.appimage_url_for()` used to build a predictable Linux AppImage
download URL from a tag, for releases whose JSON listed no assets. It was
removed with PS-305 rather than re-pointed, deliberately:

* It hardcoded an `adryfish/fingerprint-chromium` download URL and could not
  survive the move to our own repository as written.
* It never bought the availability it cost. It fired only when the asset
  matcher found nothing, and PS-49 measured that on every such upstream release
  the URL it formatted 404'd — it rescued no real release, it only widened what
  persona would install without looking.
* We cut our own releases now. A release with no matching asset is a **broken
  release**, and the right answer to one is a refusal a person can see and fix,
  not a guessed URL. A guessed URL also carries no digest, so it would land on
  the fail-closed digest refusal anyway.

If a future release genuinely needs a predictable URL, add it as a *named*
per-OS rule derived from this file's asset table — not as a Linux-only special
case.

---

## Cutting a release

**Application:** bump `APP_VERSION` in `src/services/app_update/updater.py`,
commit, and push a `v<APP_VERSION>` tag. `.github/workflows/release.yml` builds
each OS and publishes the release. (`engine-autoupdate.yml` does this
automatically for Firefox-engine bumps.)

**Engine:** build the per-OS artifacts, name them per the table above, and
publish them under a `personium-<version>` tag **with the prerelease box
ticked**. Building and packaging the engine artifacts is not automated yet —
see PS-299 for the patch rebase and compile.
