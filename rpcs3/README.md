# RPCS3 on mediahub-r9

- `custom_configs/config_BLUS30377.yml`: MW2 per-game config. Resolution Scale 200 (1080p internal), Anisotropic 16x,
  Disable Vertex Cache on (split-screen fix). Live path: `~/.config/rpcs3/custom_configs/`.
- `loadtest/`: FPS recorder + load ladder used for alleyneja/mediahub-issues#52 (1 Hz FPS/CPU/GPU CSV).
  `loadtest_res.py` is the minimal "watch relaunch, record 12 min" variant; `res200-summary.md` is its result
  (mean 60.0 FPS, min 46.7, GPU 11%).
- Gotcha: the watcher's "already applied?" check must match the key line, not the file's comment header.

## Global defaults (changed 2026-10-10)
`~/.config/rpcs3/config.yml` (not tracked, machine-local): Resolution Scale 200, Anisotropic Filter Override 16
(was 100 / 0). New PS3 games inherit these; MW2 keeps its per-game file. If a heavy game drops below 60 FPS,
add `custom_configs/config_<TITLEID>.yml` with `Video: Resolution Scale: 100`.
Backup: `config.yml.bak-20261010-pre-global-res200` next to it.
