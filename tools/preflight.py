#!/usr/bin/env python3
"""
preflight.py - ABI pre-flight check for an out-of-tree module on a GKI/ACK device kernel.

WHY: a module can pass vermagic / CRC / signature checks and still be built against a
different `struct module` (or any other shared struct) than the running kernel.  The
loader then writes kernel fields at offsets that mean something else inside your module
- e.g. the module's own `__this_module.exit` relocation lands on the kernel's
`target_list` head, and add_usage_links() walks a "list" made of module .text bytes.
That is exactly what killed cs.ko on shennong (SM8650).

CHECKS
  1. size of .gnu.linkonce.this_module  ==  kernel sizeof(struct module)
  2. relocation offsets inside .rela.gnu.linkonce.this_module == kernel offsets of init/exit
  3. every struct present in BOTH the module DWARF and the kernel BTF must have the same
     size and the same member offsets (catches struct module, struct kernel_param, ...)

USAGE
  # strongest: needs the running kernel's BTF (device: adb exec-out su -c 'cat /sys/kernel/btf/vmlinux' > vmlinux.btf)
  python preflight.py cs.ko --btf vmlinux.btf

  # fallback without BTF: compare against a known-good module from the same device
  python preflight.py cs.ko --vendor ref-coresight.ko

Exit code 0 = no mismatch found, 1 = mismatch found (do NOT insmod).
"""
import argparse
import sys

from elftools.elf.elffile import ELFFile


# ------------------------------------------------------------------ ELF bits
def this_module_info(path):
    with open(path, "rb") as f:
        elf = ELFFile(f)
        sec = elf.get_section_by_name(".gnu.linkonce.this_module")
        size = sec["sh_size"] if sec is not None else None
        symtab = elf.get_section_by_name(".symtab")
        relocs = {}
        for rname in (".rela.gnu.linkonce.this_module", ".rel.gnu.linkonce.this_module"):
            rs = elf.get_section_by_name(rname)
            if rs is None:
                continue
            for rel in rs.iter_relocations():
                sym = symtab.get_symbol(rel["r_info_sym"]).name if symtab else "?"
                relocs[sym] = rel["r_offset"]
        return size, relocs


# ------------------------------------------------------------------ kCFI bits
#
# On arm64 kCFI (CONFIG_CFI_CLANG -> clang -fsanitize=kcfi) there is no
# __cfi_check stub: the check is *inlined at every indirect call site* as
#
#     ldur w16, [x8, #-4]      ; <- the callee's 32-bit type id
#     cmp  w16, #<expected>
#     b.ne <trap>
#
# so the number of checks tracks the number of indirect calls.  Measured on this
# device's own vendor modules (which load fine): coresight.ko 49 blr / 48 checks,
# coresight-tmc.ko 34 blr / 33 checks.  The old cs.ko had 0 / 0.
#
# Counting `__kcfi_typeid_*` symbols instead is WRONG: clang emits those absolute
# symbols only when a type id has to be materialised for a function that is
# declared but not defined in the unit (e.g. ref-tmc.ko has exactly two:
# coresight_simple_show32 / coresight_simple_show_pair, both imported).  A module
# with zero imported address-taken functions can be fully kCFI-instrumented and
# still have zero __kcfi_typeid_* symbols -- which is why the old check rejected
# a good build.
KCFI_CHECK = 0xB85FC110          # ldur w16, [x8, #-4]
BLR_MASK = 0xFFFFFC1F
BLR_OPC = 0xD63F0000             # blr xN


def kcfi_metrics(path):
    """(indirect calls, kCFI call-site checks) over every executable section."""
    blr = kcfi = 0
    with open(path, "rb") as f:
        elf = ELFFile(f)
        for sec in elf.iter_sections():
            if not (sec["sh_flags"] & 0x4):      # SHF_EXECINSTR
                continue
            d = sec.data()
            for i in range(0, len(d) - 3, 4):
                w = int.from_bytes(d[i:i + 4], "little")
                if w == KCFI_CHECK:
                    kcfi += 1
                elif (w & BLR_MASK) == BLR_OPC:
                    blr += 1
    return blr, kcfi


def typeid_prefix(path, fname):
    """The 4 bytes immediately before a function's entry == its kCFI type id.

    The kernel reaches mod->init / mod->exit through function pointers, so at
    those call sites it compares against exactly this word.  Read it from the
    device's own working vendor module and you have ground truth for "this
    module was built with kCFI" without relying on any heuristic.
    """
    with open(path, "rb") as f:
        elf = ELFFile(f)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            return None
        hit = None
        for sym in symtab.iter_symbols():
            if sym.name == fname and sym["st_shndx"] != "SHN_UNDEF":
                hit = (sym["st_shndx"], sym["st_value"])
                break
        if hit is None:
            return None
        sec = elf.get_section(hit[0])
        if sec is None or hit[1] < 4:
            return None
        f.seek(sec["sh_offset"] + (hit[1] - 4 - sec["sh_addr"]))
        return int.from_bytes(f.read(4), "little")


