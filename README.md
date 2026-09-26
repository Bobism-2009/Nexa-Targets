# Nexa-Targets

Extra platforms for the [Nexa](https://github.com/Bobism-2009/Nexa-Lang) compiler.

NexaC builds for Windows, Linux, macOS and the browser (`--wasm`) out of the box.
Everything else lives here, one directory per platform, and is installed only by
the people who want it — so NexaC itself stays the size it is.

```sh
nexapkg target install arm64-linux
NexaC hello.nxa --target arm64-linux
```

| Target | Platform | Status |
|---|---|---|
| [`arm64-linux`](arm64-linux/) | 64-bit ARM Linux (AArch64): Raspberry Pi OS 64-bit, ARM servers, Linux VMs on Apple Silicon | 1.4.0 |

## How a target works

**A target is source code and nothing else.** No compiler, no prebuilt
libraries, no binaries of any kind — a C library, a C++ library and whatever
else the platform needs, as source, plus a `target.json` saying how to build
it. You can read every line of what ends up in your program.

**It is all compiled after you run NexaC**, by the clang you already have:
clang can build for every CPU LLVM supports, whether or not anyone asked it
to. The first `--target` build compiles the platform's runtime from source
(for `arm64-linux`, about 10 seconds on Linux and under a minute on Windows)
and keeps the result in
`~/.nexa/cache/targets/`. Every build after that is a single compile and link.

**A target says what it can do.** `target.json` lists the std modules it
supports, and NexaC refuses a program that uses any other one by name, before
compiling anything — not with a link error halfway through.

## Commands

```sh
nexapkg target install <name>     # install from this repository
nexapkg target update <name>      # re-fetch; the runtime is rebuilt on next use
nexapkg target list               # what is installed
nexapkg target remove <name>      # remove it and its compiled runtime

nexapkg target install <name> --from ./Nexa-Targets    # from a local checkout
```

Installing fetches only that target's directory (a sparse checkout), so the
repository growing more targets does not make any one of them slower to get.

## Requirements

- NexaC with `--target` support
- clang (LLVM) — the version each target needs is in its `target.json`
  (`"clang"`), and NexaC checks it. On Windows, the LLVM installer from
  llvm.org; on Linux and macOS, your package manager's `clang`/`llvm`. `ld.lld`
  and `llvm-ar`, which come with LLVM, are used too.
- git, to install

## Adding a target

A target is a directory holding:

- `target.json` — its name and version, which of NexaC's platform slices to
  emit (`"os"`), the clang target triple, the std modules it supports, and the
  libraries to build: for each, a source root, a list file of what to compile,
  and the compiler flags. A library can carry `"when": ["std/inline"]`: it is
  then built, and linked, only for a program that includes one of those
  modules -- the first such program builds it -- so a large library few
  programs need stays off everyone else's first build. See
  [`arm64-linux/target.json`](arm64-linux/target.json).
- `lists/` — one source path per line, relative to each library's root.
- `sources/` — the source itself.
- ideally a `tools/` script that regenerates `sources/` and `lists/` from
  upstream, the way [`tools/make-arm64-linux.py`](tools/make-arm64-linux.py)
  does, so updating to a newer upstream release is one command.

Flags can use `{target}` (the installed directory), `{root}` (the library's
source root) and `{resource}` (clang's own headers).

## Licenses

The files written for this repository — `target.json`, the lists, the
READMEs, `tools/`, and the generated configuration headers — are MIT
licensed; see [LICENSE](LICENSE). The third-party source each target carries
keeps its own license, next to it; each target's README lists them.
