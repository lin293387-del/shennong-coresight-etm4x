#!/system/bin/sh
#
# CoreSight ETE loader for Xiaomi 14 Pro (shennong / SM8650) -- stage 2/3
# ----------------------------------------------------------------------
# Loads coresight-etm4x.ko in late_start service mode.  Memory only.

MODDIR=${0%/*}
STATE=/data/adb/coresight
LOG=$STATE/coresight.log
KO=$MODDIR/coresight-etm4x.ko

mkdir -p "$STATE" 2>/dev/null
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [service] $*" >> "$LOG" 2>/dev/null; }

# our own kill switch (set by the watchdog, or by hand)
if [ -f "$STATE/DISABLE" ]; then
    log "DISABLE present -> not loading"
    exit 0
fi

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

# arm the watchdog *immediately* before the load: if the kernel dies here, the
# marker survives and post-fs-data.sh on the next boot disables this module.
: > "$STATE/armed"
"$INSMOD" "$KO" >>"$LOG" 2>&1
rc=$?
rm -f "$STATE/armed"

log "insmod rc=$rc"
if [ "$rc" -ne 0 ]; then
    log "load FAILED (cleanly - the kernel is fine). Typical causes: taint/vermagic/CRC/protected-symbol."
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
