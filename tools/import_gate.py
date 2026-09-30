#!/usr/bin/env python3
"""Import gate: every symbol a module imports must be *exported* by the device.

`/proc/kallsyms` on the device lists all kernel symbols, but only exported ones
have a `__ksymtab_<name>` entry (and module-provided exports appear there too,
tagged with [module]).  Checking the ELF symtab's undefined set against that
export set is what predicts

    coresight_etm4x: Unknown symbol coresight_xxx (err -2)

before the phone is touched.  It matters here because the device kernel is built
with CONFIG_TRIM_UNUSED_KSYMS=y, i.e. only a whitelist of exports survives.

Usage:
    import_gate.py <module.ko> <kallsyms-dump>
"""
import struct
import sys

SHN_UNDEF = 0
KSYMTAB_PREFIXES = ("__ksymtab_",)


def undefined_symbols(path):
    data = open(path, "rb").read()
    if data[:4] != b"\x7fELF":
        raise SystemExit(f"{path}: not an ELF file")
    (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
    (e_shentsize,) = struct.unpack_from("<H", data, 0x3A)
    (e_shnum,) = struct.unpack_from("<H", data, 0x3C)
    (e_shstrndx,) = struct.unpack_from("<H", data, 0x3E)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = \
            struct.unpack_from("<IIQQQQIIQQ", data, off)
        secs.append(dict(name=name, typ=typ, offset=offset, size=size,
                         link=link, entsize=entsize))
    strtab = secs[e_shstrndx]
    out = set()
    for sec in secs:
        if sec["typ"] != 2:                      # SHT_SYMTAB
            continue
        strings = secs[sec["link"]]
        entsize = sec["entsize"] or 24
        for i in range(sec["size"] // entsize):
            off = sec["offset"] + i * entsize
            n_name, _info, _other, shndx, _value, _size = \
                struct.unpack_from("<IBBHQQ", data, off)
            if shndx != SHN_UNDEF or n_name == 0:
                continue
            name = data[strings["offset"] + n_name:].split(b"\0", 1)[0]
            if name:
                out.add(name.decode("utf-8", "replace"))
    return out


def exported_symbols(kallsyms):
    """Accepts either a raw /proc/kallsyms dump or a names-only list.

    The names-only list (refs/exported-symbols.txt, produced with
    `review/import_gate.py --list`) is what CI uses: 12k names instead of 21 MB.
    """
    exported = set()
    with open(kallsyms, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            fields = line.split()
            if not fields:
                continue
            if len(fields) == 1:
                exported.add(fields[0])
                continue
            if len(fields) < 3:
                continue
            name = fields[2]
            for prefix in KSYMTAB_PREFIXES:
                if name.startswith(prefix):
                    sym = name[len(prefix):]
                    for gpl in ("unused_gpl_", "unused_", "gpl_"):
                        if sym.startswith(gpl):
                            sym = sym[len(gpl):]
                            break
                    exported.add(sym)
                    break
    return exported


def emit_list(kallsyms, out):
    with open(out, "w", encoding="utf-8") as fh:
        for name in sorted(exported_symbols(kallsyms)):
            fh.write(name + "\n")
    print(f"wrote {out}")


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--list":
        emit_list(sys.argv[2], sys.argv[3])
        return 0
    if len(sys.argv) != 3:
        raise SystemExit("usage: import_gate.py <module.ko> <kallsyms-dump|names-list>\n"
                         "       import_gate.py --list <kallsyms-dump> <out.txt>")
    ko, kallsyms = sys.argv[1], sys.argv[2]
    wanted = undefined_symbols(ko)
    exported = exported_symbols(kallsyms)
    missing = sorted(s for s in wanted if s not in exported)
    print(f"{ko}")
    print(f"  imports                        : {len(wanted)}")
    print(f"  exported by the device         : {len(wanted) - len(missing)}")
    print(f"  NOT exported (insmod would fail): {len(missing)}")
    for name in missing:
        print(f"    NOT-EXPORTED: {name}")
    if not missing:
        print("  -> every import resolves on this kernel")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
