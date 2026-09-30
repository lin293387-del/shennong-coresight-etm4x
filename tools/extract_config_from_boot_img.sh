#!/usr/bin/env bash
# extract_config_from_boot_img.sh - fallback when the device has no /proc/config.gz
# (CONFIG_IKCONFIG_PROC is enabled on GKI, so this should normally not be needed).
#
# Usage:  ./tools/extract_config_from_boot_img.sh boot.img > refs/device.config
#         ./tools/extract_config_from_boot_img.sh /path/to/kernel-tree boot.img
#
# It un-arms the Android boot image (v4/v3/v2 header), strips the gzip/lz4 payload
# and runs the kernel's own scripts/extract-ikconfig over it.

set -euo pipefail

if [ $# -eq 1 ]; then
    IMG="$1"; TREE="${KERNEL_TREE:-kernel}"
else
    TREE="$1"; IMG="$2"
fi

[ -f "$IMG" ] || { echo "no such image: $IMG" >&2; exit 1; }
[ -x "$TREE/scripts/extract-ikconfig" ] || { echo "kernel tree not found at $TREE (set KERNEL_TREE)" >&2; exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

python3 - "$IMG" "$TMP/kernel.bin" <<'PY'
import struct, sys
img, out = sys.argv[1], sys.argv[2]
d = open(img, "rb").read()
assert d[:8] == b"ANDROID!", "not an Android boot image"
kv = struct.unpack_from("<I", d, 8)[0]
print(f"# boot image header version {kv}", file=sys.stderr)
if kv >= 3:
    ksz, ramdisk = struct.unpack_from("<II", d, 8 + 4 + 4)
elif kv == 2:
    ksz, ramdisk = struct.unpack_from("<II", d, 8 + 4 + 4)
else:
    raise SystemExit("v0/v1 header: reuse the kernel's own mkbootimg tooling instead")
# kernel payload follows page_size-aligned header (page size = 4096 on all GKI images)
off = 4096 if kv < 4 else 4096
open(out, "wb").write(d[off:off + ksz])
print(f"# kernel payload: {ksz} bytes at 0x{off:x}", file=sys.stderr)
PY

"$TREE/scripts/extract-ikconfig" "$TMP/kernel.bin"
