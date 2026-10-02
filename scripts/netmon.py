#!/usr/bin/env python3
"""Internet connection monitor: one row every run into ~/logs/netmon/netmon.csv (+ raw detail in netmon.jsonl).

Why: Plex users report buffering and we need to know whether the uplink (Comcast, ARRIS SBG8300 with no queue
management) is the cause. See docs/netmon.md. Run from cron every 30 min on production; report with netmon-report.py.

Every run (cheap, no load): idle latency/jitter/loss to the gateway, 1.1.1.1 and 8.8.8.8, what Plex is serving
(sessions, remote, transcodes, burned-in subtitles, WAN kbps, read from r9 over SSH) and what the downloaders on this host
are doing (gluetun/qBittorrent, SABnzbd), so a slow test can be blamed on the right thing.
Sometimes (saturating the line, so only when NO remote Plex stream is playing; otherwise the reason is logged):
  - upload test to two fixed servers on different networks, every ~3 h           (~100 MB each)
  - download test as well, every ~11 h                                           (~1 GB)
During each test it pings 1.1.1.1 continuously, so the latency increase under load (bufferbloat) is recorded.

Logs hold the public IP, so they live outside the repo (the repo is public). Stdlib only.
"""
import csv, datetime, fcntl, json, os, re, signal, subprocess, sys, time

LOG_DIR = os.path.expanduser('~/logs/netmon')
CSV_PATH, JSONL_PATH = f'{LOG_DIR}/netmon.csv', f'{LOG_DIR}/netmon.jsonl'
STATE_PATH, LOCK_PATH = f'{LOG_DIR}/state.json', f'{LOG_DIR}/.lock'
PINNED_SERVER = '6030'                      # fdcservers.net Ashburn VA: datacenter-grade, stable reference across runs
ALT_SERVER = '70055'                        # Brightspeed Charlottesville VA: a different network, to tell line problems from route problems
UPLOAD_EVERY_S, DOWNLOAD_EVERY_S = 170 * 60, 11 * 3600
TARGETS = {'gw': '192.168.0.1', 'cf': '1.1.1.1', 'g8': '8.8.8.8'}
LOADED_PING_TARGET = '1.1.1.1'
R9_SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'HostKeyAlias=192.168.0.149', 'jay@192.168.0.22']
PLEX_CMD = ("T=$(sudo grep -o 'PlexOnlineToken=\"[^\"]*\"' '/srv/docker/plex/Library/Application Support/Plex Media Server/"
            "Preferences.xml' | cut -d'\"' -f2); curl -s -m 8 -H \"X-Plex-Token: $T\" -H 'Accept: application/json' "
            "http://127.0.0.1:32400/status/sessions")

COLUMNS = ['ts_utc', 'ts_local', 'dow', 'hour', 'kind', 'skip_reason', 'plex_ok', 'plex_streams', 'plex_remote',
           'plex_transcodes', 'plex_burned_subs', 'plex_wan_kbps',
           'gw_loss', 'gw_avg', 'gw_max', 'gw_jitter', 'cf_loss', 'cf_avg', 'cf_max', 'cf_jitter',
           'g8_loss', 'g8_avg', 'g8_max', 'g8_jitter',
           'ctx_gluetun_rx_kbps', 'ctx_gluetun_tx_kbps', 'ctx_sab_rx_kbps', 'ctx_sab_tx_kbps']
for p in ('pin', 'alt'):                    # one block per test server
    COLUMNS += [f'{p}_server', f'{p}_km', f'{p}_ping', f'{p}_down_mbps', f'{p}_up_mbps', f'{p}_bytes_sent',
                f'{p}_bytes_recv', f'{p}_loaded_avg', f'{p}_loaded_max', f'{p}_loaded_loss', f'{p}_error']


