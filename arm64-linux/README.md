# arm64-linux

64-bit ARM Linux (AArch64): Raspberry Pi OS 64-bit, ARM cloud servers, Linux
VMs and containers on Apple Silicon.

```sh
nexapkg target install arm64-linux
NexaC hello.nxa --target arm64-linux      # writes ./hello, an AArch64 Linux program
```

## What you get

A **static** executable: everything it needs is inside it, including its C
library, so it runs on any 64-bit ARM Linux — any distribution, any version,
nothing to install on the machine it runs on. Copy it over and run it.

```sh
scp hello pi@raspberrypi.local:
ssh pi@raspberrypi.local ./hello
```

On an Apple Silicon Mac with Docker, which runs ARM Linux containers natively:

```sh
docker run --rm -v "$PWD:/w" alpine /w/hello
```

## Supported

- **Modules:** `std/io`, `std/math`, `std/os`, `std/file`, `std/random`,
  `std/crypto`, `std/json`, `std/time`, `std/thread`, `std/network`
- **C++ exceptions**, so `io.to_int`, `Result` and `try`/`catch` work

**Not supported**, and refused by NexaC before it compiles anything:

- `std/gfx` and `std/gfx3d` — no display libraries are built for this target
- `std/dll` — a static program cannot load a shared library
- `std/inline` — `inline_cpp` can reach any part of the C++ library, and only
  the parts Nexa itself uses are built here
- HTTPS in `std/network`: on Linux, NexaC loads the system's OpenSSL at run
  time, which a static program cannot do. TCP and UDP are unaffected.

## What is inside

All source, compiled by your clang the first time you build for this target
(about half a minute), then cached in `~/.nexa/cache/targets/arm64-linux/`.

| | Upstream | License |
|---|---|---|
| `sources/musl` | [musl](https://musl.libc.org) 1.2.5 — the C library | MIT, `sources/musl/COPYRIGHT` |
| `sources/libcxx` | [libc++](https://libcxx.llvm.org) from LLVM 22.1.4 — the C++ library | Apache 2.0 with LLVM exception, `sources/libcxx/LICENSE.TXT` |
| `sources/libcxxabi` | libc++abi from LLVM 22.1.4 — exceptions, RTTI, static-init guards | Apache 2.0 with LLVM exception, `sources/libcxxabi/LICENSE.TXT` |
| `sources/libunwind` | libunwind from LLVM 22.1.4 — unwinds the stack when an exception is thrown | Apache 2.0 with LLVM exception, `sources/libunwind/LICENSE.TXT` |
| `sources/compiler-rt` | compiler-rt builtins from LLVM 22.1.4 — 128-bit `long double` arithmetic and friends | Apache 2.0 with LLVM exception, `sources/compiler-rt/LICENSE.TXT` |
| `sources/musl-gen`, `sources/libcxx-gen` | the headers musl's Makefile and libc++'s CMake would have generated | MIT (this repository) |

Only the parts that are used are here: musl's AArch64 sources, and of the LLVM
libraries, the headers plus the handful of source files a Nexa program actually
links against — found by building a program for every supported module and
adding what the linker asked for.

## Regenerating

`sources/` and `lists/` are produced by
[`../tools/make-arm64-linux.py`](../tools/make-arm64-linux.py) from the
upstream releases above. To move to a newer musl or LLVM, change the versions at
the top of that script, run it, and bump `"version"` in `target.json` so
installed copies rebuild their runtime.

## Requirements

clang 21 or newer (the libc++ here is LLVM 22's, which supports the two most
recent clang releases), with `ld.lld` and `llvm-ar` beside it — all part of an
ordinary LLVM install.
