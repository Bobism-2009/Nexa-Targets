#!/usr/bin/env python3
"""Regenerate mips32-linux/sources and mips32-linux/lists from upstream.

    python3 tools/make-mips32-linux.py [--work DIR]

The same upstream releases as arm64-linux -- musl, LLVM's C++ runtime and
builtins, the Linux user-space headers and mbedTLS -- taken for 32-bit MIPS
instead, by tools/make-arm64-linux.py's own code: this script changes only what
differs. No X11: the boards and routers this target is for have no screen.

Run it on Linux, for the kernel's `make headers_install` (see
make-arm64-linux.py). The --work directory can be the one make-arm64-linux.py
used; the downloads are the same.
"""

import argparse
import importlib.util
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('arm64', os.path.join(HERE, 'make-arm64-linux.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

base.ARCH = 'mips'
base.KERNEL_ARCH = 'mips'
base.OUT = os.path.normpath(os.path.join(HERE, '..', 'mips32-linux'))
base.SRC = os.path.join(base.OUT, 'sources')
base.LISTS = os.path.join(base.OUT, 'lists')
base.CONFIG_SITE = base.CONFIG_SITE.replace(
    "Nexa's arm64-linux target: AArch64 Linux,\n// musl, statically linked.",
    "Nexa's mips32-linux target: 32-bit little-endian\n// MIPS Linux, musl, statically linked.")
assert 'mips32-linux' in base.CONFIG_SITE


# compiler-rt's builtins for 32-bit MIPS: mips_SOURCES in its CMakeLists.txt is
# GENERIC_SOURCES alone -- long double is double here, so none of the TF ones.
# Plus atomic.c: MIPS32 has no 64-bit atomic instructions, so a std::atomic of a
# 64-bit type (Nexa's int) is a call to __atomic_*_8, which it provides.
def build_builtins(llvm):
    B = os.path.join(llvm, 'compiler-rt', 'lib', 'builtins')
    cm = open(os.path.join(B, 'CMakeLists.txt'), encoding='utf-8').read()
    files = [f for f in base.cmake_list(cm, 'GENERIC_SOURCES') if f != 'enable_execute_stack.c'] + ['atomic.c']
    b_out = os.path.join(base.SRC, 'compiler-rt', 'lib', 'builtins')
    for f in os.listdir(B):
        p = os.path.join(B, f)
        if os.path.isfile(p) and f.endswith(('.c', '.h', '.inc', '.def')):
            base.copy(p, os.path.join(b_out, f))
    base.copy(os.path.join(llvm, 'compiler-rt', 'LICENSE.TXT'), os.path.join(base.SRC, 'compiler-rt', 'LICENSE.TXT'))
    base.write_list('builtins.txt', [
        "compiler-rt's builtins for 32-bit MIPS, as its CMakeLists.txt lists them:",
        'GENERIC_SOURCES (mips_SOURCES), which include the soft-float arithmetic,',
        'plus atomic.c for the 64-bit atomics MIPS32 has no instructions for.',
        'Relative to sources/compiler-rt/lib/builtins.',
    ], files)
    return files


# Some of musl's MIPS files are the generic C file behind an #if: soft float has
# no FPU instruction for src/math/mips/fabs.c to use, so it includes
# "../fabs.c" instead. The generic file is replaced, so not listed, but it has
# to be there to be included.
def build_musl(musl):
    files = base.build_musl(musl)
    inc = re.compile(r'^\s*#\s*include\s*"(\.\./[^"]+)"', re.M)
    m_out = os.path.join(base.SRC, 'musl')
    for f in files:
        if '/mips/' not in f:
            continue
        for rel in inc.findall(open(os.path.join(musl, f), encoding='utf-8', errors='replace').read()):
            src = os.path.normpath(os.path.join(musl, os.path.dirname(f), rel))
            base.copy(src, os.path.join(m_out, os.path.relpath(src, musl)))
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--work', default=os.path.join(HERE, '.work'),
                    help='where upstream sources are downloaded (reused if present)')
    args = ap.parse_args()
    musl, llvm = base.fetch(args.work)
    for d in (base.SRC, base.LISTS):
        if os.path.exists(d):
            shutil.rmtree(d)
    m = build_musl(musl)
    b = build_builtins(llvm)
    base.build_cxx(llvm)
    h = base.build_llvm_libc(llvm)
    k = base.build_linux_headers(args.work)
    t = base.build_mbedtls(args.work)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(base.OUT) for f in fs)
    print('mips32-linux: %d musl, %d builtins, %d libc++, %d libc++abi, %d libunwind files, '
          '%d LLVM libc headers, %d kernel headers, %d mbedTLS files; %.1f MB'
          % (len([f for f in m if not f.startswith('crt/')]), len(b), len(base.LIBCXX_SRC),
             len(base.LIBCXXABI_SRC), len(base.LIBUNWIND_SRC), h, k, t, size / 1e6))


if __name__ == '__main__':
    main()