def sh(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ''


def parse_ping(text):
    out = {'loss': '', 'avg': '', 'max': '', 'jitter': ''}
    m = re.search(r'(\d+(?:\.\d+)?)% packet loss', text)
    if m: out['loss'] = m.group(1)
    m = re.search(r'= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', text)
    if m: out['avg'], out['max'], out['jitter'] = m.group(2), m.group(3), m.group(4)
    return out


def idle_probe():
    procs = {k: subprocess.Popen(['ping', '-n', '-q', '-c', '20', '-i', '0.2', t], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True) for k, t in TARGETS.items()}
    row = {}
    for k, p in procs.items():
        try: out = p.communicate(timeout=30)[0]
        except Exception: p.kill(); out = ''
        for f, v in parse_ping(out).items(): row[f'{k}_{f}'] = v
    return row


def net_dev(container, iface):
    out = sh(['docker', 'exec', container, 'cat', '/proc/net/dev'], 10)
    for line in out.splitlines():
        if line.strip().startswith(iface + ':'):
            f = line.split(':', 1)[1].split()
            return int(f[0]), int(f[8]), time.time()
    return None


def container_rates(spec, secs=4):
    """spec: {name: (container, iface)} -> {name_rx_kbps, name_tx_kbps}, sampled over a few seconds."""
    a = {n: net_dev(*s) for n, s in spec.items()}
    time.sleep(secs)
    out = {}
    for n, s in spec.items():
        b = net_dev(*s)
        if a[n] and b and b[2] > a[n][2]:
            dt = b[2] - a[n][2]
            out[f'ctx_{n}_rx_kbps'] = round((b[0] - a[n][0]) * 8 / dt / 1000)
            out[f'ctx_{n}_tx_kbps'] = round((b[1] - a[n][1]) * 8 / dt / 1000)
    return out


def plex_state():
    """Returns (summary dict for the CSV, per-session list for the jsonl, remote_active bool or None if unknown)."""
    raw = sh(R9_SSH + [PLEX_CMD], 20)
    try: d = json.loads(raw)['MediaContainer']
    except Exception: return {'plex_ok': 0}, [], None
    sessions, remote_active, wan, tr, burn = [], False, 0, 0, 0
    for m in d.get('Metadata', []):
        pl, se, ts = m.get('Player', {}), m.get('Session', {}), m.get('TranscodeSession')
        md = (m.get('Media') or [{}])[0]
        remote = (se.get('location') or ('lan' if pl.get('local') else 'wan')) != 'lan'
        active = pl.get('state') in ('playing', 'buffering')
        if remote and active: remote_active = True
        if remote: wan += int(se.get('bandwidth') or 0)
        if ts: tr += 1
        if ts and ts.get('subtitleDecision') == 'burn': burn += 1
        sessions.append({'client': pl.get('product'), 'platform': pl.get('platform'), 'state': pl.get('state'),
                         'location': se.get('location'), 'kbps': se.get('bandwidth'),
                         'src_res': md.get('videoResolution'), 'src_kbps': md.get('bitrate'),
                         'src_vcodec': md.get('videoCodec'), 'src_acodec': md.get('audioCodec'),
                         'video': ts.get('videoDecision') if ts else 'direct', 'audio': ts.get('audioDecision') if ts else 'direct',
                         'subs': ts.get('subtitleDecision') if ts else None, 'out_height': ts.get('height') if ts else None,
                         'hw': ts.get('transcodeHwFullPipeline') if ts else None})
    return ({'plex_ok': 1, 'plex_streams': len(sessions), 'plex_remote': sum(1 for s in sessions if s['location'] != 'lan'),
             'plex_transcodes': tr, 'plex_burned_subs': burn, 'plex_wan_kbps': wan}, sessions, remote_active)


def run_test(server, download):
    """One speedtest-cli run with a continuous ping alongside to capture latency under load."""
    cmd = ['speedtest', '--json'] + ([] if download else ['--no-download']) + (['--server', server] if server else [])
    pinger = subprocess.Popen(['ping', '-n', '-q', '-i', '0.2', LOADED_PING_TARGET], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        lines = [l for l in r.stdout.strip().splitlines() if l.startswith('{')]
        data = json.loads(lines[-1]) if lines else None
        err = '' if data else (r.stderr.strip().splitlines() or ['no output'])[-1][:120]
    except Exception as e:
        data, err = None, f'{type(e).__name__}: {e}'[:120]
    finally:
        pinger.send_signal(signal.SIGINT)
        try: ping_out = pinger.communicate(timeout=10)[0]
        except Exception: pinger.kill(); ping_out = ''
    return data, err, parse_ping(ping_out)


def test_columns(prefix, data, err, loaded, download):
    c = {f'{prefix}_error': err}
    if data:
        s = data.get('server', {})
        c.update({f'{prefix}_server': f"{s.get('id')} {s.get('sponsor')} {s.get('name')}"[:60], f'{prefix}_km': round(s.get('d', 0)),
                  f'{prefix}_ping': round(data.get('ping', 0), 1), f'{prefix}_up_mbps': round(data['upload'] / 1e6, 1),
                  f'{prefix}_bytes_sent': data.get('bytes_sent'), f'{prefix}_bytes_recv': data.get('bytes_received')})
        if download: c[f'{prefix}_down_mbps'] = round(data['download'] / 1e6, 1)
    c.update({f'{prefix}_loaded_avg': loaded['avg'], f'{prefix}_loaded_max': loaded['max'], f'{prefix}_loaded_loss': loaded['loss']})
    return c


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    lock = open(LOCK_PATH, 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return                                   # previous run still going
    now = datetime.datetime.now().astimezone()
    row = {'ts_utc': now.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'ts_local': now.strftime('%F %T'),
           'dow': now.strftime('%a'), 'hour': now.hour, 'kind': 'probe', 'skip_reason': ''}
    try: state = json.load(open(STATE_PATH))
    except Exception: state = {}

    plex, sessions, remote_active = plex_state()
    row.update(plex)
    row.update(idle_probe())
    row.update(container_rates({'gluetun': ('gluetun', 'tun0'), 'sab': ('sabnzbd', 'eth0')}, secs=3))

    t = time.time()
    want_up = t - state.get('last_up', 0) >= UPLOAD_EVERY_S
    want_down = want_up and t - state.get('last_down', 0) >= DOWNLOAD_EVERY_S
    detail = {'ts': row['ts_utc'], 'plex_sessions': sessions}
    if want_up and remote_active is not False:               # None (Plex unreadable) also blocks: never risk viewers
        row['skip_reason'] = 'remote_streams' if remote_active else 'plex_unreadable'
    elif want_up:
        row['kind'] = 'full' if want_down else 'upload'
        for prefix, server in (('pin', PINNED_SERVER), ('alt', ALT_SERVER)):
            data, err, loaded = run_test(server, want_down)
            if not data and server:                          # fixed server gone: don't lose the run
                data, err, loaded = run_test(None, want_down); err = f'{server} failed, used nearest. {err}'
            row.update(test_columns(prefix, data, err, loaded, want_down))
            detail[prefix] = data
        if row.get('pin_up_mbps') or row.get('alt_up_mbps'):
            state['last_up'] = t
            if want_down and (row.get('pin_down_mbps') or row.get('alt_down_mbps')): state['last_down'] = t
            json.dump(state, open(STATE_PATH, 'w'))

    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
        if new: w.writeheader()
        w.writerow(row)
    with open(JSONL_PATH, 'a') as f:
        f.write(json.dumps({'row': row, **detail}) + '\n')


if __name__ == '__main__':
    main()
