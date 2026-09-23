FROM nvidia/cuda:12.8.1-base-ubuntu22.04

# Apt packages — own layer so pip changes don't re-run apt
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg python3 python3-pip curl gosu tzdata

# Torch — pinned to what the fork was vendored from and verified against
# (2.11.0+cu128). No -U: an unpinned upgrade here would silently drift the
# fork away from the stable-ts/faster-whisper behavior this design was
# built and tested against.
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install torch==2.11.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128

# App dependencies — pinned in requirements.txt (exact versions extracted
# from the container this was forked from). Only rebuilds when
# requirements.txt changes; no -U for the same drift reason as torch above.
COPY requirements.txt /
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install -r /requirements.txt \
    && apt-get purge -y --auto-remove python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /subgen

# App files last — changes here don't bust the layers above.
# qa_voting.py (added in Task 4) is our own module, not from upstream.
COPY launcher.py subgen.py language_code.py qa_voting.py /subgen/

RUN mkdir -p /cache && chmod 777 /cache

ENV XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    MPLCONFIGDIR=/cache/matplotlib \
    PYTHONUNBUFFERED=1

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "launcher.py"]
