#!/bin/sh -ex
# Build RPCS3 with debug symbols inside RPCS3's own CI image (rpcs3/rpcs3-ci-jammy:2.1).
# Same steps and flags as .ci/build-linux.sh (clang, lld, static LLVM, Release) except:
#   -g added so perf can name functions (codegen stays Release/-O3),
#   the bundle is left unstripped (NO_STRIP=1) and not packed into a compressed AppImage.
# Output: /work/rpcs3/build/AppDir (run with AppDir/AppRun).
cd /work/rpcs3

git config --global --add safe.directory '*'
# shellcheck disable=SC2046
git submodule -q update --init $(awk '/path/ && !/llvm/ && !/opencv/ && !/libsdl-org/ && !/curl/ && !/zlib/ { print $3 }' .gitmodules)

mkdir -p build && cd build

export CC="${CLANG_BINARY}"
export CXX="${CLANGXX_BINARY}"
export AR=/usr/bin/llvm-ar-"$LLVMVER"
export RANLIB=/usr/bin/llvm-ranlib-"$LLVMVER"
LINKER_FLAG="-fuse-ld=lld"

cmake ..                                               \
    -DCMAKE_BUILD_TYPE=Release                         \
    -DCMAKE_INSTALL_PREFIX=/usr                        \
    -DUSE_NATIVE_INSTRUCTIONS=OFF                      \
    -DUSE_PRECOMPILED_HEADERS=OFF                      \
    -DCMAKE_C_FLAGS="-g"                               \
    -DCMAKE_CXX_FLAGS="-g"                             \
    -DCMAKE_EXE_LINKER_FLAGS="${LINKER_FLAG}"          \
    -DCMAKE_MODULE_LINKER_FLAGS="${LINKER_FLAG}"       \
    -DCMAKE_SHARED_LINKER_FLAGS="${LINKER_FLAG}"       \
    -DCMAKE_AR="$AR"                                   \
    -DCMAKE_RANLIB="$RANLIB"                           \
    -DUSE_SYSTEM_CURL=ON                               \
    -DUSE_SDL=ON                                       \
    -DUSE_SYSTEM_SDL=ON                                \
    -DUSE_SYSTEM_FFMPEG=ON                             \
    -DUSE_SYSTEM_OPENCV=ON                             \
    -DUSE_DISCORD_RPC=ON                               \
    -DOpenGL_GL_PREFERENCE=LEGACY                      \
    -DLLVM_DIR=/opt/llvm/lib/cmake/llvm                \
    -DSTATIC_LINK_LLVM=ON                              \
    -DBUILD_RPCS3_TESTS=OFF                            \
    -DRUN_RPCS3_TESTS=OFF                              \
    -G Ninja

nice -n 19 ninja -j20

rm -rf AppDir
DESTDIR=AppDir ninja install

# Bundle Qt and other libs like the official AppImage, but keep symbols.
mkdir -p /work/tools
[ -x /work/tools/linuxdeploy ] || curl -fsSLo /work/tools/linuxdeploy "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
[ -x /work/tools/linuxdeploy-plugin-qt ] || curl -fsSLo /work/tools/linuxdeploy-plugin-qt "https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/continuous/linuxdeploy-plugin-qt-x86_64.AppImage"
chmod +x /work/tools/linuxdeploy /work/tools/linuxdeploy-plugin-qt
export PATH="/work/tools:$PATH"
export NO_STRIP=1
export EXTRA_PLATFORM_PLUGINS="libqwayland.so"
export EXTRA_QT_PLUGINS="svg;wayland-decoration-client;wayland-graphics-integration-client;wayland-shell-integration;waylandcompositor"
APPIMAGE_EXTRACT_AND_RUN=1 linuxdeploy --appdir AppDir --plugin qt

# Same removals as the official deploy script.
ln -srf ./AppDir/rpcs3.svg ./AppDir/.DirIcon
rm -f ./AppDir/usr/lib/libwayland-client.so*
rm -f ./AppDir/usr/lib/libvulkan.so*
rm -f ./AppDir/usr/lib/libQt6VirtualKeyboard.so*
rm -f ./AppDir/usr/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so*

ls -la AppDir/usr/bin/rpcs3
file AppDir/usr/bin/rpcs3
echo BUILD_DONE
