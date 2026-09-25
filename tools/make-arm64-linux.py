#!/usr/bin/env python3
"""Regenerate arm64-linux/sources and arm64-linux/lists from upstream.

    python3 tools/make-arm64-linux.py [--work DIR]

Everything in arm64-linux/ except target.json and README.md is produced by
this script, from two upstream releases:

    musl 1.2.5          https://musl.libc.org/releases/musl-1.2.5.tar.gz
    LLVM 22.1.4         https://github.com/llvm/llvm-project  (tag llvmorg-22.1.4)
                        libc++, libc++abi, libunwind, compiler-rt builtins,
                        and the LLVM libc headers libc++'s charconv uses
    Linux 6.18.53       https://cdn.kernel.org/pub/linux/kernel/v6.x/  (longterm)
                        the arm64 user-space headers: <linux/...>, <asm/...>
    mbedTLS 3.6.7       https://github.com/Mbed-TLS/mbedtls  (LTS)
                        TLS for HTTPS, behind tls/openssl-shim.c

To move to a newer release, change the versions below, run this, and rebuild a
program with --target arm64-linux: the runtime is keyed by the package version,
so bump "version" in target.json too, or installed copies will keep using the
runtime they already compiled.

Nothing here compiles anything. It downloads, selects, and copies source files,
and writes the handful of headers that musl's and libc++'s own build systems
would otherwise have generated. Needs git (for a sparse checkout of LLVM, which
downloads only the directories used) and network access.

The kernel headers are produced by the kernel's own `make headers_install`,
which needs a Linux (or macOS) host with make and a C compiler: it builds a
small host tool that strips the kernel-internal parts out of each header.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request

MUSL_VERSION = '1.2.5'
MUSL_URL = 'https://musl.libc.org/releases/musl-%s.tar.gz' % MUSL_VERSION
LLVM_TAG = 'llvmorg-22.1.4'
LLVM_URL = 'https://github.com/llvm/llvm-project.git'
ARCH = 'aarch64'
LINUX_VERSION = '6.18.53'
MBEDTLS_VERSION = '3.6.7'
MBEDTLS_URL = ('https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-%s/mbedtls-%s.tar.bz2'
               % (MBEDTLS_VERSION, MBEDTLS_VERSION))
LINUX_URL = 'https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-%s.tar.xz' % LINUX_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, '..', 'arm64-linux'))
SRC = os.path.join(OUT, 'sources')
LISTS = os.path.join(OUT, 'lists')

# The parts of libc++ that are compiled rather than header-only: all of them,
# as libc++'s own src/CMakeLists.txt lists them for Linux with threads, the
# random device, localization and the filesystem library -- the whole C++
# standard library, so inline_cpp! (std/inline) can reach any of it. Two are
# left out: new_handler.cpp, because libc++abi defines the same handler, and
# filesystem/int128_builtins.cpp, because compiler-rt's builtins are linked.
# The part of it Nexa's own runtime reaches -- every program links this; found
# by linking every std module's programs and adding what the linker asked for.
# The rest is built only for a program that includes std/inline (target.json's
# "when"), so it stays off every other program's first build.
LIBCXX_CORE = [
    'new.cpp', 'new_helpers.cpp', 'verbose_abort.cpp', 'string.cpp',
    'random.cpp', 'thread.cpp', 'system_error.cpp', 'mutex.cpp',
    'condition_variable.cpp', 'future.cpp', 'stdexcept.cpp', 'exception.cpp',
    'typeinfo.cpp', 'error_category.cpp',
]
LIBCXX_SRC = [
    'algorithm.cpp', 'any.cpp', 'bind.cpp', 'call_once.cpp', 'charconv.cpp', 'chrono.cpp',
    'error_category.cpp', 'exception.cpp', 'expected.cpp',
    'filesystem/filesystem_clock.cpp', 'filesystem/filesystem_error.cpp', 'filesystem/path.cpp',
    'functional.cpp', 'hash.cpp', 'memory.cpp', 'memory_resource.cpp', 'new.cpp',
    'new_helpers.cpp', 'optional.cpp', 'print.cpp', 'random_shuffle.cpp',
    'ryu/d2fixed.cpp', 'ryu/d2s.cpp', 'ryu/f2s.cpp', 'stdexcept.cpp', 'string.cpp',
    'system_error.cpp', 'typeinfo.cpp', 'valarray.cpp', 'variant.cpp', 'vector.cpp',
    'verbose_abort.cpp',
    # threads
    'atomic.cpp', 'barrier.cpp', 'condition_variable_destructor.cpp', 'condition_variable.cpp',
    'future.cpp', 'mutex_destructor.cpp', 'mutex.cpp', 'shared_mutex.cpp', 'thread.cpp',
    # random device
    'random.cpp',
    # localization: iostreams, locales, regex
    'fstream.cpp', 'ios.cpp', 'ios.instantiations.cpp', 'iostream.cpp', 'locale.cpp',
    'ostream.cpp', 'regex.cpp', 'strstream.cpp',
    # filesystem
    'filesystem/directory_entry.cpp', 'filesystem/directory_iterator.cpp',
    'filesystem/operations.cpp',
]
LIBCXXABI_SRC = [
    'cxa_guard.cpp', 'abort_message.cpp', 'cxa_handlers.cpp', 'cxa_default_handlers.cpp',
    'cxa_aux_runtime.cpp', 'cxa_exception.cpp', 'cxa_exception_storage.cpp', 'cxa_personality.cpp',
    'cxa_vector.cpp', 'cxa_virtual.cpp', 'fallback_malloc.cpp', 'private_typeinfo.cpp',
    'stdlib_exception.cpp', 'stdlib_stdexcept.cpp', 'stdlib_typeinfo.cpp', 'cxa_demangle.cpp',
    'cxa_thread_atexit.cpp',
]
# EHABI is 32-bit ARM's, SEH is Windows', sjlj/wasm/AIX are other platforms'.
LIBUNWIND_SRC = [
    'libunwind.cpp', 'UnwindLevel1.c', 'UnwindLevel1-gcc-ext.c',
    'UnwindRegistersRestore.S', 'UnwindRegistersSave.S',
]

# libc++'s site configuration. CMake writes this from __config_site.in; here
# every answer is fixed by the target, so it is written out once.
CONFIG_SITE = """\
// libc++'s site configuration for Nexa's arm64-linux target: AArch64 Linux,
// musl, statically linked. Normally generated by CMake from __config_site.in;
// written out here because this target is built from source by NexaC, not by
// CMake, and every value is fixed by the target anyway.
#ifndef _LIBCPP___CONFIG_SITE
#define _LIBCPP___CONFIG_SITE

