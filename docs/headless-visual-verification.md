# Headless visual verification on the P400 — the screen saver, not the driver

**Status:** fixed 2026-08-18. `scripts/arcade-screenshot.sh` is the tool.

## The claim that turned out to be wrong

From 2026-08-16 the arcade plan recorded that X11 screen capture of `:1` was "dead
on the P400": setting the root window to `#00FF00` and capturing returned near-black
(`020002`), so every visual check needed a human at the projector. Three fixes were
drafted — an NvFBC grabber, a compositor on `:1`, or a debug Xorg unit that swaps
back to `xf86-video-dummy`.

None of them were needed. **X11 capture on the NVIDIA driver works fine.** What was
actually happening is that the X screen saver had blanked the display.

## The mechanism

`xset q` on `:1` reported `timeout: 600, prefer blanking: yes`. The X screen saver
blanks after 600s **of no input**. Client drawing does not reset it — and on a
headless streaming host there is no input at all unless a Moonlight client is
connected, so `:1` blanks ten minutes after every session ends and stays blanked.

A blanked screen returns a *uniform frame* from every capture path: `x11grab`, `xwd`
and NvFBC alike. It looks exactly like "capture returns black", which is why it read
as a driver-level failure.

Reproduced and confirmed both directions on 2026-08-18:

| state | root set to | capture read |
|---|---|---|
| idle 16h (blanked) | `#00FF00` | uniform `#1c1c1c` |
| `xset s activate` | `#00FF00` | uniform `#000000` |
| `xset s reset` | `#00FF00` | `#00fe00` — correct |
| not blanked | `#B22222`, `#0000FF`, `#FFFF00` | all correct, immediately |

Window contents capture correctly too — a 400x100 xclock read back as 39,134 white
pixels plus antialiased glyph greys, and a full ES-DE session captured as a normal
screenshot with 17,050 distinct colours.

## The fix

`Section "ServerFlags"` with `BlankTime 0` in `system/xorg-p400-headless.conf`.
Restart `xorg-headless.service`, then `sunshine.service`, and confirm `xset q` shows
`timeout: 0`.

This is worth having for its own sake, separately from verification: without it an
**idle ES-DE session goes black on the projector after ten minutes**, which would
have looked like a Sunshine or capture regression.

## Two things this also settled

- **Hardware GL is real on `:1`.** ES-DE logs `GL renderer: Quadro P400/PCIe/SSE2`,
  `GL version: 3.3.0 NVIDIA 580.173.02` — not llvmpipe, not software.
- **`Couldn't release NvFBC context from current thread:` in `sunshine.log` is
  noise.** It fires on essentially every session, including ones that streamed
  correctly all evening. Do not chase it.

## Usage

    ~/arcade-screenshot.sh [out.png]

Prints the mapped windows on `:1`, a colour histogram, and a verdict of
CONTENT PRESENT / NEARLY BLANK / UNIFORM FRAME. It warns if the screen-saver timeout
is non-zero, so a blank capture can never silently be mistaken for a broken app again.

`picom` was installed on 2026-08-18 while testing the compositor theory. It is **not**
required and is not running; it can be removed if it is ever in the way.
