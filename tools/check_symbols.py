#!/usr/bin/env python3
"""Check that every symbol a module imports exists on the running kernel.

The kernel resolves module symbols lazily at load time: an unknown symbol makes
`insmod` fail with

    coresight_etm4x: Unknown symbol coresight_xxx (err -2)

Parsing the module's ELF symtab for undefined symbols and comparing that set
against `/proc/kallsyms` (which lists every exported symbol of the running
kernel *and* of every loaded module) catches that before touching the device.

Usage:
    check_symbols.py <module.ko> [kallsyms-dump]

If the second argument is omitted it reads /proc/kallsyms, otherwise a file
produced with e.g.

    su -c 'cat /proc/kallsyms' | awk '{print $3}' | sort -u > kallsyms.txt
"""

import struct
import sys

SHN_UNDEF = 0


def undefined_symbols(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise SystemExit(f"{path}: expected an ELF64 little-endian object")

    (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
    (e_shentsize,) = struct.unpack_from("<H", data, 0x3A)
    (e_shnum,) = struct.unpack_from("<H", data, 0x3C)
    (e_shstrndx,) = struct.unpack_from("<H", data, 0x3E)

    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        (name, typ, flags, addr, offset, size, link, info, align, entsize) = \
            struct.unpack_from("<IIQQQQIIQQ", data, off)
        sections.append({"name": name, "typ": typ, "offset": offset,
                         "size": size, "link": link, "entsize": entsize})

    strtab = sections[e_shstrndx]
    for sec in sections:
        start = strtab["offset"] + sec["name"]
        sec["sname"] = data[start:].split(b"\0", 1)[0].decode("utf-8", "replace")

    names = set()
    for sec in sections:
        if sec["typ"] != 2:          # SHT_SYMTAB
            continue
        strings = sections[sec["link"]]
        entsize = sec["entsize"] or 24
        for i in range(sec["size"] // entsize):
            off = sec["offset"] + i * entsize
            n_name, _info, _other, shndx, _value, _size = \
                struct.unpack_from("<IBBHQQ", data, off)
            if shndx != SHN_UNDEF or n_name == 0:
                continue
            name = data[strings["offset"] + n_name:].split(b"\0", 1)[0]
            if name:
                names.add(name.decode("utf-8", "replace"))
    return names


def load_kallsyms(source):
    if source is None:
        try:
            fh = open("/proc/kallsyms", "r", encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SystemExit(f"cannot read /proc/kallsyms: {exc}")
        with fh:
            return {line.split()[2] for line in fh if len(line.split()) >= 3}
    with open(source, "r", encoding="utf-8", errors="replace") as fh:
        return {line.strip() for line in fh if line.strip()}


def main():
    if not 2 <= len(sys.argv) <= 3:
        raise SystemExit("usage: check_symbols.py <module.ko> [kallsyms-dump]")
    module = sys.argv[1]
    known = load_kallsyms(sys.argv[2] if len(sys.argv) > 2 else None)

    wanted = undefined_symbols(module)
    missing = sorted(s for s in wanted if s not in known)

    print(f"{module}")
    print(f"  undefined symbols        : {len(wanted)}")
    print(f"  missing from kallsyms    : {len(missing)}")
    interesting = sorted(s for s in wanted if s.startswith("coresight"))
    if interesting:
        print("  coresight_* symbols:")
        for name in interesting:
            print(f"    {'OK  ' if name in known else 'MISS'}  {name}")
    for name in missing:
        print(f"    MISSING: {name}")
    if not missing:
        print("  -> every symbol resolves on this kernel")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