#define _LIBCPP_ABI_VERSION 1
#define _LIBCPP_ABI_NAMESPACE __1
#define _LIBCPP_ABI_FORCE_ITANIUM 0
#define _LIBCPP_ABI_FORCE_MICROSOFT 0
#define _LIBCPP_HAS_THREADS 1
#define _LIBCPP_HAS_MONOTONIC_CLOCK 1
#define _LIBCPP_HAS_TERMINAL 1
#define _LIBCPP_HAS_MUSL_LIBC 1
#define _LIBCPP_HAS_THREAD_API_PTHREAD 1
#define _LIBCPP_HAS_THREAD_API_EXTERNAL 0
#define _LIBCPP_HAS_THREAD_API_WIN32 0
#define _LIBCPP_HAS_THREAD_API_C11 0
#define _LIBCPP_HAS_VENDOR_AVAILABILITY_ANNOTATIONS 0
#define _LIBCPP_HAS_FILESYSTEM 1
#define _LIBCPP_HAS_RANDOM_DEVICE 1
#define _LIBCPP_HAS_LOCALIZATION 1
#define _LIBCPP_HAS_UNICODE 1
#define _LIBCPP_HAS_WIDE_CHARACTERS 1
#define _LIBCPP_HAS_TIME_ZONE_DATABASE 0
#define _LIBCPP_INSTRUMENTED_WITH_ASAN 0

