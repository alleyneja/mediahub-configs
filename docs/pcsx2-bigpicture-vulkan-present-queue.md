# PCSX2 Big Picture fails to start while Sunshine is streaming — ✅ RESOLVED 2026-08-17

**Resolved by fitting the mini-DP dummy plug and moving Xorg to
`xorg-p400-headless.conf` with NvFBC capture** (commits `6391791`, `0a094f4`).
No PCSX2 change and no change to Sunshine's encoder were needed. Details in the
"Resolution" section at the end; the original investigation is kept below because
the reasoning that led to the fix is worth preserving.

## 2026-08-15 — `VK: Failed to find an acceptable present queue`

**Status when written: unresolved.** Workaround known, root cause inferred rather
than proven. **The inference turned out to be correct — see Resolution below.**

**Symptom:** launching PCSX2 from the Moonlight app list with `-bigpicture` shows:

> Failed to create render device. This may be due to your GPU not supporting the
> chosen renderer (Vulkan), or because your graphics drivers need to be updated.

```
[0.6281] VK: Failed to find an acceptable present queue.
[0.6281] Failed to create GS device
[0.6318] GS failed to open.
```

**The flags are not the problem.** Verified from the process command line that
`-bigpicture -fullscreen` reaches PCSX2, so `apps.json` reloads correctly.

## Reproduction is clean in both directions

| Sunshine state | `-bigpicture` launch |
|---|---|
| Streaming (client connected) | **fails**, every time |
| Idle (no session) | **works**, Big Picture renders fully |

With Sunshine idle the same command initialises without complaint:

```
[0.7259] Vulkan Graphics Driver Info:
[0.7259] Driver 580.692.128 / Vulkan 1.4.312
[0.7259] NVIDIA Quadro P400
[0.5963] No GPU requested, using first (Quadro P400)
```

## Why Big Picture specifically

The plain PCSX2 game list is ordinary Qt widgets and needs no GPU, so it always
launches. **Big Picture requires a render device immediately at startup**, which
is where it dies. This is also why booting a game the normal way appeared to work
earlier — that render device was created while Sunshine was idle.

## Both programs want the same GPU

PCSX2 renders and presents on the P400 (`using first (Quadro P400)`), while
Sunshine encodes on the same card:

```
Info: Found H.264 encoder: h264_nvenc [nvenc]
Info: Creating encoder [h264_nvenc]
```

**Caveat on the diagnosis:** "acceptable present queue" is a Vulkan WSI question
about queue families supporting presentation to a surface, not a GPU-load
question, so contention alone should not cause it. Best current reading is that
NVIDIA presentation is already on a degraded path under `xf86-video-dummy` (no
DRM connector is enabled, nothing reaches a scanout engine) and an active NVENC
session tips it over. **Not proven.**

## Workaround that works today

The render device only fails to be *created* during a stream; once created it
survives a client attaching. So:

1. Start PCSX2 with `-bigpicture` while Sunshine is idle.
2. Connect Moonlight to **Desktop**.

You land in the pad-navigable UI. Confirmed by the same mechanism keeping a
running game rendering when a client connected mid-session.

## Candidate fixes, in order of preference

1. **Fit the mini-DP dummy plug and switch back to `xorg-p400-headless.conf`.**
   A real connector makes the X screen genuinely NVIDIA-driven so presentation
   stops being a fallback path. Free — this swap is already planned. **Try first.**
2. **Move Sunshine's encoder to the Intel iGPU (VAAPI/QuickSync on HD 630).**
   Frees the P400 entirely: render on the Quadro, encode on the iGPU. Addresses
   the contention directly and does not depend on the display driver. Cost is
   that the CUDA 12 NVENC source build stops being load-bearing.
3. Switch PCSX2's renderer away from Vulkan. Least attractive — there is no
   hardware GL under the dummy driver, so this trades a working GPU path for a
   software one.

## Untested and important

**Does booting a game from the plain game list also fail during a session?** If
render-device creation always fails mid-stream, PCSX2 cannot start *any* game
over Moonlight, and the working Budokai session on 2026-08-15 only succeeded
because it was launched before the client connected. This determines whether the
issue is a Big-Picture annoyance or a fundamental blocker.

## Current state

`sunshine/apps.json` is reverted to the plain `flatpak run net.pcsx2.PCSX2`
so the entry is not outright broken. Re-add `-bigpicture -fullscreen` once the
dummy plug is fitted and this retests clean.

---

## Resolution — 2026-08-17

**The inferred diagnosis was right.** The failure was NVIDIA's presentation path
under `xf86-video-dummy`, not GPU contention between PCSX2 and Sunshine's NVENC
session. Once a real DRM connector existed, the failure vanished with no change
to PCSX2 and no move of Sunshine's encoder to the Intel iGPU.

**What changed** (both already committed for the NvFBC work, not for this bug):

- mini-DP `DP1080P60` dummy plug fitted on **DFP-5**
- `xorg-headless.service` → `xorg-p400-headless.conf`, `ConnectedMonitor DFP-0`
  override dropped so NVIDIA auto-selects DFP-5
- `nvidia_drm modeset=0`, GDM disabled, `capture = nvfbc`
- `ExecStartPost` changed `chvt 1` → `chvt 7` so NvFBC keeps an active display

**Verification, in the direction that used to fail** (connect the client *first*,
then launch — the order that failed every time on 08-15):

1. Moonlight connected to the **Desktop** tile; confirmed live with
   `nvidia-smi --query-gpu=encoder.stats.sessionCount,encoder.stats.averageFps`
   → `1, 30`, and visually with `xsetroot -solid "#B22222"` showing red on the
   projector. **That `xsetroot` test is the fast capture check** — under the old
   config it changed not one pixel.
2. `flatpak run net.pcsx2.PCSX2 -bigpicture -fullscreen` on `DISPLAY=:1`.
3. Result: **0** occurrences of `present queue` / `Failed to create GS device`,
   full `Vulkan Graphics Driver Info` init on the Quadro P400 at `[0.7823]`, and
   a `1852x1011` `PCSX2 v2.6.3` window mapped on `:1`. Process alive well past
   the `[0.6281]` mark where it used to die. Confirmed on the projector by Jay.

**Consequences:**

- The "**untested and it matters**" question — whether booting a game from the
  plain game list also failed mid-stream — is moot. Big Picture is the harder
  case (it demands a render device immediately at startup); if it succeeds, the
  plain list does too.
- **The ES-DE frontend is unblocked.** It was deferred specifically because "a
  frontend needs its own GPU context and may hit the same present-queue failure."
  That risk is retired. Caveat: this was proven on the **Vulkan** path, and ES-DE
  is expected to render through SDL + **OpenGL** — the fix is at the connector /
  presentation level and should cover both, but confirm at install time.
- **Do not** move Sunshine's encode to the Intel iGPU. That was step (2) of the
  planned fix and is now unnecessary.
- A black screen on the **Desktop** tile is **normal** — bare openbox with no
  wallpaper. Check `xwininfo -root -children` on `:1`; if the only entries are
  Sunshine's 16x16 tray icon and openbox's 1x1 helpers, there is genuinely
  nothing to draw. Do not re-diagnose it as a capture regression.
