# PS-330 — what a page receives from `enumerateDevices()` on Firefox

**Taken:** 2026-09-07, in the worker container.
**Tree:** `db6d528` (`origin/main` at the time of the reading, tree clean).
**Engine:** `firefox-20` (the build on disk; `installed_builds()` → `['firefox-20']`).
**Instrument:** `scripts/ps330_ff_devices_reading.py`, which launches through the
SHIPPING launch path (`spawn_browser`, `in_process=True`) and reads the resolved
promise's payload. **Guarded** by `tests/test_ps330_ff_mediadevices_live.py`.

---

## §0 — Venue, and how it differs from what the ticket recorded

The ticket recorded this container as incapable of launching a browser at all.
Two of its three findings had changed by the time the work ran, and one had not:

| Fact | Ticket's reading | This container |
|---|---|---|
| engine binary | absent (`~/.persona/engine/` held only `builds.json`) | **PRESENT** — `firefox-20_151.0_20260817150018` in the invisible-playwright cache; `is_invisible_installed()` → `True` |
| `xvfb-run` | not installed | **installed by this session** (`apt-get install xvfb`) |
| `/dev/snd`, `/dev/video*` | absent | **still absent** |

So the launch legs (AC1, AC2) became executable, and the AC3 constraint did not
move: no host in this fleet has a webcam.

---

## §1 — The reading

Three launches, three distinct seeds, on a secure loopback origin
(`window.isSecureContext === true`, asserted — `enumerateDevices` is gated on
one, and a reading taken in a non-secure context would measure the context).

| profile | seed | permission | `kindCounts` | `deviceId`/`groupId` | `label` |
|---|---|---|---|---|---|
| `ps330-probe-0` | 2952235449 | `prompt` (shipped default) | `{audioinput: 1, videoinput: 1}` | `""` / `""` on both devices | length 0 |
| `ps330-probe-1` | 2635390347 | `prompt` (shipped default) | `{audioinput: 1, videoinput: 1}` | `""` / `""` on both devices | length 0 |
| `ps330-probe-granted` | 2278800404 | **`granted`** (test-side overlay) | `{audioinput: 1, videoinput: 1}` | `""` / `""` on both devices | length 0 |

`Function.prototype.toString` on `enumerateDevices` → `function enumerateDevices() {\n    [native code]\n}`.

**Controls that fired**, so a null would have been a fact about the engine
rather than about the instrument:

- **channel** — `1+1` → `2` on every launch, evaluated before anything else.
- **API presence** — `navigator.mediaDevices` and `.enumerateDevices` both
  present, recorded separately from the list, so *"the API is missing"* and
  *"it enumerated zero devices"* cannot be confused.
- **permission overlay** — `navigator.permissions.query` reported `granted` for
  both camera and microphone on the third launch, so the overlay is proved to
  have taken effect rather than assumed.
- **seed distinctness** — three different `fingerprint_seed_value`s, asserted.
  ⚠️ An earlier draft of the instrument read `profile.seed`, which does not
  exist, and recorded `seed: null` for every profile. That reading would have
  *looked* like the answer while establishing nothing, since two identical
  rosters prove nothing about linkability if both rows might be the same
  profile. Fixed before the committed run.

---

## §2 — What it establishes

**1. The roster is not this host's device list.** A host with no `/dev/snd` and
no `/dev/video*` is told it has one microphone and one camera. Whatever that
roster is, it is synthesised — there is no host fact here for a spoof to
displace.

**2. `deviceId`/`groupId` are not per-profile identifiers.** They are the empty
string on every device of every profile. This is the axis the committed
artifacts could not speak to: PS-135's and PS-290's 20 recordings establish that
the *kind count* is constant, but the kind count is the weak axis — "1 mic, 1
camera" is what most real machines look like. The ids are the linkability
question, and nothing had ever read them on this engine.

**3. The empty ids are a CONSTANT, not a pre-permission placeholder.** This is
the reading that took a third launch, and it is the one that makes (2) mean
anything. A real browser blanks labels and device ids until permission is
granted, so an empty id under the shipped `prompt` default is ambiguous. With
permission genuinely `granted` the ids are still empty. (PS-312 recorded the
general form of this trap: *a permission-gated web API measured under its
shipped `prompt` default measures the doorhanger, not the engine.*)

**4. `enumerateDevices` renders as `[native code]`.** That is the concrete price
of shipping a spoof, measured rather than assumed.

### The conclusion

Persona ships **no** Firefox mediaDevices spoof, and the matrix cell moves to
`not_covered_recorded`. A JS override would replace a native-rendering method
with one a detector can see, in order to change a value that leaks no host fact
and carries no cross-profile identifier. That is a net loss under Invariant #0 —
the same shape PS-312 established for geolocation.

The reason is written into `invisible_launch.py` beside the `_install_spoof`
calls, where a reader asks why there is a webgl spoof, an audio spoof and a
locale spoof but no device one. It is re-read out of the tree on every run by
`test_recorded_reasons_still_in_tree`, and the absence itself is guarded from
both sides — emitted source AND spoof registry — by
`test_the_recorded_device_absence_is_still_an_absence`.

---

## §3 — ⛔ Bounds. What this reading does NOT establish.

**1. AC3's second leg is UNMEASURED and is NOT inferred.**

- **Measured leg:** a host with **no** audio/video devices. That is this
  container, and it is stated in the reading itself
  (`ac3_leg_measured`, plus per-reading `host_devices`).
- **Unmeasured leg:** a host **with** real audio/video devices. No such host
  exists in this fleet. Whether a machine with four real microphones would
  still be told it has one is **not established**.

The direction that carries the finding is the one that was measured — a host
with nothing is told it has two — so "the roster does not track *this* host" is
a reading. "The roster tracks no host" is not, and is not claimed. **If a
reading on a device-carrying host ever shows the roster tracking that host, it
is a host-fact leak and the cell must be restated.** `test_the_measured_leg_is_the_device_less_host`
asserts which leg was taken so a later reader cannot mistake one for the other.

**2. One engine build.** `firefox-20` only. The committed artifacts cover
builds firefox-20/21/25/26 on the kind-count axis, but the *id* axis is read
here on one build.

**3. Not a proxied launch.** These are direct launches; the device roster is not
a proxied-exit question and a relay would have been an extra variable.

**4. Frequency unmeasured.** `tests/fixtures/checker-pages/sannysoft.txt:180`
shows `bot.sannysoft.com` reads `navigator.mediaDevices`, so the vector is on a
checker's surface. How often a real fingerprinting script reads it is not known,
and this reading does not speak to it.

**5. No priority claim.** Nothing measured here shows a host fact escaping, so
the ticket's bound #2 stands: the priority was not raised.

---

## §4 — Files

- `reading.json` — the three readings, with controls, host device inventory and
  the AC3 leg labels. Labels are recorded as `label_len`/`label_empty` only;
  ⛔ **no label string is ever written here**, per the restriction `probes.py`
  states for its own device probe (*"labels are user-identifying and are
  deliberately NOT recorded into a file the operator may share"*).