#define _LIBCPP_PSTL_BACKEND_SERIAL

#define _LIBCPP_HARDENING_MODE_DEFAULT _LIBCPP_HARDENING_MODE_NONE
#define _LIBCPP_ASSERTION_SEMANTIC_DEFAULT _LIBCPP_ASSERTION_SEMANTIC_HARDENING_DEPENDENT

#define _LIBCPP_LIBC_PICOLIBC 0
#define _LIBCPP_LIBC_NEWLIB 0

#endif // _LIBCPP___CONFIG_SITE
"""


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def copytree(src, dst):
    shutil.copytree(src, dst, dirs_exist_ok=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='\n') as f:
        f.write(text)


def write_list(name, header, files):
    write(os.path.join(LISTS, name), ''.join('# ' + h + '\n' for h in header) + ''.join(f + '\n' for f in files))


def fetch(work):
    os.makedirs(work, exist_ok=True)
    musl = os.path.join(work, 'musl-' + MUSL_VERSION)
    if not os.path.isdir(musl):
        tgz = musl + '.tar.gz'
        print('downloading', MUSL_URL)
        urllib.request.urlretrieve(MUSL_URL, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(work)
    llvm = os.path.join(work, 'llvm-project')
    if not os.path.isdir(llvm):
        print('sparse-cloning', LLVM_URL, LLVM_TAG)
        # autocrlf off: a Windows git would otherwise hand back CRLF copies,
        # and the package keeps upstream's bytes exactly.
        subprocess.check_call(['git', '-c', 'core.autocrlf=false', 'clone', '--quiet',
                               '--depth', '1', '--branch', LLVM_TAG,
                               '--filter=blob:none', '--sparse', LLVM_URL, llvm])
        subprocess.check_call(['git', '-C', llvm, 'sparse-checkout', 'set',
                               'libcxx/include', 'libcxx/src', 'libcxx/vendor',
                               'libcxxabi/include', 'libcxxabi/src',
                               'libunwind/include', 'libunwind/src',
                               'compiler-rt/lib/builtins',
                               'libc/shared', 'libc/src/__support', 'libc/hdr',
                               'libc/include/llvm-libc-macros', 'libc/include/llvm-libc-types'])
    return musl, llvm


# --- musl ----------------------------------------------------------------------
#
# musl's Makefile builds every src/*/*.c (plus src/malloc/mallocng and crt/),
# except that a file of the same stem in the architecture's subdirectory --
# src/string/aarch64/memcpy.S for src/string/memcpy.c -- replaces it.

def musl_sources(musl):
    rel = lambda p: os.path.relpath(p, musl).replace('\\', '/')
    dirs = sorted(d for d in glob.glob(os.path.join(musl, 'src', '*')) if os.path.isdir(d))
    dirs += [os.path.join(musl, 'src', 'malloc', 'mallocng'), os.path.join(musl, 'crt')]
    out = []
    for d in dirs:
        arch_dir = os.path.join(d, ARCH)
        arch = sorted(glob.glob(os.path.join(arch_dir, '*.[csS]')))
        replaced = {os.path.splitext(os.path.basename(a))[0] for a in arch}
        for c in sorted(glob.glob(os.path.join(d, '*.c'))):
            if os.path.splitext(os.path.basename(c))[0] not in replaced:
                out.append(rel(c))
        out += [rel(a) for a in arch]
    return out


def mkalltypes(lines):
    """tools/mkalltypes.sed, in Python."""
    out = []
    for line in lines:
        m = re.match(r'^TYPEDEF (.*) ([^ ]*);$', line)
        if m:
            t, n = m.group(1), m.group(2)
            out.append('#if defined(__NEED_%s) && !defined(__DEFINED_%s)\ntypedef %s %s;\n#define __DEFINED_%s\n#endif\n'
                       % (n, n, t, n, n))
            continue
        m = re.match(r'^STRUCT * ([^ ]*) (.*);$', line)
        if m:
            n, b = m.group(1), m.group(2)
            out.append('#if defined(__NEED_struct_%s) && !defined(__DEFINED_struct_%s)\nstruct %s %s;\n#define __DEFINED_struct_%s\n#endif\n'
                       % (n, n, n, b, n))
            continue
        m = re.match(r'^UNION * ([^ ]*) (.*);$', line)
        if m:
            n, b = m.group(1), m.group(2)
            out.append('#if defined(__NEED_union_%s) && !defined(__DEFINED_union_%s)\nunion %s %s;\n#define __DEFINED_union_%s\n#endif\n'
                       % (n, n, n, b, n))
            continue
        out.append(line)
    return ''.join(l + '\n' for l in out)


def build_musl(musl):
    files = musl_sources(musl)
    m_out = os.path.join(SRC, 'musl')
    for f in files:
        copy(os.path.join(musl, f), os.path.join(m_out, f))
    # Every header under src/ -- sources include their neighbours -- except
    # those in another architecture's override directory.
    arches = set(os.listdir(os.path.join(musl, 'arch')))
    for h in glob.glob(os.path.join(musl, 'src', '**', '*.h'), recursive=True):
        parts = os.path.relpath(h, musl).replace('\\', '/').split('/')
        if len(parts) >= 4 and parts[2] in arches and parts[2] != ARCH:
            continue
        copy(h, os.path.join(m_out, *parts))
    copytree(os.path.join(musl, 'include'), os.path.join(m_out, 'include'))
    copytree(os.path.join(musl, 'arch', ARCH), os.path.join(m_out, 'arch', ARCH))
    copytree(os.path.join(musl, 'arch', 'generic'), os.path.join(m_out, 'arch', 'generic'))
    for f in ('COPYRIGHT', 'VERSION', 'README'):
        copy(os.path.join(musl, f), os.path.join(m_out, f))

    # The three headers musl's Makefile generates.
    gen = os.path.join(SRC, 'musl-gen')
    read = lambda p: open(os.path.join(musl, p), newline='').read().splitlines()
    write(os.path.join(gen, 'include', 'bits', 'alltypes.h'),
          mkalltypes(read('arch/%s/bits/alltypes.h.in' % ARCH) + read('include/alltypes.h.in')))
    sc = read('arch/%s/bits/syscall.h.in' % ARCH)
    write(os.path.join(gen, 'include', 'bits', 'syscall.h'),
          ''.join(l + '\n' for l in sc) + ''.join(l.replace('__NR_', 'SYS_', 1) + '\n' for l in sc if '__NR_' in l))
    write(os.path.join(gen, 'src', 'internal', 'version.h'), '#define VERSION "%s"\n' % MUSL_VERSION)

    write_list('musl.txt', [
        'musl %s, the files its Makefile builds for %s: every src/*/*.c,' % (MUSL_VERSION, ARCH),
        'except where src/*/%s/ has a file of the same name, which replaces it.' % ARCH,
        'Relative to sources/musl. The crt/ start files are in target.json instead.',
    ], [f for f in files if not f.startswith('crt/')])
    return files


# --- compiler-rt builtins --------------------------------------------------------

def cmake_list(text, name):
    m = re.search(r'^set\(' + name + r'\s*\n(.*?)^\)', text, re.S | re.M)
    return [l.strip() for l in m.group(1).splitlines()
            if l.strip() and not l.strip().startswith('#') and '${' not in l]


def build_builtins(llvm):
    B = os.path.join(llvm, 'compiler-rt', 'lib', 'builtins')
    cm = open(os.path.join(B, 'CMakeLists.txt'), encoding='utf-8').read()
    # aarch64_SOURCES in the same file: the two generic lists plus two of its
    # own. The SME and outline-atomics extras are left out: the target builds
    # with -mno-outline-atomics, and nothing here uses SME.
    files = cmake_list(cm, 'GENERIC_TF_SOURCES') + cmake_list(cm, 'GENERIC_SOURCES') + \
        ['cpu_model/%s.c' % ARCH, '%s/fp_mode.c' % ARCH]
    files = [f for f in files if f != 'enable_execute_stack.c']
    b_out = os.path.join(SRC, 'compiler-rt', 'lib', 'builtins')
    for f in os.listdir(B):
        p = os.path.join(B, f)
        if os.path.isfile(p) and f.endswith(('.c', '.h', '.inc', '.def')):
            copy(p, os.path.join(b_out, f))
    copytree(os.path.join(B, ARCH), os.path.join(b_out, ARCH))
    copytree(os.path.join(B, 'cpu_model'), os.path.join(b_out, 'cpu_model'))
    copy(os.path.join(llvm, 'compiler-rt', 'LICENSE.TXT'), os.path.join(SRC, 'compiler-rt', 'LICENSE.TXT'))
    write_list('builtins.txt', [
        "compiler-rt's builtins for %s, as its CMakeLists.txt lists them:" % ARCH,
        'GENERIC_TF_SOURCES + GENERIC_SOURCES + the two %s files. The SME' % ARCH,
        'and outline-atomics extras are left out (the target builds with',
        '-mno-outline-atomics). Relative to sources/compiler-rt/lib/builtins.',
    ], files)
    return files


# --- Linux kernel headers ----------------------------------------------------------
#
# <linux/futex.h> for libc++'s atomic waits, and for inline_cpp! everything a
# Linux program reaches for below the C library: <linux/gpio.h>,
# <linux/i2c-dev.h>, <linux/spi/spidev.h>, <linux/input.h> and the rest. musl
# ships none of them, by design. This is the kernel's user-space API exactly
# as `make headers_install` exports it for arm64 -- the same set a distribution
# packages as linux-libc-dev or kernel-headers. Licensed GPL-2.0 WITH
# Linux-syscall-note: using these headers does not make a program a derived
# work of the kernel.

def build_linux_headers(work):
    src = os.path.join(work, 'linux-' + LINUX_VERSION)
    if not os.path.isdir(src):
        txz = src + '.tar.xz'
        print('downloading', LINUX_URL)
        urllib.request.urlretrieve(LINUX_URL, txz)
        with tarfile.open(txz) as t:
            t.extractall(work)
    staged = os.path.join(work, 'linux-headers-arm64')
    if os.path.exists(staged):
        shutil.rmtree(staged)
    subprocess.check_call(['make', '-s', '-C', src, 'ARCH=arm64',
                           'INSTALL_HDR_PATH=' + staged, 'headers_install'])
    out = os.path.join(SRC, 'linux-headers')
    copytree(os.path.join(staged, 'include'), os.path.join(out, 'include'))
    # headers_install leaves its bookkeeping beside the headers.
    for root, _, files in os.walk(out):
        for f in files:
            if f.startswith('.') or not f.endswith('.h'):
                os.remove(os.path.join(root, f))
    # A few netfilter headers come in pairs that differ only in case
    # (xt_MARK.h, xt_mark.h). Windows and macOS keep one of each, so the
    # package would install differently there than on Linux; both of every
    # such pair are left out. They are iptables extension structures, which
    # no program built for this target reaches for.
    by_lower = {}
    for root, _, files in os.walk(out):
        for f in files:
            full = os.path.join(root, f)
            by_lower.setdefault(full.lower(), []).append(full)
    for group in by_lower.values():
        if len(group) > 1:
            for full in group:
                os.remove(full)
    copy(os.path.join(src, 'COPYING'), os.path.join(out, 'COPYING'))
    copy(os.path.join(src, 'LICENSES', 'exceptions', 'Linux-syscall-note'),
         os.path.join(out, 'Linux-syscall-note'))
    return sum(len(fs) for _, _, fs in os.walk(os.path.join(out, 'include')))


# --- mbedTLS: HTTPS -----------------------------------------------------------------
#
# Nexa's HTTP runtime dlopen()s libssl at run time, which a static program
# cannot do. tls/openssl-shim.c (written for this package, not generated)
# answers that dlopen with the OpenSSL calls Nexa makes, implemented on
# mbedTLS. Everything in library/ is listed; the linker keeps what the shim
# reaches. The release tarball already carries the files mbedTLS generates.

def build_mbedtls(work):
    src = os.path.join(work, 'mbedtls-' + MBEDTLS_VERSION)
    if not os.path.isdir(src):
        tbz = src + '.tar.bz2'
        print('downloading', MBEDTLS_URL)
        urllib.request.urlretrieve(MBEDTLS_URL, tbz)
        with tarfile.open(tbz) as t:
            t.extractall(work)
    out = os.path.join(SRC, 'mbedtls')
    copytree(os.path.join(src, 'include'), os.path.join(out, 'include'))
    lib = sorted(f for f in os.listdir(os.path.join(src, 'library')) if f.endswith(('.c', '.h')))
    for f in lib:
        copy(os.path.join(src, 'library', f), os.path.join(out, 'library', f))
    copy(os.path.join(src, 'LICENSE'), os.path.join(out, 'LICENSE'))
    files = ['sources/mbedtls/library/' + f for f in lib if f.endswith('.c')]
    write_list('tls.txt', [
        'HTTPS: mbedTLS %s, and the OpenSSL-shaped front Nexa\'s HTTP runtime' % MBEDTLS_VERSION,
        'loads (tls/openssl-shim.c, part of this package). Relative to the',
        'package directory. Built only for programs that include std/network.',
    ], files + ['tls/openssl-shim.c'])
    return len(files)


# --- LLVM libc: the headers libc++'s charconv borrows -------------------------------
#
# libc++'s from_chars for floating point is LLVM libc's string-to-float, used as
# headers: src/include/from_chars_floating_point.h includes "shared/fp_bits.h"
# and friends, with the libc directory on the include path. Only the headers
# those includes reach are copied -- found by following every quoted #include
# from them, conditional ones too, so nothing a configuration might want is
# missing.

LIBC_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)


def build_llvm_libc(llvm):
    libc = os.path.normpath(os.path.join(llvm, 'libc'))
    start = os.path.join(llvm, 'libcxx', 'src', 'include', 'from_chars_floating_point.h')
    seen, todo = set(), [start]
    while todo:
        f = todo.pop()
        with open(f, encoding='utf-8') as fh:
            text = fh.read()
        for inc in LIBC_INCLUDE.findall(text):
            for base in (libc, os.path.dirname(f)):
                cand = os.path.normpath(os.path.join(base, inc))
                if os.path.isfile(cand) and cand.startswith(libc):
                    if cand not in seen:
                        seen.add(cand)
                        todo.append(cand)
                    break
    out = os.path.join(SRC, 'llvm-libc')
    for f in sorted(seen):
        copy(f, os.path.join(out, os.path.relpath(f, libc)))
    copy(os.path.join(libc, 'LICENSE.TXT'), os.path.join(out, 'LICENSE.TXT'))
    return len(seen)


# --- libc++, libc++abi, libunwind --------------------------------------------------

def build_cxx(llvm):
    L = os.path.join(llvm, 'libcxx')
    x_out = os.path.join(SRC, 'libcxx')
    copytree(os.path.join(L, 'include'), os.path.join(x_out, 'include'))
    copytree(os.path.join(L, 'src', 'include'), os.path.join(x_out, 'src', 'include'))
    copytree(os.path.join(L, 'src', 'support'), os.path.join(x_out, 'src', 'support'))
    for f in LIBCXX_SRC:
        copy(os.path.join(L, 'src', f), os.path.join(x_out, 'src', f))
    # The sources' private headers, wherever they sit under src/.
    for h in glob.glob(os.path.join(L, 'src', '**', '*.h'), recursive=True):
        copy(h, os.path.join(x_out, 'src', os.path.relpath(h, os.path.join(L, 'src'))))
    copy(os.path.join(L, 'LICENSE.TXT'), os.path.join(x_out, 'LICENSE.TXT'))
    write(os.path.join(SRC, 'libcxx-gen', '__config_site'), CONFIG_SITE)
    copy(os.path.join(L, 'vendor', 'llvm', 'default_assertion_handler.in'),
         os.path.join(SRC, 'libcxx-gen', '__assertion_handler'))
    assert set(LIBCXX_CORE) <= set(LIBCXX_SRC)
    write_list('libcxx.txt', [
        'The part of libc++ Nexa\'s own runtime reaches: every program links it.',
        'Relative to sources/libcxx.',
    ], ['src/' + f for f in LIBCXX_CORE])
    write_list('libcxx-full.txt', [
        'The rest of libc++ -- iostreams, locales, <filesystem>, <regex>,',
        '<charconv> and the others -- built only for a program that includes',
        'std/inline. Relative to sources/libcxx.',
    ], ['src/' + f for f in LIBCXX_SRC if f not in LIBCXX_CORE])

    A = os.path.join(llvm, 'libcxxabi')
    a_out = os.path.join(SRC, 'libcxxabi')
    copytree(os.path.join(A, 'include'), os.path.join(a_out, 'include'))
    for f in os.listdir(os.path.join(A, 'src')):
        if f.endswith(('.h', '.inc')) or f in LIBCXXABI_SRC:
            copy(os.path.join(A, 'src', f), os.path.join(a_out, 'src', f))
    copytree(os.path.join(A, 'src', 'demangle'), os.path.join(a_out, 'src', 'demangle'))
    copy(os.path.join(A, 'LICENSE.TXT'), os.path.join(a_out, 'LICENSE.TXT'))
    write_list('libcxxabi.txt', [
        'The parts of libc++abi a Nexa program on this target reaches: exceptions',
        '(throwing, catching, the personality routine and the type information a',
        'catch matches against), the static-initialisation guard, terminate and',
        'its handlers. Relative to sources/libcxxabi.',
    ], ['src/' + f for f in LIBCXXABI_SRC])

    U = os.path.join(llvm, 'libunwind')
    u_out = os.path.join(SRC, 'libunwind')
    copytree(os.path.join(U, 'include'), os.path.join(u_out, 'include'))
    for f in os.listdir(os.path.join(U, 'src')):
        if f.endswith(('.h', '.hpp')) or f in LIBUNWIND_SRC:
            copy(os.path.join(U, 'src', f), os.path.join(u_out, 'src', f))
    copy(os.path.join(U, 'LICENSE.TXT'), os.path.join(u_out, 'LICENSE.TXT'))
    write_list('libunwind.txt', [
        'libunwind for %s: what walks the stack when a C++ exception is thrown.' % ARCH,
        "The EHABI, SEH, sjlj, wasm and AIX sources are other platforms'. Relative",
        'to sources/libunwind.',
    ], ['src/' + f for f in LIBUNWIND_SRC])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--work', default=os.path.join(HERE, '.work'),
                    help='where upstream sources are downloaded (reused if present)')
    args = ap.parse_args()
    musl, llvm = fetch(args.work)
    for d in (SRC, LISTS):
        if os.path.exists(d):
            shutil.rmtree(d)
    m = build_musl(musl)
    b = build_builtins(llvm)
    build_cxx(llvm)
    h = build_llvm_libc(llvm)
    k = build_linux_headers(args.work)
    t = build_mbedtls(args.work)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs)
    print('arm64-linux: %d musl, %d builtins, %d libc++, %d libc++abi, %d libunwind files, '
          '%d LLVM libc headers, %d kernel headers, %d mbedTLS files; %.1f MB'
          % (len([f for f in m if not f.startswith('crt/')]), len(b), len(LIBCXX_SRC),
             len(LIBCXXABI_SRC), len(LIBUNWIND_SRC), h, k, t, size / 1e6))


if __name__ == '__main__':
    main()
