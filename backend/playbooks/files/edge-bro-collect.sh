#!/bin/sh
# Node-side telemetry collector. One JSON line per invocation, appended to a
# local buffer that the orchestrator drains over the SSH channel it already
# uses for backups.
#
# Invoked by a systemd timer once a minute. Deliberately a short-lived process
# rather than a daemon: nothing to leak, nothing to restart, and the buffer
# handover below is race-free precisely because no long-lived file descriptor
# is held open.
#
# Design constraints this file exists under:
#
#   * Runs on live roadside hardware with a 15 W passive CPU. Everything here
#     is a sysfs read; no smartctl (that is pulled live at harvest time, twice
#     a day, not every minute), no subshell storms, no network.
#   * A missing sensor must never abort the run. Every field is independently
#     optional and simply omitted when unavailable, because a node whose board
#     sensor is absent still has useful CPU telemetry.
#   * The buffer is bounded. An orchestrator that cannot reach the node for
#     three months must not fill its disk.
#
# The RAPL energy counter is written raw and cumulative rather than converted
# to watts here. Deltas are taken on the orchestrator by
# core.thermal.power_watts, which knows how to handle counter wrap; doing it
# on the node would mean keeping state between invocations for no gain.
#
# POSIX sh. No bashisms, no Python assumed on the node.

set -u

BUFFER_DIR=${BUFFER_DIR:-/var/log/edge/edge-bro}
BUFFER="$BUFFER_DIR/telemetry.jsonl"

# 16 MB is roughly three months of minute samples at ~130 bytes each. Past
# that the oldest half is dropped: recent telemetry is what diagnoses a
# problem, and a full disk on a roadside unit is a worse failure than a gap
# in history.
MAX_BYTES=${MAX_BYTES:-16777216}

SCHEMA_VERSION=1

mkdir -p "$BUFFER_DIR" 2>/dev/null || exit 0

# --- helpers ----------------------------------------------------------------

# Read a file, echo nothing at all if it is missing or unreadable.
read_or_nothing() {
    [ -r "$1" ] || return 0
    cat "$1" 2>/dev/null
}

# Emit `,"key":value` only when value is non-empty, so absent sensors leave no
# key behind rather than a null the parser would have to special-case.
field() {
    [ -n "$2" ] || return 0
    printf ',"%s":%s' "$1" "$2"
}

# Same, for values that must be quoted.
field_str() {
    [ -n "$2" ] || return 0
    printf ',"%s":"%s"' "$1" "$2"
}

# Millidegrees to degrees with one decimal, which is all these sensors resolve.
millidegrees() {
    [ -n "$1" ] || return 0
    printf '%s' "$1" | awk '{printf "%.1f", $1/1000}' 2>/dev/null
}

# --- timestamp and uptime ---------------------------------------------------

TS=$(date +%s 2>/dev/null) || exit 0
UPTIME=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)

# --- CPU package energy -----------------------------------------------------

RAPL_UJ=""
RAPL_MAX=""
for d in /sys/class/powercap/intel-rapl:0 /sys/class/powercap/intel-rapl/intel-rapl:0; do
    if [ -r "$d/energy_uj" ]; then
        RAPL_UJ=$(read_or_nothing "$d/energy_uj")
        RAPL_MAX=$(read_or_nothing "$d/max_energy_range_uj")
        break
    fi
done

# --- temperatures -----------------------------------------------------------

# Package temperature: coretemp's "Package id 0" is the die reading the
# thermal model wants. x86_pkg_temp is the same sensor by another path and is
# the fallback when coretemp did not bind.
T_PKG=""
T_CORE_MAX=""
for h in /sys/class/hwmon/hwmon*; do
    [ -r "$h/name" ] || continue
    case "$(cat "$h/name" 2>/dev/null)" in
        coretemp)
            for t in "$h"/temp*_input; do
                [ -r "$t" ] || continue
                label_file=$(echo "$t" | sed 's/_input$/_label/')
                label=$(cat "$label_file" 2>/dev/null)
                value=$(cat "$t" 2>/dev/null)
                [ -n "$value" ] || continue
                case "$label" in
                    Package*) T_PKG="$value" ;;
                    Core*)
                        if [ -z "$T_CORE_MAX" ] || [ "$value" -gt "$T_CORE_MAX" ] 2>/dev/null; then
                            T_CORE_MAX="$value"
                        fi
                        ;;
                esac
            done
            ;;
    esac
done

if [ -z "$T_PKG" ]; then
    for z in /sys/class/thermal/thermal_zone*; do
        [ -r "$z/type" ] || continue
        if [ "$(cat "$z/type" 2>/dev/null)" = "x86_pkg_temp" ]; then
            T_PKG=$(read_or_nothing "$z/temp")
            break
        fi
    done
fi