# ---------------------------------------------------------------- DWARF bits
def dwarf_structs(path, wanted=None):
    out = {}
    with open(path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return out
        dw = elf.get_dwarf_info()
        for cu in dw.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_structure_type":
                    continue
                attrs = die.attributes
                if "DW_AT_name" not in attrs:
                    continue
                name = attrs["DW_AT_name"].value
                name = name.decode() if isinstance(name, bytes) else name
                if wanted and name not in wanted:
                    continue
                size = attrs.get("DW_AT_byte_size")
                size = size.value if size is not None else None
                members = {}
                for child in die.iter_children():
                    if child.tag != "DW_TAG_member":
                        continue
                    ca = child.attributes
                    mname = ca.get("DW_AT_name")
                    if mname is None:
                        continue
                    mname = mname.value
                    mname = mname.decode() if isinstance(mname, bytes) else mname
                    loc = ca.get("DW_AT_data_member_location")
                    if loc is None:
                        continue
                    off = loc.value
                    if isinstance(off, list):
                        off = next((x for x in reversed(off) if isinstance(x, int)), None)
                    if off is None:
                        continue
                    members[mname] = off
                if name not in out or len(members) > len(out[name][1]):
                    out[name] = (size, members)
    return out


# ------------------------------------------------------------------ BTF bits
def btf_structs(path, wanted=None):
    with open(path, "rb") as f:
        data = f.read()
    magic, ver, flags, hdr_len, type_off, type_len, str_off, str_len = __import__("struct").unpack_from("<HBBIIIII", data, 0)
    if magic != 0xEB9F:
        raise SystemExit(f"{path}: not a BTF file (magic=0x{magic:x})")
    blob = data[hdr_len + type_off: hdr_len + type_off + type_len]
    strs = data[hdr_len + str_off: hdr_len + str_off + str_len]

    def s(off):
        if off == 0:
            return None
        end = strs.find(b"\0", off)
        return strs[off:end].decode("utf-8", "replace")

    types, p = [], 0
    while p < len(blob):
        name_off, info, size = __import__("struct").unpack_from("<III", blob, p)
        kind = (info >> 24) & 0x1F
        vlen = info & 0xFFFF
        kflag = (info >> 31) & 1
        rec = dict(name=s(name_off), kind=kind, size=size, members={}, vlen=vlen, kflag=kflag)
        p += 12
        if kind == 1:
            p += 4
        elif kind == 3:
            p += 12
        elif kind in (4, 5):
            for _ in range(vlen):
                mo, mt, moff = __import__("struct").unpack_from("<III", blob, p)
                p += 12
                mn = s(mo)
                if mn is None:
                    continue
                bits = (moff & 0xFFFFFF) if kflag else moff
                rec["members"][mn] = bits // 8
        elif kind == 6:
            p += 8 * vlen
        elif kind == 13:
            p += 8 * vlen
        elif kind == 14:
            p += 4
        elif kind == 15:
            p += 12 * vlen
        elif kind == 17:
            p += 4
        elif kind == 19:
            p += 12 * vlen
        types.append(rec)
    out = {}
    for t in types:
        if t["kind"] in (4, 5) and t["name"] and (not wanted or t["name"] in wanted):
            out[t["name"]] = (t["size"], t["members"])
    return out


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--btf", help="kernel BTF dump (e.g. from /sys/kernel/btf/vmlinux)")
    ap.add_argument("--vendor", help="known-good .ko from the same device (fallback expectations)")
    ap.add_argument("--all-structs", action="store_true",
                    help="with --btf: compare every struct in common, not just ABI-relevant ones")
    ap.add_argument("--etm4x", action="store_true",
                    help="require the ETE/ETM DT match strings (coresight-etm4x.ko only)")
    ap.add_argument("--strict-cfi", action="store_true",
                    help="deprecated no-op: the kCFI checks are always enforced now")
    args = ap.parse_args()

    problems = []
    warnings_ = []
    mod_size, mod_relocs = this_module_info(args.module)
    print(f"module            : {args.module}")
    print(f"  this_module size: {mod_size}")
    print(f"  this_module relocs: {mod_relocs}")

    # --- static checks that need no reference -------------------------------
    blr, kcfi = kcfi_metrics(args.module)
    mod_init_tid = typeid_prefix(args.module, "init_module")
    mod_exit_tid = typeid_prefix(args.module, "cleanup_module")
    print(f"  indirect calls (blr)  : {blr}")
    print(f"  kCFI call-site checks : {kcfi}")
    print(f"  init/exit type id     : "
          f"{hex(mod_init_tid) if mod_init_tid is not None else None} / "
          f"{hex(mod_exit_tid) if mod_exit_tid is not None else None}")
    if blr and not kcfi:
        problems.append(f"{blr} indirect call(s) but 0 kCFI checks: this module was NOT built "
                        f"with CONFIG_CFI_CLANG; the device kernel (kCFI, CFI_PERMISSIVE unset) "
                        f"panics on the first checked call into it")
    elif blr and kcfi * 2 < blr:
        warnings_.append(f"only {kcfi} kCFI checks for {blr} indirect calls - unexpected ratio")
    if mod_init_tid is None:
        problems.append("init_module has no room for a kCFI type-id prefix (or is missing); the "
                        "kernel calls mod->init through a function pointer and will trap")
    with open(args.module, "rb") as f:
        data = f.read()
    if args.etm4x:
        for s in (b"arm,embedded-trace-extension", b"qcom,skip-power-up", b"arm,coresight-etm4x-sysreg"):
            ok = s in data
            print(f"  dt string {s.decode():32}: {'present' if ok else 'MISSING'}")
            if not ok:
                problems.append(f"module lacks the DT match string '{s.decode()}' - it would never "
                                f"bind to the shennong ete0..7 nodes")

    if args.vendor:
        v_size, v_relocs = this_module_info(args.vendor)
        print(f"vendor reference  : {args.vendor}")
        print(f"  this_module size: {v_size}")
        print(f"  this_module relocs: {v_relocs}")
        if mod_size != v_size:
            problems.append(f".gnu.linkonce.this_module size {mod_size} != device kernel's sizeof(struct module) {v_size}")
        for sym, off in v_relocs.items():
            if sym not in mod_relocs:
                problems.append(f"vendor relocates {sym} but our module does not")
            elif mod_relocs[sym] != off:
                problems.append(f"{sym} relocated at 0x{mod_relocs[sym]:x}, device kernel expects 0x{off:x}")
        # the killer check: does any of our relocations land on a *different* kernel field?
        v_rel_by_off = {off: sym for sym, off in v_relocs.items()}
        for sym, off in mod_relocs.items():
            if off in v_rel_by_off and v_rel_by_off[off] != sym:
                problems.append(
                    f"offset 0x{off:x}: our {sym} collides with device kernel's {v_rel_by_off[off]}")

        # kCFI: the type ids the kernel compares when it calls into the module.
        # Both modules have the same function types for init/exit, so these words
        # must be identical -- this is the direct evidence that the module was
        # compiled with -fsanitize=kcfi, not an inference from symbol names.
        v_init_tid = typeid_prefix(args.vendor, "init_module")
        v_exit_tid = typeid_prefix(args.vendor, "cleanup_module")
        print(f"  init/exit type id: "
              f"{hex(v_init_tid) if v_init_tid is not None else None} / "
              f"{hex(v_exit_tid) if v_exit_tid is not None else None}")
        for label, mine, theirs in (("init_module", mod_init_tid, v_init_tid),
                                    ("cleanup_module", mod_exit_tid, v_exit_tid)):
            if theirs is None:
                continue
            if mine != theirs:
                problems.append(
                    f"{label} kCFI type id {hex(mine) if mine is not None else None} != device's "
                    f"{hex(theirs)}: the kernel compares exactly this word when it calls into the "
                    f"module, so a mismatch traps (CFI failure -> panic)")

    if args.btf:
        k = btf_structs(args.btf, None)
        m = dwarf_structs(args.module, None)
        common = sorted(set(k) & set(m))
        if not k:
            print("WARNING: the BTF file yielded no named structs. Make sure it is the BASE BTF")
            print("         (/sys/kernel/btf/vmlinux); per-module BTF under /sys/kernel/btf/<mod> is")
            print("         split BTF and can omit base types (or be name-stripped). Use --vendor then.")
        # structs where a *missing* member is also fatal (the loader / driver touches them by offset)
        ABI_CRITICAL = {
            "module", "module_kobject", "kobject", "kernel_param", "module_attribute", "attribute",
            "bin_attribute", "device", "device_driver", "platform_device", "platform_driver",
            "amba_device", "amba_driver", "coresight_desc", "coresight_platform_data",
            "coresight_ops", "coresight_ops_source", "module_layout", "mod_kallsyms",
        }
        print(f"BTF structs compared: {len(common)} of {len(k)} in kernel / {len(m)} in module DWARF")
        for name in common:
            ksize, kmem = k[name]
            msize, mmem = m[name]
            if ksize and msize and ksize != msize:
                problems.append(f"struct {name}: size {msize} != kernel {ksize}")
            for mname, moff in mmem.items():
                if mname in kmem and kmem[mname] != moff:
                    problems.append(
                        f"struct {name}.{mname}: offset 0x{moff:x} != kernel 0x{kmem[mname]:x}")
            if name in ABI_CRITICAL:
                for mname, moff in mmem.items():
                    if mname not in kmem:
                        problems.append(
                            f"struct {name}: member '{mname}' exists in our build but not in the kernel's {name}")

    print()
    for w in warnings_:
        print("WARN:", w)
    if not problems:
        print("RESULT: no ABI mismatch found. Safe to attempt insmod (keep last_kmsg capture running).")
        return 0
    print("RESULT: ABI MISMATCH - do NOT insmod (it will Oops in the module loader).")
    for p in problems:
        print("   *", p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
