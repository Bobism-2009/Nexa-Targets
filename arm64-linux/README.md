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
  `std/crypto`, `std/json`, `std/time`, `std/thread`, `std/network`,
  `std/inline`
- **The whole C++ standard library** for `inline_cpp!` — iostreams,
  `<filesystem>`, `<regex>`, `<charconv>`, locales, threads and the rest —
  plus musl's Linux system calls, so inline C++ can open `/dev/gpiochip0`,
  an I2C bus or a serial port the way any Linux program does
- **HTTPS** in `std/network`, TLS 1.2 and 1.3, checked against the device's
  own trusted roots (`/etc/ssl/certs/ca-certificates.crt` on Raspberry Pi OS,
  Debian and Ubuntu; other distributions' bundles too; `SSL_CERT_FILE` or
  `SSL_CERT_DIR` to choose). An expired, self-signed or wrong-host
  certificate fails with `TLS certificate verify failed` before anything is
  sent. See [How HTTPS works here](#how-https-works-here).
- **C++ exceptions**, so `io.to_int`, `Result` and `try`/`catch` work
- **`char` is signed**, as on every other Nexa platform. AArch64 Linux makes
  a C `char` unsigned by default, so programs are built with `-fsigned-char`;
  without it `'\x80'` would be 128 here and -128 everywhere else.

**Not supported**, and refused by NexaC before it compiles anything:

- `std/gfx` and `std/gfx3d` — no display libraries are built for this target
- `std/dll` — a static program cannot load a shared library

## What is inside

All source, compiled by your clang the first time you build for this target,
then cached in `~/.nexa/cache/targets/arm64-linux/`: about 10 seconds on Linux,
under a minute on Windows, where clang is slower per file. Most of libc++ --
iostreams, locales, `<filesystem>`, `<regex>` -- is compiled only the first
time a program includes `std/inline` (another 5-15 seconds), since nothing else
uses it.

| | Upstream | License |
|---|---|---|
| `sources/musl` | [musl](https://musl.libc.org) 1.2.5 — the C library | MIT, `sources/musl/COPYRIGHT` |
| `sources/libcxx` | [libc++](https://libcxx.llvm.org) from LLVM 22.1.4 — the C++ library | Apache 2.0 with LLVM exception, `sources/libcxx/LICENSE.TXT` |
| `sources/libcxxabi` | libc++abi from LLVM 22.1.4 — exceptions, RTTI, static-init guards | Apache 2.0 with LLVM exception, `sources/libcxxabi/LICENSE.TXT` |
| `sources/libunwind` | libunwind from LLVM 22.1.4 — unwinds the stack when an exception is thrown | Apache 2.0 with LLVM exception, `sources/libunwind/LICENSE.TXT` |
| `sources/compiler-rt` | compiler-rt builtins from LLVM 22.1.4 — 128-bit `long double` arithmetic and friends | Apache 2.0 with LLVM exception, `sources/compiler-rt/LICENSE.TXT` |
| `sources/llvm-libc` | the LLVM libc 22.1.4 headers libc++'s `from_chars` is built from | Apache 2.0 with LLVM exception, `sources/llvm-libc/LICENSE.TXT` |
| `sources/linux-headers` | [Linux](https://kernel.org) 6.18.53 (longterm) — the arm64 user-space API, as `make headers_install` exports it: `<linux/...>`, `<asm/...>` | GPL-2.0 WITH Linux-syscall-note, `sources/linux-headers/COPYING` — the note means a program that uses them is not a derived work of the kernel. Eight pairs of netfilter headers whose names differ only in case (`xt_MARK.h`, `xt_mark.h`) are left out, so the package installs the same on Windows and macOS |
| `sources/mbedtls` | [mbedTLS](https://github.com/Mbed-TLS/mbedtls) 3.6.7 (LTS) — TLS for HTTPS | Apache 2.0, `sources/mbedtls/LICENSE` |
| `tls/` | written for this package: `openssl-shim.c`, and mbedTLS's settings | MIT (this repository) |
| `sources/musl-gen`, `sources/libcxx-gen` | the headers musl's Makefile and libc++'s CMake would have generated | MIT (this repository) |

Only the parts that are used are here: musl's AArch64 sources; all of libc++
(so `inline_cpp!` has the whole standard library); and of libc++abi, libunwind
and compiler-rt, the source files a C++ program on AArch64 Linux links against.
The linker keeps only what a program calls: a program that uses none of it is
16 bytes bigger than it was before the rest of libc++ was added.

## How HTTPS works here

On Linux, Nexa's HTTP runtime does not link OpenSSL; it `dlopen`s `libssl.so`
when a program first makes an HTTPS request. A static program has no dynamic
loader, and musl's `dlopen` is a stub that always fails -- a *weak* stub, as is
the `__dlsym` behind `dlsym`, there to be replaced. `tls/openssl-shim.c`
replaces them: asked for `libssl` or `libcrypto`, `dlopen` returns a handle, and
`dlsym` on it answers with the OpenSSL calls Nexa makes, implemented on
mbedTLS. Any other `dlopen` fails exactly as musl's would. Nothing in Nexa
knows; it is the same runtime a desktop Linux program gets.

The `tls` library is built only for programs that include `std/network`, the
first time one is built (about 6 more seconds on Linux), and is listed before
`c` in `target.json` so the linker takes the shim's `dlopen` over musl's. A
program that includes `std/network` but makes no HTTPS request carries none of
it.

## Regenerating

`sources/` and `lists/` are produced by
[`../tools/make-arm64-linux.py`](../tools/make-arm64-linux.py) from the
upstream releases above. To move to a newer musl, LLVM or Linux, change the
versions at the top of that script, run it, and bump `"version"` in
`target.json` so installed copies rebuild their runtime. Run it on Linux or
macOS: the kernel headers come from the kernel's own `make headers_install`,
which needs make and a C compiler.

## Requirements

clang 21 or newer (the libc++ here is LLVM 22's, which supports the two most
recent clang releases), with `ld.lld` and `llvm-ar` beside it — all part of an
ordinary LLVM install.
