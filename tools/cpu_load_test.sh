#!/bin/bash
# All-core load test: 40 s of load, package power and temperatures measured over the middle 20 s.
set -e
sudo -v
RAPL=/sys/class/powercap/intel-rapl:0/energy_uj
K=$(grep -l k10temp /sys/class/hwmon/hwmon*/name | xargs dirname)

case "${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}" in
    zh*) MSG_WARMUP="满载中，预热 10 秒..."
         MSG_RESULT="满载功耗: %s W   Tctl 平均 %s°C / 最高 %s°C   平均频率 %s GHz\n" ;;
    *)   MSG_WARMUP="Loading all cores, warming up for 10 s..."
         MSG_RESULT="Package power: %s W   Tctl avg %s°C / max %s°C   avg clock %s GHz\n" ;;
esac

/usr/bin/python3 - 40 <<'EOF' &
import multiprocessing as mp, sys, time
def burn(end):
    x = 0
    while time.time() < end:
        for _ in range(100000):
            x = (x * 1103515245 + 12345) & 0xFFFFFFFF
end = time.time() + float(sys.argv[1])
ps = [mp.Process(target=burn, args=(end,)) for _ in range(mp.cpu_count())]
[p.start() for p in ps]
[p.join() for p in ps]
EOF
LOAD=$!

echo "$MSG_WARMUP"
sleep 10
e0=$(sudo cat $RAPL); t0=$(date +%s%N)
temps=()
for i in $(seq 1 20); do
    sleep 1
    temps+=($(( $(cat $K/temp1_input) / 1000 )))
done
e1=$(sudo cat $RAPL); t1=$(date +%s%N)
freq=$(awk '{s+=$1} END{printf "%.2f", s/NR/1e6}' /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq)
wait $LOAD

watts=$(( (e1 - e0) * 1000 / (t1 - t0) ))
avg=$(printf '%s\n' "${temps[@]}" | awk '{s+=$1} END{printf "%.1f", s/NR}')
max=$(printf '%s\n' "${temps[@]}" | sort -n | tail -1)
# shellcheck disable=SC2059
printf "$MSG_RESULT" "$watts" "$avg" "$max" "$freq"
