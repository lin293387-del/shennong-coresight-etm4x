#!/usr/bin/env python3
"""Harvest the symbol CRCs a running kernel expects, from its own modules.

Why this is needed
------------------
`kernel/module/version.c :: check_version()` compares CRCs *exactly*:

    for (i = 0; i < num_versions; i++) {
            if (strcmp(versions[i].name, symname) != 0)
                    continue;
            crcval = *crc;
            if (versions[i].crc == crcval)
                    return 1;
            goto bad_version;
    }
    pr_warn_once("%s: no symbol version for %s\\n", info->name, symname);
    return 1;

There is no "CRC 0 means anything goes" escape hatch: a recorded CRC that does
not match is fatal (`disagrees about version of symbol X` -> -ENOEXEC), while a
symbol that is *absent* from `__versions` is accepted with a warning.  So a
module built against a different tree must either omit the entry or carry the
exact CRC the running kernel publishes.

That exact CRC is readable from the vendor modules already installed on the
device: they import the very same kernel symbols and load successfully, so the
CRC they recorded is by definition the one this kernel checks against.

This script walks every .ko in a module directory, parses its `__versions`
section, and emits a synthetic `Module.symvers` (the format `modpost`'s
`read_dump()` accepts) for those symbols that `/proc/kallsyms` lists *without*
a `[module]` tag - i.e. symbols exported by vmlinux.

Symbols exported by other modules on purpose left out: modpost resolves those
against the modules it is building in the same run, which keeps the `depends=`
field of the resulting module correct.  They then simply do not appear in
`__versions`, which the kernel accepts.

Usage:
    collect_crcs.py <module-dir> <kallsyms> [output]

where <kallsyms> is produced with:

    su -c 'cat /proc/kallsyms' | awk 'NF>=3 {print $3"\\t"$4}' > kallsyms.txt
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modversions  # noqa: E402


def parse_kallsyms(path):
    """Return (kernel_symbols, module_symbols) name sets."""
    kernel, modules = set(), set()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                parts = line.split()
                if len(parts) < 3:
                    continue
                name, owner = parts[2], (parts[3] if len(parts) > 3 else "")
            else:
                name, owner = parts[0], parts[1]
            if not name:
                continue
            (modules if owner.startswith("[") else kernel).add(name)
    return kernel, modules


def main():
    if not 3 <= len(sys.argv) <= 4:
        raise SystemExit("usage: collect_crcs.py <module-dir> <kallsyms> [output]")
    moddir, kallsyms = sys.argv[1], sys.argv[2]

    kernel, modules = parse_kallsyms(kallsyms)
    print(f"kallsyms: {len(kernel)} kernel symbols, {len(modules)} module symbols")

    crcs = {}
    sources = {}
    scanned = skipped = 0
    for name in sorted(os.listdir(moddir)):
        if not name.endswith(".ko"):
            continue
        path = os.path.join(moddir, name)
        try:
            _data, _sections, sec, entries = modversions.load(path)
        except SystemExit as exc:
            print(f"  ! {exc}")
            continue
        if sec is None or not entries:
            skipped += 1
            continue
        scanned += 1
        for _i, _off, crc, sym in entries:
            if not crc:
                continue
            value = crc & 0xFFFFFFFF
            if sym not in kernel:
                continue          # exported by another module; leave it to modpost
            if sym in crcs and crcs[sym] != value:
                print(f"  ! {sym}: conflicting CRCs "
                      f"0x{crcs[sym]:08x} ({sources[sym]}) vs 0x{value:08x} ({name})")
                continue
            crcs[sym] = value
            sources[sym] = name

    print(f"scanned {scanned} module(s), {skipped} without a __versions section")
    print(f"harvested {len(crcs)} vmlinux symbol CRCs")

    lines = [f"0x{crcs[s]:08x}\t{s}\tvmlinux\tEXPORT_SYMBOL\t" for s in sorted(crcs)]
    text = "\n".join(lines) + "\n"
    if len(sys.argv) == 4:
        with open(sys.argv[3], "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {sys.argv[3]}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
