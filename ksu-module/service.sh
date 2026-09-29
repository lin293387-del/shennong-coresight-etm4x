#!/system/bin/sh
#
# Reaching this script proves that the previous boot completed, i.e. that the
# coresight-etm4x.ko loaded by post-fs-data.sh did not break anything.  That is
# the signal the watchdog in post-fs-data.sh waits for, so reset the counter
# and write a one-shot capability report.
#

MODDIR=${0%/*}
STATE=/data/adb/coresight
LOG=$STATE/coresight.log
REPORT=$STATE/last-report.txt

mkdir -p "$STATE" 2>/dev/null

# give the rest of the boot a chance to settle before declaring success
sleep 30

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [service] $*" >> "$LOG" 2>/dev/null
}

if [ -f "$STATE/DISABLE" ]; then
    log "boot completed but DISABLE is set -> watchdog left alone"
    exit 0
fi

echo 0 > "$STATE/attempts"
log "boot completed -> watchdog counter reset to 0"

{
    echo "generated : $(date '+%Y-%m-%d %H:%M:%S')"
    echo "kernel    : $(uname -r)"
    echo

    if grep -q '^coresight_etm4x ' /proc/modules 2>/dev/null; then
        echo "module    : LOADED"
        grep '^coresight_etm4x ' /proc/modules
    else
        echo "module    : NOT LOADED"
    fi
    echo

    echo "coresight-ete devices:"
    ls -d /sys/bus/coresight/devices/coresight-ete* 2>/dev/null | sed 's|.*/|  |' || true

    echo
    echo "platform devices:"
    for d in /sys/bus/platform/devices/soc:ete*; do
        [ -e "$d" ] || continue
        printf '  %-16s driver=%s\n' "$(basename "$d")" \
            "$(basename "$(readlink -f "$d/driver" 2>/dev/null)" 2>/dev/null)"
    done

    echo
    if [ -r /sys/bus/event_source/devices/cs_etm/cpus ]; then
        echo "cs_etm cpus: $(cat /sys/bus/event_source/devices/cs_etm/cpus 2>/dev/null)"
    else
        echo "cs_etm cpus: (no cpus attribute -> no ETM/ETE source attached)"
    fi
    echo
    echo "verdict: $([ -e /sys/bus/coresight/devices/coresight-ete0 ] \
        && echo 'SUCCESS - ETE hardware accepted the driver' \
        || echo 'FAILED - no ete device registered (driver absent, insmod failed, or hardware/TZ gate)')"
} > "$REPORT" 2>&1

log "report written to $REPORT"
exit 0
