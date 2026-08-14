# Building Sunshine with Pascal (sm_61) CUDA support

**Why this exists:** the official LizardByte `.deb` cannot use NVENC on the Quadro P400.
It falls back to `h264_vaapi` on the Intel iGPU, which forces a per-frame copy off the
NVIDIA card just to encode.

## Root cause

Sunshine's `cmake/compile_definitions/linux.cmake` only adds the older CUDA
architectures on the *else* branch of a CUDA-13 check:

```cmake
if(CMAKE_CUDA_COMPILER_VERSION VERSION_GREATER_EQUAL 13.0)
    list(REMOVE_ITEM CMAKE_CUDA_ARCHITECTURES 101)
    list(APPEND CMAKE_CUDA_ARCHITECTURES 110)
else()
    list(APPEND CMAKE_CUDA_ARCHITECTURES 50 52 53 60 61 62 70 72)
endif()
```

Upstream builds with CUDA 13, which dropped Pascal, so `61` is never included.
The P400 is compute capability **6.1**, so at runtime the colorspace kernel fails with:

```
RGBA_to_NV12 failed: cudaErrorNoKernelImageForDevice:
    no kernel image is available for execution on the device
```

Building against **CUDA 12.x** takes the `else` branch and includes `61` automatically.
No source patching is required.

## Build recipe (Ubuntu 24.04)

Four non-obvious requirements, none in the upstream dependency list:

1. **CUDA 12.x, not 13.** Ubuntu's `nvidia-cuda-toolkit` is 12.0 — correct here.
2. **GCC 13 for C++, but CUDA 12.0 rejects it.** Sunshine's source needs C++20
   `<format>` (GCC 13+); nvcc 12.0 refuses GCC 13. Resolve with
   `-DCMAKE_CUDA_FLAGS="-allow-unsupported-compiler"`. Do **not** downgrade to GCC 12 —
   the build then fails with `fatal error: format: No such file or directory`.
3. **Force GCC 13's libstdc++ at link time.** CUDA's implicit link dirs point at
   GCC 12, whose static `libstdc++.a` lacks `GLIBCXX_3.4.31`, producing
   `undefined reference to ... _M_replace_cold`. Verify with:
   `nm -C /usr/lib/gcc/x86_64-linux-gnu/13/libstdc++.a | grep -c _M_replace_cold` (6 vs 0 for GCC 12).
4. **Node 20+ for the web UI.** Ubuntu ships Node 18; vite needs `crypto.hash()`,
   added in Node 20.12. Use a local Node tarball rather than adding a NodeSource repo.

```bash
# extra deps beyond the documented list
sudo apt-get install -y nvidia-cuda-toolkit libvulkan-dev glslang-tools libpipewire-0.3-dev

# Node 22 staged locally, not installed system-wide
curl -sL -o node.tar.xz https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz
tar -xf node.tar.xz && export PATH="$PWD/node-v22.23.2-linux-x64/bin:$PATH"

git clone --branch <tag> --depth 1 --recurse-submodules --shallow-submodules \
    https://github.com/LizardByte/Sunshine.git
cd Sunshine && npm install && mkdir build && cd build

cmake -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DSUNSHINE_ENABLE_CUDA=ON \
      -DBUILD_DOCS=OFF \
      -DCMAKE_INSTALL_PREFIX=/usr \
      -DCMAKE_CUDA_HOST_COMPILER=g++-13 \
      -DCMAKE_CUDA_FLAGS="-allow-unsupported-compiler" \
      -DCMAKE_EXE_LINKER_FLAGS="-L/usr/lib/gcc/x86_64-linux-gnu/13" ..

ninja -j3          # -j3 not -j4: this box also serves Plex and ~50 containers
cpack -G DEB
sudo dpkg -i cpack_artifacts/Sunshine.deb
```

`BUILD_DOCS=OFF` because Ubuntu's Doxygen is 1.9.8 and the build wants >= 1.10.
Docs have nothing to do with the streaming binary.

## Verify before trusting it

```bash
cuobjdump --list-elf /usr/bin/sunshine | grep -oE 'sm_[0-9]+' | sort -u   # must include sm_61
journalctl -u sunshine | grep 'Found H.264'                              # want h264_nvenc, not h264_vaapi
```

Correct result on this host — note NvFBC, which is Quadro-only and avoids a capture copy:

```
Info: Screencasting with NvFBC
Info: Found H.264 encoder: h264_nvenc [nvenc]
Info: Found HEVC encoder: hevc_nvenc [nvenc]
```

## Gotchas

- **The package is held** (`apt-mark hold sunshine`). The built version is
  `0.0.0-<commit>-dirty`, which sorts *below* the official release, so an
  upgrade would silently restore the broken-NVENC build. Removing the hold
  reintroduces the bug.
- **Rollback** is `dpkg -i` of the official
  `sunshine-ubuntu-24.04-amd64.deb`; it will warn about a downgrade either way.
- **Every Sunshine update means recompiling.** There is no repo path to a working
  build for this card.
- **All of this becomes unnecessary on the new build** — any post-Pascal GPU is
  supported by the stock package. Delete this doc then.
