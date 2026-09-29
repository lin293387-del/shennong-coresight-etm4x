#!/usr/bin/env python3
"""Make the tree's modpost emit a CRC of 0 for every imported symbol.

Why
---
`kernel/module/version.c :: check_version()` accepts a recorded symbol CRC of
zero unconditionally:

    for (i = 0; i < num_versions; i++) {
            if (strcmp(versions[i].name, symname) != 0) continue;
            if (versions[i].crc == 0) return 1;      /* <-- accepted */
            if (versions[i].crc == sym->crc) return 1;
            goto bad_version;
    }

and `same_magic()` skips the version prefix of the vermagic string whenever the
module carries a `__versions` section at all.  Upstream modpost only emits an
entry per symbol when it could resolve the exporting side from `Module.symvers`
(`if (!s->module) continue;`), which means a partial or symvers-less build can
end up with an *empty* `__versions` section - and a linker is free to drop a
zero-sized section, which would silently break the vermagic relaxation.

Emitting every unresolved symbol with CRC 0 does two things at once:

  * `__versions` is guaranteed to be non-empty, so `same_magic()` takes the
    `has_crcs` path and only compares the trailing vermagic flags
    ("SMP preempt mod_unload modversions aarch64");
  * every per-symbol version check short-circuits on the CRC-0 clause.

This only disables *symbol version bookkeeping*; it does not change any code,
register, or ABI.  It is applied to a throw-away CI checkout, never to the tree
that produced the device's modules.

Usage:  patch_modpost.py <kernel-src>/scripts/mod/modpost.c
"""

import sys

OLD_LOOP_HEAD = "\tlist_for_each_entry(s, &mod->unresolved_symbols, list) {\n"

OLD_SKIP_NO_MODULE = "\t\tif (!s->module)\n\t\t\tcontinue;\n"
NEW_SKIP_NO_MODULE = (
    "\t\t/* patched: keep symbols whose exporter is unknown so that the\n"
    "\t\t * __versions section is never empty (see tools/patch_modpost.py).\n"
    "\t\t */\n"
)

OLD_SKIP_NO_CRC = (
    '\t\tif (!s->crc_valid) {\n'
    '\t\t\twarn("\\"%s\\" [%s.ko] has no CRC!\\n",\n'
    '\t\t\t\ts->name, mod->name);\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
)
NEW_SKIP_NO_CRC = ""

OLD_PRINT = (
    '\t\tbuf_printf(b, "\\t{ %#8x, \\"%s\\" },\\n",\n'
    '\t\t\t   s->crc, s->name);\n'
)
NEW_PRINT = '\t\tbuf_printf(b, "\\t{ %#8x, \\"%s\\" },\\n", 0, s->name);\n'

MARKER = "patched: keep symbols whose exporter is unknown"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    path = sys.argv[1]

    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()

    if MARKER in src:
        print(f"{path}: already patched")
        return 0

    func_at = src.find("static void add_versions(struct buffer *b, struct module *mod)")
    if func_at < 0:
        raise SystemExit(f"{path}: add_versions() not found - wrong tree?")

    brace = src.find("{", func_at)
    depth = 0
    end = None
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f"{path}: unbalanced braces in add_versions()")

    body = src[brace:end]
    for label, old, new, expected in (
        ("!s->module guard", OLD_SKIP_NO_MODULE, NEW_SKIP_NO_MODULE, 1),
        ("!s->crc_valid guard", OLD_SKIP_NO_CRC, NEW_SKIP_NO_CRC, 1),
        ("crc print", OLD_PRINT, NEW_PRINT, 1),
        ("unresolved_symbols loop", OLD_LOOP_HEAD, OLD_LOOP_HEAD, 1),
    ):
        got = body.count(old)
        if got != expected:
            raise SystemExit(
                f"{path}: expected {expected} occurrence(s) of {label}, found {got}"
            )
        body = body.replace(old, new, 1)

    patched = src[:brace] + body + src[end:]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(patched)

    print(f"{path}: patched add_versions() to emit CRC 0 for every symbol")
    for line in body.splitlines():
        if "buf_printf" in line and "__versions" not in line:
            print("   ->", line.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
