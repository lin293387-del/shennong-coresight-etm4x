#!/system/bin/sh
#
# CoreSight ETE/ETMv4 loader for Xiaomi 14 Pro (shennong / SM8650)
# ---------------------------------------------------------------
# Xiaomi's shipping vendor-module config enables the CoreSight infrastructure
# but never ships coresight-etm4x.ko, so the DT nodes ete0..7
# ("arm,embedded-trace-extension") have no driver and the perf "cs_etm" PMU
# ends up with zero CPUs.  This script loads the module at boot.
#
# Safety design
# -------------
#   * Only /data is touched.  No signed/AVB/dynamic partition is modified, so
#     there is no path to a hard brick and uninstalling this module (or just
#     deleting the files) restores the stock behaviour exactly.
#   * If insmod fails, or the driver refuses to probe, nothing else changes:
#     the ETE platform devices simply stay unbound, i.e. stock behaviour.
#   * A boot-attempt watchdog counts boots that never reached service.sh.
#     After MAX_ATTEMPTS such boots it writes DISABLE and stops loading, so a
#     module that turns out to break boot repairs itself within a few reboots.
#   * /data/adb/coresight/DISABLE is an absolute kill switch.  It is trivial to
#     create from a custom recovery or from `adb shell` if anything goes wrong.
#   * KernelSU/Magisk can also be put into safe mode by holding Volume Down
#     while booting, which skips all module scripts unconditionally.
#

MODDIR=${0%/*}
STATE=/data/adb/coresight
LOG=$STATE/coresight.log
KO=$MODDIR/coresight-etm4x.ko
MAX_ATTEMPTS=3

mkdir -p "$STATE" 2>/dev/null

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [post-fs-data] $*" >> "$LOG" 2>/dev/null
}

# ---- manual kill switch -------------------------------------------------
if [ -f "$STATE/DISABLE" ]; then
    log "DISABLE flag present -> not loading (delete $STATE/DISABLE to re-enable)"
    exit 0
fi

# ---- boot-attempt watchdog ---------------------------------------------
attempts=$(cat "$STATE/attempts" 2>/dev/null)
case "$attempts" in
    ''|*[!0-9]*) attempts=0 ;;
esac

if [ "$attempts" -ge "$MAX_ATTEMPTS" ]; then
    touch "$STATE/DISABLE"
    log "watchdog: $attempts boots never completed -> DISABLED (delete $STATE/DISABLE to retry)"
    exit 0
fi
echo $((attempts + 1)) > "$STATE/attempts"
log "boot attempt $((attempts + 1))/$MAX_ATTEMPTS"

# ---- load ---------------------------------------------------------------
if [ ! -f "$KO" ]; then
    log "missing $KO -> nothing to do"
    exit 0
fi

if grep -q '^coresight_etm4x ' /proc/modules 2>/dev/null; then
    log "coresight_etm4x already loaded"
    exit 0
fi

insmod "$KO" 2>>"$LOG"
rc=$?
log "insmod $KO rc=$rc"

# ---- report -------------------------------------------------------------
found=0
for dev in /sys/bus/coresight/devices/coresight-ete*; do
    [ -e "$dev" ] || continue
    found=$((found + 1))
    log "registered $(basename "$dev")"
done
log "ete devices registered: $found"

exit 0
