#!/system/bin/sh
# collect_device_facts.sh - read-only harvest of everything the build needs.
#
# Run on the phone (root), then pull /data/local/tmp/coresight-facts.tar.gz.
# It writes NOTHING outside /data/local/tmp, touches no partition, changes no
# config, and is safe to run any number of times.
#
#   adb push tools/collect_device_facts.sh /data/local/tmp/
#   adb shell "su -c 'sh /data/local/tmp/collect_device_facts.sh'"
#   adb exec-out su -c 'cat /data/local/tmp/coresight-facts.tar.gz' > coresight-facts.tar.gz

set -u
D=/data/local/tmp/coresight-facts
rm -rf "$D"; mkdir -p "$D"

echo "[*] kernel banner"
uname -a > "$D/uname.txt" 2>&1
cat /proc/version >> "$D/uname.txt" 2>&1

echo "[*] kernel config (ground truth for the ABI)"
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz > "$D/device.config" 2>/dev/null && echo "    /proc/config.gz OK ($(wc -l < "$D/device.config") lines)"
else
    echo "    /proc/config.gz unavailable - extract it from boot.img with scripts/extract-ikconfig" | tee "$D/device.config.MISSING"
fi

echo "[*] running kernel BTF (for the struct-offset comparison)"
cat /sys/kernel/btf/vmlinux > "$D/vmlinux.btf" 2>/dev/null
ls -l "$D/vmlinux.btf" 2>/dev/null | sed 's/^/    /'

echo "[*] kallsyms"
cat /proc/kallsyms > "$D/kallsyms" 2>/dev/null
grep -c . "$D/kallsyms" | sed 's/^/    symbols: /'
grep -c 'coresight' "$D/kallsyms" | sed 's/^/    coresight symbols: /'
grep -c 'etm4' "$D/kallsyms" | sed 's/^/    etm4 symbols: /'

echo "[*] vendor modules that already load (ground truth for struct module + CRCs)"
ls -l /vendor_dlkm/lib/modules/ > "$D/vendor-modules.list" 2>&1
mkdir -p "$D/refs"
for m in coresight.ko coresight-tmc.ko; do
    src="/vendor_dlkm/lib/modules/$m"
    [ -r "$src" ] && cat "$src" > "$D/refs/$m" && echo "    copied $m"
done
cat /vendor_dlkm/lib/modules/modules.load 2>/dev/null > "$D/modules.load"
cat /vendor_dlkm/lib/modules/modules.vendor_blocklist.msm.shennong 2>/dev/null > "$D/vendor_blocklist"
cat /vendor_dlkm/lib/modules/modules.dep 2>/dev/null | head -50 > "$D/modules.dep.head"

echo "[*] CoreSight state (what works today)"
ls /sys/bus/coresight/devices/ > "$D/coresight-devices.txt" 2>&1
echo "count: $(wc -l < "$D/coresight-devices.txt")" >> "$D/coresight-devices.txt"
cat /sys/bus/event_source/devices/cs_etm/cpus > "$D/cs_etm-cpus.txt" 2>&1
ls -l /sys/bus/platform/devices/ 2>/dev/null | grep -i 'soc:ete' > "$D/platform-ete.txt" 2>&1
for i in 0 1 2 3 4 5 6 7; do
    n="/proc/device-tree/soc/ete$i"
    [ -d "$n" ] || continue
    echo "== ete$i ==" >> "$D/dt-ete.txt"
    for p in compatible status qcom,skip-power-up; do
        f="$n/$p"
        [ -e "$f" ] && echo "  $p = $(tr -d '\0' < "$f" | tr '\n' ' ')" >> "$D/dt-ete.txt"
    done
done
echo "== funnel_ete ==" >> "$D/dt-ete.txt"
ls /proc/device-tree/soc/funnel_ete/ports/ 2>/dev/null >> "$D/dt-ete.txt"

echo "[*] panic / watchdog / taint state"
for f in /proc/sys/kernel/panic_on_oops /proc/sys/kernel/panic /proc/sys/kernel/tainted; do
    printf '%s = %s\n' "$f" "$(cat $f 2>/dev/null)" >> "$D/sysctl.txt"
done
cat /proc/cmdline > "$D/cmdline.txt" 2>&1
dmesg 2>/dev/null | grep -iE 'coresight|ete|etm|watchdog|gh-watchdog|BTF' > "$D/dmesg-coresight.txt"

echo "[*] last_kmsg files (leave them alone, just list)"
ls -l /data/vendor/diag/last_kmsg* > "$D/last_kmsg.list" 2>&1

cd /data/local/tmp
tar -czf coresight-facts.tar.gz coresight-facts
echo "[*] done: /data/local/tmp/coresight-facts.tar.gz ($(wc -c < coresight-facts.tar.gz) bytes)"
echo "[*] pull with: adb exec-out su -c 'cat /data/local/tmp/coresight-facts.tar.gz' > coresight-facts.tar.gz"
