# coresight-etm4x for Xiaomi 14 Pro (shennong / SM8650)

Builds the **missing** APSS program-flow-trace driver module (`coresight-etm4x.ko`)
for the Xiaomi 14 Pro (`shennong`, SM8650, HyperOS `OS3.0.306.0.WNBCNXM`,
kernel `6.1.138-android14-11`), and packages it as a KernelSU/Magisk module.

## The finding

`/sys/bus/coresight/devices/` on the device lists 129 CoreSight devices — every
funnel, replicator, TMC (ETF/ETR), CTI, TPDM, TPDA, STM and traceNoc is present
and probes successfully. `qdss_region@82800000` is still reserved for trace
buffers. The live DT still contains the CPU trace sources:

```
/soc/ete0 .. /soc/ete7      compatible = "arm,embedded-trace-extension"
                            (no "status" property -> enabled)
funnel_ete                  wired to all eight ETE ports
```

…yet `/sys/bus/platform/devices/soc:ete0` never gets a driver, there is no
`/sys/bus/coresight/devices/coresight-ete0`, and `cs_etm/nr_addr_filters` exists
with **no** `cpus` attribute. `grep -c etm4 /proc/kallsyms` is `0`.

The reason is purely a build/packaging decision, not hardware fusing:

| config file (branch `shennong-u-oss`) | variant | `CONFIG_CORESIGHT_SOURCE_ETM4X` |
|---|---|---|
| `arch/arm64/configs/vendor/pineapple_GKI.config` | **shipping / GKI** | **absent** |
| `arch/arm64/configs/vendor/pineapple_consolidate.config` | internal debug (28 lines: LKDTM, RCU/lock torture, DEBUG_PAGEALLOC …) | `=m` |

The 13 `CONFIG_CORESIGHT_*` lines in `pineapple_GKI.config` map 1:1 to the
modules shipped in `/vendor_dlkm` — the ETM/ETE driver is simply left out of the
production config and is only enabled together with the internal debug knobs.

`drivers/hwtracing/coresight/coresight-etm4x-core.c` already supports this DT
without any change, including the Qualcomm specific bits:

```c
/* 1955-1958: sysreg access cannot use TRCPDCR */
if (!desc.access.io_mem ||
    fwnode_property_present(dev_fwnode(dev), "qcom,skip-power-up"))
        drvdata->skip_power_up = true;

/* 2157-2163: ETE is reached through the sysreg interface, so no "reg" needed */
static const struct of_device_id etm4_sysreg_match[] = {
        { .compatible = "arm,coresight-etm4x-sysreg" },
        { .compatible = "arm,embedded-trace-extension" },
        {}
};
```

## Why a module built from a different tree still loads

Three kernel behaviours add up:

1. **`kernel/module/version.c :: same_magic()`** — when the module carries a
   `__versions` section (`CONFIG_MODVERSIONS`) the kernel compares *only* the
   trailing vermagic flags:

   ```c
   if (has_crcs) {
           amagic += strcspn(amagic, " ");
           bmagic += strcspn(bmagic, " ");
   }
   return strcmp(amagic, bmagic) == 0;
   ```

   So `6.1.25-…` vs `6.1.138-…` does not matter; only
   `SMP preempt mod_unload modversions aarch64` must match.

2. **`check_version()`** — a recorded symbol CRC of `0` is accepted outright:

   ```c
   if (versions[i].crc == 0) return 1;
   pr_warn("%s: no symbol version for %s\n", …); return 1;
   ```

   That is what `tools/modversions.py zero` exploits, which is why the CI also
   emits a `-crc0` variant and the KernelSU zip uses it.

3. **`kernel/module/signing.c`** — with `CONFIG_MODULE_SIG_PROTECT=y`
   (present in the device config) signature enforcement is disabled:

   ```c
   #ifndef CONFIG_MODULE_SIG_PROTECT
           return security_locked_down(LOCKDOWN_MODULE_SIGNATURE);
   #else
           return 0;
   #endif
   ```

   The same patch also forces `sig_enforce = false`.

## Layout

```
.github/workflows/build.yml   clone kernel -> fetch AOSP clang -> build -> package
ksu-module/                   KernelSU/Magisk module skeleton (module.prop + scripts)
tools/modversions.py          dump / zero / compare `__versions` CRCs
```

## Using the artifact

Download `ksu-coresight-etm4x.zip` from the workflow run and install it with the
KernelSU (or Magisk) manager, then reboot. Verify with:

```sh
ls /sys/bus/coresight/devices/ | grep ete        # expect coresight-ete0 .. coresight-ete7
cat /sys/bus/event_source/devices/cs_etm/cpus    # expect 0-7
cat /data/adb/coresight/last-report.txt
```

If the driver binds, the hardware gate is open and ETE works. If
`coresight-ete0` never appears, check `dmesg` for `ETM arch init failed` — that
would mean the ETE registers are not reachable (a TrustZone / DBGEN issue) rather
than a missing driver.

## Rollback

Nothing outside `/data` is modified. To undo:

```sh
touch /data/adb/coresight/DISABLE      # stop loading, keep everything else
```

or remove the module from the KernelSU manager. Safe mode (hold Volume Down
while booting) skips all module scripts. The boot-attempt watchdog in
`post-fs-data.sh` already disables the module automatically after three boots
that never reach `service.sh`.