# Board and drive temperatures are independent ambient proxies. The heatsink
# on these units serves the CPU alone, so neither is conducted to it — they
# couple only through enclosure air, which is exactly what makes them useful
# as a reference the CPU die cannot contaminate.
T_BOARD=""
for h in /sys/class/hwmon/hwmon*; do
    [ -r "$h/name" ] || continue
    case "$(cat "$h/name" 2>/dev/null)" in
        it87*|nct*|acpitz)
            [ -n "$T_BOARD" ] && continue
            T_BOARD=$(read_or_nothing "$h/temp1_input")
            ;;
    esac
done

T_SSD=""
for h in /sys/class/hwmon/hwmon*; do
    [ -r "$h/name" ] || continue
    if [ "$(cat "$h/name" 2>/dev/null)" = "drivetemp" ]; then
        T_SSD=$(read_or_nothing "$h/temp1_input")
        break
    fi
done

# --- throttling -------------------------------------------------------------

# Cumulative counters. Any increase between two samples invalidates the window
# for thermal fitting: while throttling, the controller cuts power to hold
# temperature, and the fit would read that correlation as a very low thermal
# resistance.
THR_PKG=$(read_or_nothing /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count)
THR_CORE=$(read_or_nothing /sys/devices/system/cpu/cpu0/thermal_throttle/core_throttle_count)

# --- load -------------------------------------------------------------------

LOAD1=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)

# Cumulative jiffies. The orchestrator differences these into a utilisation
# fraction; doing it here would need state between invocations.
CPU_LINE=$(head -1 /proc/stat 2>/dev/null)
CPU_BUSY=""
CPU_TOTAL=""
if [ -n "$CPU_LINE" ]; then
    CPU_TOTAL=$(printf '%s' "$CPU_LINE" | awk '{s=0; for(i=2;i<=NF;i++) s+=$i; print s}')
    CPU_BUSY=$(printf '%s' "$CPU_LINE" | awk '{s=0; for(i=2;i<=NF;i++) if(i!=5 && i!=6) s+=$i; print s}')
fi

# --- disk I/O ---------------------------------------------------------------

# Cumulative read/write service time in ms and completed I/Os, for the root
# device. Differenced on the orchestrator into average service time, which is
# the "access times are rising" early warning that SMART cannot see.
DISK_R_MS=""
DISK_W_MS=""
DISK_IOS=""
ROOT_SRC=$(awk '$2=="/" {print $1; exit}' /proc/mounts 2>/dev/null)
ROOT_DEV=$(basename "$ROOT_SRC" 2>/dev/null | sed 's/p\?[0-9]*$//')
if [ -n "$ROOT_DEV" ] && [ -r /proc/diskstats ]; then
    STATS=$(awk -v dev="$ROOT_DEV" '$3==dev {print $7, $11, $4+$8; exit}' /proc/diskstats 2>/dev/null)
    if [ -n "$STATS" ]; then
        DISK_R_MS=$(printf '%s' "$STATS" | cut -d' ' -f1)
        DISK_W_MS=$(printf '%s' "$STATS" | cut -d' ' -f2)
        DISK_IOS=$(printf '%s' "$STATS" | cut -d' ' -f3)
    fi
fi

# --- emit -------------------------------------------------------------------

# Built as one printf so a truncated write cannot leave a half-line that the
# parser would have to recover from. Temperatures are converted here rather
# than shipped in millidegrees purely to keep the line short.
LINE=$(
    printf '{"v":%s,"ts":%s' "$SCHEMA_VERSION" "$TS"
    field "up" "$UPTIME"
    field "rapl_uj" "$RAPL_UJ"
    field "rapl_max" "$RAPL_MAX"
    field "t_pkg" "$(millidegrees "$T_PKG")"
    field "t_core_max" "$(millidegrees "$T_CORE_MAX")"
    field "t_board" "$(millidegrees "$T_BOARD")"
    field "t_ssd" "$(millidegrees "$T_SSD")"
    field "thr_pkg" "$THR_PKG"
    field "thr_core" "$THR_CORE"
    field "load1" "$LOAD1"
    field "cpu_busy" "$CPU_BUSY"
    field "cpu_total" "$CPU_TOTAL"
    field "dr_ms" "$DISK_R_MS"
    field "dw_ms" "$DISK_W_MS"
    field "dio" "$DISK_IOS"
    printf '}'
)

# Trim the buffer before appending, so the cap holds even if the orchestrator
# has been away for months. Dropping the oldest half rather than the whole
# file keeps recent history across the trim.
if [ -f "$BUFFER" ]; then
    SIZE=$(wc -c < "$BUFFER" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt "$MAX_BYTES" ] 2>/dev/null; then
        LINES=$(wc -l < "$BUFFER" 2>/dev/null || echo 0)
        KEEP=$((LINES / 2))
        if [ "$KEEP" -gt 0 ]; then
            tail -n "$KEEP" "$BUFFER" > "$BUFFER.trim" 2>/dev/null &&
                mv "$BUFFER.trim" "$BUFFER" 2>/dev/null
        fi
    fi
fi

printf '%s\n' "$LINE" >> "$BUFFER" 2>/dev/null || exit 0
