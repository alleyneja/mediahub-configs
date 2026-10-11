# RPCS3 on mediahub-r9

- `custom_configs/config_BLUS30377.yml`: MW2 per-game config. Resolution Scale 200 (1080p internal), Anisotropic 16x,
  Disable Vertex Cache on (split-screen fix). Live path: `~/.config/rpcs3/custom_configs/`.
- `loadtest/`: FPS recorder + load ladder used for alleyneja/mediahub-issues#52 (1 Hz FPS/CPU/GPU CSV).
  `loadtest_res.py` is the minimal "watch relaunch, record 12 min" variant; `res200-summary.md` is its result
  (mean 60.0 FPS, min 46.7, GPU 11%).
- Gotcha: the watcher's "already applied?" check must match the key line, not the file's comment header.
