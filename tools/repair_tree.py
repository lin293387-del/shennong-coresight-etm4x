#!/usr/bin/env python3
"""Stub Kconfig files that the tree references but does not contain.

Xiaomi's OSS snapshot of `shennong-u-oss` is incomplete: for example
`drivers/misc/Kconfig` still contains

    source "drivers/misc/hwid/Kconfig"

while `drivers/misc/hwid/` was stripped from the release, and
`drivers/misc/Makefile` still has

    obj-$(CONFIG_MI_HARDWARE_ID)   += hwid/

The first line makes *every* `make *_defconfig` / `olddefconfig` invocation fail
with

    drivers/misc/Kconfig:540: can't open file "drivers/misc/hwid/Kconfig"

Creating an empty stub for each dangling `source`/`osource` target fixes the
parse, and because the stub defines no symbols the matching `obj-$(CONFIG_...)`
lines in the Makefiles stay empty.  Nothing that we actually want to build is
lost - the stripped directories are all Xiaomi-specific drivers (MI_HARDWARE_ID,
etc.), none of which the CoreSight module depends on.

Usage:  repair_tree.py <kernel-src>
"""

import os
import re
import sys

SOURCE_RE = re.compile(r'^\s*(?:source|osource)\s+"([^"]+)"')


def iter_kconfigs(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            if name == "Kconfig" or name.startswith("Kconfig."):
                yield os.path.join(dirpath, name)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: repair_tree.py <kernel-src>")
    root = os.path.abspath(sys.argv[1])
    if not os.path.isdir(root):
        raise SystemExit(f"{root}: not a directory")

    created = []
    for kpath in iter_kconfigs(root):
        try:
            with open(kpath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            m = SOURCE_RE.match(line)
            if not m:
                continue
            target = m.group(1)
            # Skip anything the static parser cannot resolve.  $(VAR) forms are
            # resolved by kconfig itself (arch/$SRCARCH/...), and a leading '/'
            # is relative to the source root.
            if "$" in target:
                continue
            if target.startswith("/"):
                rel = os.path.join(root, target.lstrip("/"))
            else:
                rel = os.path.normpath(os.path.join(os.path.dirname(kpath), target))
            if os.path.exists(rel):
                continue
            os.makedirs(os.path.dirname(rel), exist_ok=True)
            with open(rel, "w", encoding="utf-8") as fh:
                fh.write(
                    "# Stub created by tools/repair_tree.py.\n"
                    f"# Referenced by {os.path.relpath(kpath, root)} as \"{target}\"\n"
                    "# but missing from Xiaomi's OSS snapshot.  An empty Kconfig\n"
                    "# keeps the tree parseable; the symbols it would have defined\n"
                    "# (and therefore the matching obj-$(CONFIG_...) Makefile lines)\n"
                    "# simply stay disabled.\n"
                )
            created.append(os.path.relpath(rel, root))

    if created:
        print(f"created {len(created)} stub Kconfig file(s):")
        for path in sorted(created):
            print(f"  {path}")
    else:
        print("no dangling Kconfig sources found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
