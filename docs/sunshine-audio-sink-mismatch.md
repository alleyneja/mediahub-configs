# Sunshine silent audio — capture sink vs playback sink mismatch

**Resolved 2026-08-17.** Fix: `audio_sink = sink-sunshine-stereo` in
`~/.config/sunshine/sunshine.conf`.

## Symptom

No audio at the Moonlight client — in games *and* in the ES-DE UI — while
**every surface indicator says the audio stack is healthy**:

- sinks present and `state=running`
- `mute=False`, `channelVolumes=[1.0, 1.0]`
- `default.audio.sink` set correctly
- Sunshine's log shows `Opus initialized: 48 kHz, 2 channels, LOWDELAY` every
  session, including silent ones

**It presents as intermittent**, which is the worst part — audio worked for hours
on 2026-08-15/16 and during the morning of 08-17, then "stopped working" with no
config change. It was never actually fixed or broken; see Ordering below.

## Root cause

Two different sinks, and Sunshine was recording the wrong one.

- `sunshine-sink` is a **null sink** defined by
  `/etc/pipewire/pipewire.conf.d/10-sunshine-sink.conf`, created because this
  host has no physical audio output.
- **Sunshine creates its own virtual sinks** — `sink-sunshine-stereo`,
  `sink-sunshine-surround51`, `sink-sunshine-surround71` — when a **client
  connects**, and sets `sink-sunshine-stereo` as the **default output**.
- `sunshine.conf` had `audio_sink = sunshine-sink`, so Sunshine captured the null
  sink's monitor.

Result: applications played into `sink-sunshine-stereo` while Sunshine recorded
`sunshine-sink`. **Nothing connects the two.** Audio was produced perfectly and
captured from a node nobody was writing to.

Diagnostic that proves it, `pw-link -l`:

```
ES-DE:output_FL          -> sink-sunshine-stereo:playback_FL   <- apps play HERE
PCSX2:output_FL          -> sink-sunshine-stereo:playback_FL
sunshine-sink:monitor_FL -> sunshine:input_FL                  <- Sunshine records HERE
```

Two disjoint graphs. **This single command is the whole diagnosis** — go to it
first, ahead of volumes, mute states and default-sink checks, all of which look
fine while broken.

## Why it looked intermittent — the ordering dependency

PipeWire binds a stream to whatever sink is default **when the app starts**.

| App started | Default sink at the time | Result |
|---|---|---|
| **Before** a client connects | `sunshine-sink` (the only sink) | **audio worked** |
| **After** a client connects | `sink-sunshine-stereo` | **silent** |

So the fault depended entirely on whether an emulator was launched before or
after connecting Moonlight. Earlier "audio confirmed working" results were real
but lucky — those launches happened while the default was still `sunshine-sink`.
**The bug was latent the whole time**, not a regression from any later change.

## Fix

```ini
# ~/.config/sunshine/sunshine.conf
audio_sink = sink-sunshine-stereo
```

Capture the sink Sunshine itself manages and makes default, rather than the
custom null sink. Verified: game audio and ES-DE UI audio both correct, with the
game launched **after** connecting — the case that previously failed.

Graph afterwards is a single path:

```
ES-DE -> sink-sunshine-stereo -> monitor -> sunshine
```

### What did NOT work

**`virtual_sink = sunshine-sink` is ignored.** The intent was to make Sunshine
adopt the existing null sink instead of creating its own. Sunshine still created
`sink-sunshine-stereo` and still made it default. Do not retry this.

**Restarting Sunshine does not fix it** — the broken pattern reappears on the
next client connect, because the sinks are created per-session. A restart looks
promising for a moment (with no client connected there is one sink and the graph
is briefly correct) and then breaks again the instant a client connects. Do not
be fooled by the idle state.

## Residual notes

- `10-sunshine-sink.conf` and its `sunshine-sink` null sink are now **vestigial**
  — nothing in the path uses them. Left in place deliberately; removing it is
  another audio change for no benefit.
- **Inverted risk:** an app started while *no* client is connected will bind to
  `sunshine-sink`, since `sink-sunshine-stereo` does not exist yet. In practice
  PipeWire moves such streams when the default changes (ES-DE did exactly this),
  but if a specific app is ever silent while everything else works, check
  `pw-link -l` for a stream still parked on `sunshine-sink`.
- On this host use `pw-link`, `pw-dump`, `pw-cli`, `pw-metadata` with
  `XDG_RUNTIME_DIR=/run/user/1000`. **`pactl` is not installed.**

## Unrelated, seen while diagnosing

Restarting Sunshine can log `h264_nvenc`/`hevc_nvenc` "Provided device doesn't
support required NVENC features" during startup encoder probing — a race with the
outgoing process releasing NVENC. It recovers; check
`nvidia-smi --query-gpu=encoder.stats.sessionCount` and whether video actually
works before chasing it. At boot (no race) the same probe succeeds.
