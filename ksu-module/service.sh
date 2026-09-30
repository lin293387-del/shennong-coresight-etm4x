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

# The switches that decide the layout of every struct this module shares with the
# kernel (struct module above all).  Pinned as a hash so a config change is caught
# even if `uname -r` somehow did not move.
ABI_CONFIG_RE='^CONFIG_(DEBUG_INFO_BTF_MODULES|DEBUG_INFO_BTF|CFI_CLANG|LTO_NONE|LTO_CLANG_FULL|LTO_CLANG_THIN|SHADOW_CALL_STACK|MODVERSIONS|MODULE_UNLOAD|PREEMPT|BPF_EVENTS|JUMP_LABEL|KPROBES|KUNIT|TRACEPOINTS|MODULE_SIG_PROTECT|TRIM_UNUSED_KSYMS)=|^# CONFIG_(DEBUG_INFO_BTF_MODULES|CFI_CLANG|LTO_CLANG_FULL|LTO_CLANG_THIN|PREEMPT|BPF_EVENTS|JUMP_LABEL) '
abi_hash() { zcat /proc/config.gz 2>/dev/null | grep -E "$ABI_CONFIG_RE" | sort | sha256sum | cut -d' ' -f1; }

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
ABIFILE=$MODDIR/verified-abihash
WANT_ABI=$(cat "$ABIFILE" 2>/dev/null)
HAVE_ABI=$(abi_hash)
REASON=""
[ -n "$WANT" ] && [ "$WANT" != "$HAVE" ] && REASON="uname -r moved ('$WANT' -> '$HAVE')"
if [ -z "$REASON" ] && [ -n "$WANT_ABI" ] && [ "$WANT_ABI" != "$HAVE_ABI" ]; then
    REASON="ABI config switches changed (sha256 $WANT_ABI -> $HAVE_ABI)"
fi
if [ -n "$REASON" ]; then
    {
        echo "reason      : $REASON"
        echo "verified for: ${WANT:-<none>}  abi ${WANT_ABI:-<none>}"
        echo "running     : $HAVE  abi $HAVE_ABI"
        echo "since       : $(date '+%Y-%m-%d %H:%M:%S')"
    } > "$STATE/kernel-changed" 2>/dev/null
    log "REFUSING TO LOAD: $REASON"
    log "  -> this .ko is ABI- and CRC-matched to one kernel; reloading it blindly can panic the loader."
    log "  -> re-run the offline gate, rebuild, update verified-kernel / verified-abihash, reboot."
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
