# mips32-linux

32-bit little-endian MIPS Linux (mipsel, MIPS32 release 2): routers and boards
built on MediaTek MT7621, MT7620 and MT7628 and other 24K/74K/1004K-class
cores -- what OpenWrt calls `ramips`, and most little-endian MIPS still in use.

```sh
nexapkg target install mips32-linux
NexaC hello.nxa --target mips32-linux      # writes ./hello, a MIPS32 Linux program
```

## What you get

A **static** executable: everything it needs is inside it, including its C
library, so it runs on any little-endian MIPS32r2 Linux -- OpenWrt, Debian, a
vendor firmware -- with nothing to install. Copy it over and run it.

```sh
scp -O hello root@192.168.1.1:/tmp/
ssh root@192.168.1.1 /tmp/hello
```

(`-O`: OpenWrt's Dropbear speaks the old scp protocol, not SFTP.) Without the
hardware, `qemu-mipsel ./hello` runs it on any Linux machine.

**Soft float.** Programs do their floating-point arithmetic in software rather
than with FPU instructions. Most chips this target is for -- the MT7621's
1004Kc, the MT7628's 24KEc -- have no FPU, and there a hard-float program
works only because the kernel traps every FPU instruction and emulates it,
which is many times slower than doing the arithmetic in software to begin
with. On a chip that does have an FPU, soft float still runs, just without it.

**32-bit.** Nexa's own types are the same size everywhere, but `size_t` is the
C type it names, and here that is 32 bits, as on any 32-bit platform.

## Supported

- **Modules:** `std/io`, `std/math`, `std/os`, `std/file`, `std/random`,
  `std/crypto`, `std/json`, `std/time`, `std/thread`, `std/network`,
  `std/inline`
- **The whole C++ standard library** for `inline_cpp!`, plus musl's Linux
  system calls and the kernel's own headers, so inline C++ can reach GPIO, I2C,
  a serial port or a network interface the way any Linux program does
- **HTTPS** in `std/network`, TLS 1.2 and 1.3, checked against the device's
  trusted roots: `/etc/ssl/certs/ca-certificates.crt` (on OpenWrt, from the
  `ca-bundle` package), the other distributions' usual bundles, or
  `SSL_CERT_FILE` / `SSL_CERT_DIR`. An expired, self-signed or wrong-host
  certificate fails with `TLS certificate verify failed` before anything is
  sent. It works as [arm64-linux's does](../arm64-linux/README.md#how-https-works-here).
- **C++ exceptions**, so `io.to_int`, `Result` and `try`/`catch` work

**Not supported**, and refused by NexaC before it compiles anything:

- `std/gfx`, `std/gfx3d`, `std/ui` -- no windowing is built for this target
- `std/dll` -- a static program cannot load a shared library

**Not this target:** big-endian MIPS (Atheros/Qualcomm routers, OpenWrt's
`ath79`), 64-bit MIPS, and MIPS32 release 6. A program built here will not
start on them.

## What is inside

All source, compiled by your clang the first time you build for this target,
then cached in `~/.nexa/cache/targets/mips32-linux/`: about 11 seconds on
Linux. Most of libc++ is compiled only the first time a program includes
`std/inline` (about 5 more seconds), and mbedTLS the first time one includes
`std/network` (about 2).

| | Upstream | License |
|---|---|---|
| `sources/musl` | [musl](https://musl.libc.org) 1.2.5 -- the C library | MIT, `sources/musl/COPYRIGHT` |
| `sources/libcxx` | [libc++](https://libcxx.llvm.org) from LLVM 22.1.4 -- the C++ library | Apache 2.0 with LLVM exception, `sources/libcxx/LICENSE.TXT` |
| `sources/libcxxabi` | libc++abi from LLVM 22.1.4 -- exceptions, RTTI, static-init guards | Apache 2.0 with LLVM exception, `sources/libcxxabi/LICENSE.TXT` |
| `sources/libunwind` | libunwind from LLVM 22.1.4 -- unwinds the stack when an exception is thrown | Apache 2.0 with LLVM exception, `sources/libunwind/LICENSE.TXT` |
| `sources/compiler-rt` | compiler-rt builtins from LLVM 22.1.4 -- the soft-float arithmetic, 64-bit division and 64-bit atomics | Apache 2.0 with LLVM exception, `sources/compiler-rt/LICENSE.TXT` |
| `sources/llvm-libc` | the LLVM libc 22.1.4 headers libc++'s `from_chars` is built from | Apache 2.0 with LLVM exception, `sources/llvm-libc/LICENSE.TXT` |
| `sources/linux-headers` | [Linux](https://kernel.org) 6.18.53 (longterm) -- the MIPS user-space API, as `make headers_install` exports it: `<linux/...>`, `<asm/...>` | GPL-2.0 WITH Linux-syscall-note, `sources/linux-headers/COPYING` -- the note means a program that uses them is not a derived work of the kernel. The netfilter headers whose names differ only in case are left out, as in arm64-linux |
| `sources/mbedtls` | [mbedTLS](https://github.com/Mbed-TLS/mbedtls) 3.6.7 (LTS) -- TLS for HTTPS | Apache 2.0, `sources/mbedtls/LICENSE` |
| `tls/` | written for this package: `openssl-shim.c`, and mbedTLS's settings | MIT (this repository) |
| `sources/musl-gen`, `sources/libcxx-gen` | the headers musl's Makefile and libc++'s CMake would have generated | MIT (this repository) |

## How it is compiled

`-march=mips32r2 -msoft-float` for everything, and `-fno-pic` for everything
but musl. MIPS code is position-independent by default, which makes every
call a load from a table first; a static program has no use for that, and
without it calls are direct and a program that uses the C++ runtime is about
8% smaller. musl
itself stays position-independent: its hand-written MIPS assembly (`dlsym`,
`clone`, `sigsetjmp` and others) expects to be called the PIC way, and the
linker bridges each call into it from the rest of the program.

musl on 32-bit platforms renames `dlsym` to `__dlsym_time64`, whose stub
calls `__dlsym_redir_time64`; `tls/openssl-shim.c` replaces that one too, so
HTTPS finds its OpenSSL calls here as it does on arm64-linux.

## Regenerating

`sources/` and `lists/` are produced by
[`../tools/make-mips32-linux.py`](../tools/make-mips32-linux.py), which reuses
[`make-arm64-linux.py`](../tools/make-arm64-linux.py) and changes only what
differs for MIPS. Run it on Linux, and bump `"version"` in `target.json` so
installed copies rebuild their runtime.

## Requirements

clang 21 or newer **built with the MIPS backend**, with `ld.lld` and `llvm-ar`
beside it. `clang -print-targets` lists `mipsel` when it has it. Linux
distributions' clang packages and Homebrew's `llvm` do. The llvm.org Windows
installer and llvm-mingw do not: they build for x86 and ARM only, and NexaC
stops with `No available targets are compatible with triple
"mipsel-unknown-linux-musl"`. On Windows, build in WSL.
