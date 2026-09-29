#!/usr/bin/env python3
"""Disable Kconfig `source` statements whose target file is not in the tree.

Xiaomi's OSS snapshot of `shennong-u-oss` is incomplete: `drivers/misc/Kconfig`
still contains

    source "drivers/misc/hwid/Kconfig"

while `drivers/misc/hwid/` was stripped from the release, and
`drivers/misc/Makefile` still has

    obj-$(CONFIG_MI_HARDWARE_ID)   += hwid/

The first line makes *every* `make *_defconfig` / `olddefconfig` invocation die
with

    drivers/misc/Kconfig:540: can't open file "drivers/misc/hwid/Kconfig"

so the tree cannot be configured at all.

Rather than guessing how kconfig resolves the path (kernel Kconfigs use
root-relative paths, but kconfig also accepts paths relative to the including
file), this script resolves the target under *both* conventions and only
comments the statement out when neither candidate exists.  Because the
statement disappears, the symbols it would have defined stay undefined, and the
matching `obj-$(CONFIG_...)` lines in the Makefiles evaluate to nothing - which
is exactly what we want: the stripped directories are all Xiaomi-specific
drivers (MI_HARDWARE_ID, ...) that the CoreSight module does not need.

Usage:  repair_tree.py <kernel-src>
"""

import os
import re
import sys

SOURCE_RE = re.compile(r'^(\s*)(source|osource)(\s+)("([^"]+)")(.*)$')
MARKER = "# disabled by tools/repair_tree.py"


def iter_kconfigs(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            if name == "Kconfig" or name.startswith("Kconfig."):
                yield os.path.join(dirpath, name)


def candidates(root, kpath, target):
    if target.startswith("/"):
        return [os.path.join(root, target.lstrip("/"))]
    return [
        os.path.normpath(os.path.join(os.path.dirname(kpath), target)),
        os.path.normpath(os.path.join(root, target)),
    ]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: repair_tree.py <kernel-src>")
    root = os.path.abspath(sys.argv[1])
    if not os.path.isdir(root):
        raise SystemExit(f"{root}: not a directory")

    disabled = []
    for kpath in iter_kconfigs(root):
        try:
            with open(kpath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue

        changed = False
        out = []
        for line in lines:
            if MARKER in line:
                out.append(line)
                continue
            m = SOURCE_RE.match(line)
            if not m:
                out.append(line)
                continue

            indent, keyword, _sp, quoted, target, tail = m.groups()
            if "$" in target:
                # kconfig expands $(VAR); we cannot check it statically, and
                # those are always generated or arch-local files.
                out.append(line)
                continue

            if any(os.path.exists(c) for c in candidates(root, kpath, target)):
                out.append(line)
                continue

            out.append(f"{indent}{MARKER}: missing-in-snapshot {keyword} {quoted}{tail}")
            disabled.append(
                f"{os.path.relpath(kpath, root)}: {keyword} {quoted}"
            )
            changed = True

        if changed:
            with open(kpath, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out) + "\n")

    if disabled:
        print(f"disabled {len(disabled)} dangling Kconfig source(s):")
        for item in sorted(disabled):
            print(f"  {item}")
    else:
        print("no dangling Kconfig sources found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
