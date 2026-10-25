#!/usr/bin/env python3
"""Read-only metrics collector for the service-placement review (alleyneja/mediahub-issues#83).

One JSON line per minute to /var/log/placement-metrics/<hostname>.jsonl:
  - cpu busy/iowait %, load, memory, swap in/out pages per second (/proc)
  - CPU package watts (RAPL) and GPU watts/util: average and max of 1 s samples
  - per-container CPU % and memory (docker stats)
  - mount usage for the paths listed in DISKS
Needs root (RAPL). Changes nothing on the host. Stop with: systemctl disable --now placement-metrics
"""
import glob, json, os, socket, subprocess, threading, time

OUT_DIR = "/var/log/placement-metrics"
DISKS = ["/", "/mnt/internal", "/mnt/media", "/mnt/nas", "/srv/docker"]
INTERVAL = 60

def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""

def cpu_times():
    v = list(map(int, read("/proc/stat").split("\n")[0].split()[1:]))
    return sum(v), v[3] + v[4], v[4]  # total, idle+iowait, iowait

def meminfo():
    d = {}
    for line in read("/proc/meminfo").splitlines():
        k, v = line.split(":")
        d[k] = int(v.split()[0]) // 1024
    return d

def vm_swap():
    d = dict(l.split() for l in read("/proc/vmstat").splitlines())
    return int(d.get("pswpin", 0)), int(d.get("pswpout", 0))

def rapl_paths():
    return [p for p in glob.glob("/sys/class/powercap/*/energy_uj")
            if ":" in p.split("/")[-2] and p.split("/")[-2].count(":") == 1]

def num(x):
    """float() that maps nvidia-smi's '[N/A]' (e.g. GTX 1650, Quadro P400 power) to None."""
    x = x.strip()
    return None if x.startswith("[") else float(x)

class GpuSampler(threading.Thread):
    """Streams nvidia-smi once a second; keeps per-minute watts/util samples."""
    def __init__(self):
        super().__init__(daemon=True)
        self.samples, self.lock = [], threading.Lock()

    def run(self):
        try:
            p = subprocess.Popen(
                ["nvidia-smi", "--query-gpu=power.draw,utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits", "-lms", "1000"],
                stdout=subprocess.PIPE, text=True)
        except OSError:
            return
        for line in p.stdout:
            try:
                w, u, m = [num(x) for x in line.split(",")]
            except ValueError:
                continue
            with self.lock:
                self.samples.append((w, u, m))  # power is None on cards that report [N/A]

    def drain(self):
        with self.lock:
            s, self.samples = self.samples, []
        if not s:
            return None
        w = [x[0] for x in s if x[0] is not None]
        u = [x[1] for x in s if x[1] is not None]
        return {"w_avg": round(sum(w) / len(w), 1) if w else None, "w_max": max(w) if w else None,
                "util_avg": round(sum(u) / len(u), 1) if u else None, "util_max": max(u) if u else None,
                "mem_mib": s[-1][2]}

def containers():
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=45).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    res = {}
    for line in out.splitlines():
        try:
            name, cpu, mem = line.split("\t")
            used = mem.split("/")[0].strip()
            mib = float(used[:-3]) * (1024 if used.endswith("GiB") else 1) if used.endswith("iB") else 0
            res[name] = {"cpu": float(cpu.rstrip("%")), "mem_mib": round(mib, 1)}
        except ValueError:
            continue
    return res

def disks():
    res = {}
    for m in DISKS:
        if os.path.ismount(m):
            s = os.statvfs(m)
            res[m] = round(100 * (1 - s.f_bavail / s.f_blocks), 1)
    return res

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    host = socket.gethostname()
    gpu = GpuSampler()
    gpu.start()
    rapl = rapl_paths()
    prev_e = [int(read(p) or 0) for p in rapl]
    prev_t = time.time()
    watts_samples = []
    c0, s0 = cpu_times(), vm_swap()
    last_min = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        e = [int(read(p) or 0) for p in rapl]
        dt = now - prev_t
        if rapl and dt > 0:
            watts_samples.append(sum(max(b - a, 0) for a, b in zip(prev_e, e)) / 1e6 / dt)
        prev_e, prev_t = e, now
        if now - last_min < INTERVAL:
            continue
        ctr = containers()  # takes ~2 s; the RAPL deltas above stay correct because they use real dt
        c1, s1 = cpu_times(), vm_swap()
        span = now - last_min
        tot = max(c1[0] - c0[0], 1)
        mi = meminfo()
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": host,
            "cpu_busy_pct": round(100 * (1 - (c1[1] - c0[1]) / tot), 1),
            "cpu_iowait_pct": round(100 * (c1[2] - c0[2]) / tot, 1),
            "load": [float(x) for x in read("/proc/loadavg").split()[:3]],
            "mem_avail_mib": mi.get("MemAvailable"), "mem_total_mib": mi.get("MemTotal"),
            "swap_used_mib": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0),
            "swap_in_ps": round((s1[0] - s0[0]) / span, 1), "swap_out_ps": round((s1[1] - s0[1]) / span, 1),
            "cpu_pkg_w_avg": round(sum(watts_samples) / len(watts_samples), 1) if watts_samples else None,
            "cpu_pkg_w_max": round(max(watts_samples), 1) if watts_samples else None,
            "gpu": gpu.drain(), "disk_used_pct": disks(), "containers": ctr,
        }
        with open(f"{OUT_DIR}/{host}.jsonl", "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        c0, s0, last_min, watts_samples = c1, s1, now, []

if __name__ == "__main__":
    main()
