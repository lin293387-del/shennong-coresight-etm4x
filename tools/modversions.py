#!/usr/bin/env python3
"""Inspect / zero / compare the modversions section of a kernel module.

Background
----------
Two kernel behaviours make it possible to load a module that was built from a
different kernel tree than the running kernel (see kernel/module/version.c):

  same_magic()
      When the module carries a "__versions" section (i.e. it was built with
      CONFIG_MODVERSIONS) the kernel strips the version part of the vermagic
      string and compares only the trailing flags, e.g.
          "SMP preempt mod_unload modversions aarch64"
      The kernel version prefix (6.1.68 vs 6.1.138) is therefore irrelevant.

  check_version()
      For every imported symbol the kernel compares the CRC recorded in
      "__versions" against the exporting side's CRC -- but a recorded CRC of
      *zero* is accepted unconditionally:

          if (versions[i].crc == 0) return 1;

      Zeroing the CRCs therefore turns symbol versioning into a no-op while
      keeping the version-relaxed vermagic path.

Layout
------
  struct modversion_info { unsigned long crc; char name[MODULE_NAME_LEN]; };
  MODULE_NAME_LEN = 64 - sizeof(unsigned long)  ->  56 on aarch64,
  so one entry is 8 + 56 = 64 bytes.  sh_entsize is normally set correctly and
  is preferred when present.

Usage
-----
  modversions.py dump    <module.ko>
  modversions.py zero    <module.ko>          # in place
  modversions.py compare <ref.ko> [ref2.ko …] -- <target.ko>
"""

import argparse
import struct
import sys

VERSEC = "__versions"


def parse_elf(path):
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    if data[:4] != b"\x7fELF":
        raise SystemExit(f"{path}: not an ELF file")
    if data[4] != 2 or data[5] != 1:
        raise SystemExit(f"{path}: only ELF64 little-endian is supported")

    (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
    (e_shentsize,) = struct.unpack_from("<H", data, 0x3A)
    (e_shnum,) = struct.unpack_from("<H", data, 0x3C)
    (e_shstrndx,) = struct.unpack_from("<H", data, 0x3E)

    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        (name, typ, flags, addr, offset, size, link, info, align, entsize) = \
            struct.unpack_from("<IIQQQQIIQQ", data, off)
        sections.append({"name": name, "type": typ, "offset": offset,
                         "size": size, "entsize": entsize})

    strtab = sections[e_shstrndx]
    for sec in sections:
        start = strtab["offset"] + sec["name"]
        sec["sname"] = bytes(data[start:]).split(b"\0", 1)[0].decode("utf-8", "replace")
    return data, sections


def find_versions(sections):
    for sec in sections:
        if sec["sname"] == VERSEC:
            return sec
    return None


def read_entries(data, sec):
    ent = sec["entsize"] or 64
    count = sec["size"] // ent
    out = []
    for i in range(count):
        off = sec["offset"] + i * ent
        (crc,) = struct.unpack_from("<Q", data, off)
        name = bytes(data[off + 8: off + ent]).split(b"\0", 1)[0]
        out.append((i, off, crc, name.decode("utf-8", "replace")))
    return out


def load(path):
    data, sections = parse_elf(path)
    sec = find_versions(sections)
    if sec is None:
        return data, sections, None, []
    return data, sections, sec, read_entries(data, sec)


def cmd_dump(args):
    data, sections, sec, entries = load(args.ko)
    if sec is None:
        print(f"{args.ko}: NO {VERSEC} section")
        print("  -> same_magic() will compare the FULL vermagic including the")
        print("     kernel version, so this module will only load on a kernel")
        print("     with an identical UTS_RELEASE.")
        return 1
    ent = sec["entsize"] or 64
    print(f"{args.ko}: {VERSEC} size={sec['size']} entsize={ent} "
          f"entries={len(entries)}")
    for i, _off, crc, name in entries:
        print(f"  [{i:4d}] {crc:016x}  {name}")
    return 0


def cmd_zero(args):
    data, _sections, sec, entries = load(args.ko)
    if sec is None:
        raise SystemExit(f"{args.ko}: no {VERSEC} section, cannot zero")
    changed = 0
    for _i, off, crc, _name in entries:
        if crc:
            struct.pack_into("<Q", data, off, 0)
            changed += 1
    if changed:
        with open(args.ko, "wb") as fh:
            fh.write(data)
    print(f"{args.ko}: zeroed {changed} CRC(s) out of {len(entries)} entries")
    return 0


def cmd_compare(args):
    refs = {}
    for ref in args.refs:
        try:
            _d, _s, sec, entries = load(ref)
        except SystemExit as exc:
            print(f"! {exc}")
            continue
        if sec is None:
            print(f"! {ref}: no {VERSEC} section")
            continue
        for _i, _off, crc, name in entries:
            refs.setdefault(name, (crc, ref))

    _d, _s, sec, entries = load(args.target)
    if sec is None:
        raise SystemExit(f"{args.target}: no {VERSEC} section")

    same = diff = unknown = 0
    print(f"{'STATUS':8} {'TARGET CRC':>18} {'REF CRC':>18}  SYMBOL")
    for _i, _off, crc, name in entries:
        ref = refs.get(name)
        if ref is None:
            unknown += 1
            print(f"{'UNKNOWN':8} {crc:>18x} {'-':>18}  {name}")
        elif ref[0] == crc:
            same += 1
            print(f"{'MATCH':8} {crc:>18x} {ref[0]:>18x}  {name}")
        else:
            diff += 1
            print(f"{'DIFF':8} {crc:>18x} {ref[0]:>18x}  {name}   ({ref[1]})")
    print()
    print(f"summary: {same} match, {diff} differ, {unknown} not found in reference")
    return 0 if diff == 0 else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dump")
    p.add_argument("ko")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("zero")
    p.add_argument("ko")
    p.set_defaults(func=cmd_zero)

    p = sub.add_parser("compare")
    p.add_argument("refs", nargs="+")
    p.add_argument("--target", required=True)
    p.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
