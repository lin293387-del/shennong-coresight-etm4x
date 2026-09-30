#!/system/bin/sh
#
# CoreSight ETE loader for Xiaomi 14 Pro (shennong / SM8650) -- stage 2/3
# ----------------------------------------------------------------------
# Loads coresight-etm4x.ko in late_start service mode.  Memory only.

MODDIR=${0%/*}
STATE=/data/adb/coresight
LOG=$STATE/coresight.log
KO=$MODDIR/coresight-etm4x.ko
VERFILE=$MODDIR/verified-kernel

mkdir -p "$STATE" 2>/dev/null
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [service] $*" >> "$LOG" 2>/dev/null; }

# our own kill switch (set by the watchdog, or by hand)
if [ -f "$STATE/DISABLE" ]; then
    log "DISABLE present -> not loading"
    exit 0
fi

# ---------------------------------------------------------------------------
# Validity guard -- the reason this module cannot bootloop after a system update
# ---------------------------------------------------------------------------
# This .ko is ABI-matched and CRC-matched against ONE kernel build.  `struct
# module`'s layout (and therefore the offset of `exit` inside it) is decided by
# config switches; if a system update flips one, the loader writes kernel fields
# at offsets that mean something else in the module and panics -- that is
# exactly how the very first attempt died.
#
# The dangerous part of a crash like that is that it happens *during* the load,
# so nothing in userspace gets a chance to react.  So: never load unless the
# running kernel is byte-for-byte the one we verified against.  On a mismatch we
# simply do nothing, which leaves the device stock and healthy -- an update can
# cost you ETE, it cannot cost you the boot.
#
# Re-enabling after an update: re-run the offline gate for the new kernel
# (preflight.py --vendor ... --btf ...), rebuild in CI, then write the new
# `uname -r` into $VERFILE and reboot.
WANT=$(cat "$VERFILE" 2>/dev/null)
HAVE=$(uname -r)
if [ -n "$WANT" ] && [ "$WANT" != "$HAVE" ]; then
    rm -f "$STATE/kernel-changed"
    {
        echo "verified for: ${WANT:-<none>}"
        echo "running     : $HAVE"
        echo "since       : $(date '+%Y-%m-%d %H:%M:%S')"
    } > "$STATE/kernel-changed" 2>/dev/null
    log "REFUSING TO LOAD: built/verified for '$WANT' but running '$HAVE'."
    log "  -> kernel (or its ABI-relevant config) changed; the old .ko may panic the loader."
    log "  -> re-run the offline gate, rebuild, update $VERFILE, reboot."
    exit 0
fi
rm -f "$STATE/kernel-changed"

# already in the kernel (e.g. action.sh loaded it) -> nothing to do
if grep -q '^coresight_etm4x ' /proc/modules 2>/dev/null; then
    log "coresight_etm4x already loaded"
    exit 0
fi

if [ ! -f "$KO" ]; then
    log "ERROR: $KO missing"
    exit 0
fi

# full paths, so busybox standalone mode cannot shadow them
INSMOD=/system/bin/insmod
[ -x "$INSMOD" ] || INSMOD=insmod

# Arm the watchdog immediately before the load, and make it durable: if the
# kernel dies in insmod() the marker must survive the reboot, so flush it out of
# the page cache before handing control to the module loader.
: > "$STATE/armed"
sync
"$INSMOD" "$KO" >>"$LOG" 2>&1
rc=$?
rm -f "$STATE/armed"
sync

log "insmod rc=$rc"
if [ "$rc" -ne 0 ]; then
    log "load FAILED cleanly (kernel fine). Typical causes: unknown symbol, CRC,"
    log "vermagic, protected symbol, or the coresight stack not being present."
    exit 0
fi

# how many ETE sources did we get?
n=0
for d in /sys/bus/coresight/devices/coresight-ete*; do
    [ -e "$d" ] || continue
    n=$((n + 1))
done
log "coresight-ete devices registered: $n"

exit 0
