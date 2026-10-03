#!/usr/bin/env python3
"""Internet connection monitor: one row every run into ~/logs/netmon/netmon.csv (+ raw detail in netmon.jsonl).

Why: Plex users report buffering and we need to know whether the uplink (Comcast, ARRIS SBG8300 with no queue
management) is the cause. See docs/netmon.md. Run from cron every 30 min on production; report with netmon-report.py.

Every run (cheap, no load): idle latency/jitter/loss to the gateway, 1.1.1.1 and 8.8.8.8, what Plex is serving
(sessions, remote, transcodes, burned-in subtitles, WAN kbps, read from r9 over SSH) and what the downloaders on this host
are doing (gluetun/qBittorrent, SABnzbd), so a slow test can be blamed on the right thing. Also logs into the SBG8300 and
records DOCSIS signal (SNR, power, modulation) and error-codeword deltas since the last run (credentials: modem.env).
Sometimes (saturating the line, so only when NO video stream is playing, LAN included: the gateway is also the LAN
switch and Wi-Fi AP; otherwise the reason is logged):
  - upload test to two fixed servers on different networks, every ~3 h           (~100 MB each)
  - download test as well, every ~11 h                                           (~1 GB)
During each test it pings 1.1.1.1 continuously, so the latency increase under load (bufferbloat) is recorded.
Also: every run copies new Plex 'buffering' reports from r9's logs into buffering-events.csv, and `--sample` (cron, every
minute) writes minute.csv (qBittorrent/SABnzbd rates + latency) so the two can be lined up: netmon-report.py does the join.

Logs hold the public IP, so they live outside the repo (the repo is public). Stdlib only.
"""
import csv, datetime, fcntl, http.cookiejar, json, os, re, signal, ssl, statistics, subprocess, sys, time
import urllib.error, urllib.request

MODEM_URL = 'https://192.168.0.1'           # ARRIS SBG8300 gateway; login is a JSON PUT, DOCSIS tables are embedded in wan.php
MODEM_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modem.env')   # MODEM_USER (SURFboard email), MODEM_PASS; gitignored
LOG_DIR = os.path.expanduser('~/logs/netmon')
CSV_PATH, JSONL_PATH = f'{LOG_DIR}/netmon.csv', f'{LOG_DIR}/netmon.jsonl'
STATE_PATH, LOCK_PATH = f'{LOG_DIR}/state.json', f'{LOG_DIR}/.lock'
MINUTE_PATH, MINUTE_STATE_PATH, MINUTE_LOCK = f'{LOG_DIR}/minute.csv', f'{LOG_DIR}/minute-state.json', f'{LOG_DIR}/.lock-minute'
BUFFER_PATH = f'{LOG_DIR}/buffering-events.csv'
MINUTE_COLUMNS = ['ts_local', 'gluetun_rx_kbps', 'gluetun_tx_kbps', 'sab_rx_kbps', 'sab_tx_kbps', 'cf_avg', 'cf_max', 'cf_loss']
BUFFER_COLUMNS = ['ts_local', 'client', 'playback_ms', 'startup', 'rating_key', 'position_ms', 'duration_ms']   # playback_ms = time since the session began (includes pauses), position_ms = place in the movie
PINNED_SERVER = '6030'                      # fdcservers.net Ashburn VA: datacenter-grade, stable reference across runs
ALT_SERVER = '70055'                        # Brightspeed Charlottesville VA: a different network, to tell line problems from route problems
UPLOAD_EVERY_S, DOWNLOAD_EVERY_S = 170 * 60, 11 * 3600
TARGETS = {'gw': '192.168.0.1', 'cf': '1.1.1.1', 'g8': '8.8.8.8'}
LOADED_PING_TARGET = '1.1.1.1'
R9_SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'HostKeyAlias=192.168.0.149', 'jay@192.168.0.22']
PLEX_CMD = ("T=$(sudo grep -o 'PlexOnlineToken=\"[^\"]*\"' '/srv/docker/plex/Library/Application Support/Plex Media Server/"
            "Preferences.xml' | cut -d'\"' -f2); curl -s -m 8 -H \"X-Plex-Token: $T\" -H 'Accept: application/json' "
            "http://127.0.0.1:32400/status/sessions")

COLUMNS = ['ts_utc', 'ts_local', 'dow', 'hour', 'kind', 'skip_reason', 'test_start', 'test_end', 'plex_ok', 'plex_streams', 'plex_remote',
           'plex_transcodes', 'plex_burned_subs', 'plex_wan_kbps',
           'gw_loss', 'gw_avg', 'gw_max', 'gw_jitter', 'cf_loss', 'cf_avg', 'cf_max', 'cf_jitter',
           'g8_loss', 'g8_avg', 'g8_max', 'g8_jitter',
           'ctx_gluetun_rx_kbps', 'ctx_gluetun_tx_kbps', 'ctx_sab_rx_kbps', 'ctx_sab_tx_kbps',
           'modem_ok', 'modem_error', 'modem_status', 'mds_n', 'mds_locked', 'mds_pwr_min', 'mds_pwr_avg', 'mds_pwr_max',
           'mds_snr_min', 'mds_snr_avg', 'mofdm_mer', 'mus_n', 'mus_pwr_min', 'mus_pwr_max', 'mus_low_mod',
           'm_corr_total', 'm_uncorr_total', 'm_corr_delta', 'm_uncorr_delta', 'm_good_delta', 'm_reset']
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
    """Returns (summary dict for the CSV, per-session list for the jsonl, any_active bool or None if unknown)."""
    raw = sh(R9_SSH + [PLEX_CMD], 20)
    try: d = json.loads(raw)['MediaContainer']
    except Exception: return {'plex_ok': 0}, [], None
    sessions, remote_active, any_active, wan, tr, burn = [], False, False, 0, 0, 0
    for m in d.get('Metadata', []):
        pl, se, ts = m.get('Player', {}), m.get('Session', {}), m.get('TranscodeSession')
        md = (m.get('Media') or [{}])[0]
        remote = (se.get('location') or ('lan' if pl.get('local') else 'wan')) != 'lan'
        active = pl.get('state') in ('playing', 'buffering')
        if remote and active: remote_active = True
        if active and m.get('type') != 'track': any_active = True    # any video stream, LAN included; music (Plexamp) doesn't block
        if remote: wan += int(se.get('bandwidth') or 0)
        if ts: tr += 1
        if ts and ts.get('subtitleDecision') == 'burn': burn += 1
        sessions.append({'type': m.get('type'), 'client': pl.get('product'), 'platform': pl.get('platform'), 'state': pl.get('state'),
                         'location': se.get('location'), 'kbps': se.get('bandwidth'),
                         'src_res': md.get('videoResolution'), 'src_kbps': md.get('bitrate'),
                         'src_vcodec': md.get('videoCodec'), 'src_acodec': md.get('audioCodec'),
                         'video': ts.get('videoDecision') if ts else 'direct', 'audio': ts.get('audioDecision') if ts else 'direct',
                         'subs': ts.get('subtitleDecision') if ts else None, 'out_height': ts.get('height') if ts else None,
                         'hw': ts.get('transcodeHwFullPipeline') if ts else None})
    return ({'plex_ok': 1, 'plex_streams': len(sessions), 'plex_remote': sum(1 for s in sessions if s['location'] != 'lan'),
             'plex_transcodes': tr, 'plex_burned_subs': burn, 'plex_wan_kbps': wan}, sessions, any_active)


def read_env(path):
    out = {}
    try:
        for line in open(path):
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.rstrip('\n').split('=', 1); out[k.strip()] = v
    except OSError: pass
    return out


def modem_stats(state):
    """Log into the gateway, read the DOCSIS tables, log out. Returns (CSV columns, per-channel detail or None).
    A rejected password is NOT retried until modem.env changes (repeated bad logins can lock the gateway out)."""
    env = read_env(MODEM_ENV)
    if not env.get('MODEM_USER') or not env.get('MODEM_PASS'):
        return {'modem_ok': 0, 'modem_error': 'no credentials in modem.env'}, None
    mtime = os.path.getmtime(MODEM_ENV)
    if state.get('modem_bad_cred_mtime') == mtime:
        return {'modem_ok': 0, 'modem_error': 'password rejected earlier; edit modem.env to retry'}, None
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE   # self-signed gateway cert
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.HTTPSHandler(context=ctx))

    def call(path, method='GET', body=None):
        r = urllib.request.Request(MODEM_URL + path, data=body, method=method, headers={'Content-Type': 'application/json'} if body else {})
        return op.open(r, timeout=20).read().decode(errors='replace')

    try:
        call('/login.php')
        try:
            call('/actionHandler/ajaxSet_login.php', 'PUT', json.dumps({'username': env['MODEM_USER'], 'password': env['MODEM_PASS']}).encode())
        except urllib.error.HTTPError as e:
            if e.code == 409: state['modem_bad_cred_mtime'] = mtime
            return {'modem_ok': 0, 'modem_error': f'login HTTP {e.code}' + (' (credentials rejected)' if e.code == 409 else '')}, None
        html = call('/wan.php')
        d = json.loads(re.search(r'let channelData = (\{.*?\});\s*\n', html, re.S).group(1))
    except Exception as e:
        return {'modem_ok': 0, 'modem_error': f'{type(e).__name__}: {e}'[:120]}, None
    finally:
        try: call('/actionHandler/ajaxSet_logout.php', 'PUT', b'')   # free the gateway's single admin session
        except Exception: pass

    ds, us, of, ec = d.get('ds_channels', []), d.get('us_channels', []), d.get('ofdm_channels', []), d.get('error_codewords', [])
    P, S = [float(c['PowerLevel']) for c in ds], [float(c['SNRLevel']) for c in ds]
    UP = [float(c['PowerLevel']) for c in us]
    row = {'modem_ok': 1, 'modem_status': d.get('cm_status'), 'mds_n': len(ds), 'mds_locked': sum(c['LockStatus'] == 'Locked' for c in ds),
           'mus_n': len(us), 'mus_low_mod': sum(c.get('Modulation') != '64QAM' for c in us)}
    if P: row.update(mds_pwr_min=round(min(P), 1), mds_pwr_avg=round(statistics.mean(P), 1), mds_pwr_max=round(max(P), 1),
                     mds_snr_min=round(min(S), 1), mds_snr_avg=round(statistics.mean(S), 1))
    if UP: row.update(mus_pwr_min=round(min(UP), 1), mus_pwr_max=round(max(UP), 1))
    if of: row['mofdm_mer'] = round(float(of[0]['DataScAvgMer']), 1)
    tot = {k: sum(int(e[v]) for e in ec) for k, v in (('good', 'UnerroredCodewords'), ('corr', 'CorrectableCodewords'), ('uncorr', 'UncorrectableCodewords'))}
    row.update(m_corr_total=tot['corr'], m_uncorr_total=tot['uncorr'])
    prev = state.get('modem_prev')
    if prev:
        if all(tot[k] >= prev[k] for k in tot):
            row.update(m_corr_delta=tot['corr'] - prev['corr'], m_uncorr_delta=tot['uncorr'] - prev['uncorr'],
                       m_good_delta=tot['good'] - prev['good'], m_reset=0)
        else:
            row['m_reset'] = 1                               # counters went down: the modem rebooted/resynced since last run
    state['modem_prev'] = tot
    return row, {'ds': ds, 'us': us, 'ofdm': of, 'errors': ec}


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


def sample_minute():
    """`netmon.py --sample`, cron every minute: line load and latency at 1-minute resolution, so a Plex buffering event
    can be lined up with what qBittorrent/SABnzbd were doing at that moment. Rates are the average since the previous
    sample (counters are stored), so nothing sleeps and a minute's bursts are not missed."""
    os.makedirs(LOG_DIR, exist_ok=True)
    lock = open(MINUTE_LOCK, 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return
    try: prev = json.load(open(MINUTE_STATE_PATH))
    except Exception: prev = {}
    cur, row = {}, {'ts_local': datetime.datetime.now().strftime('%F %T')}
    for name, (container, iface) in {'gluetun': ('gluetun', 'tun0'), 'sab': ('sabnzbd', 'eth0')}.items():
        c = net_dev(container, iface)
        if c: cur[name] = list(c)
        p = prev.get(name)
        if c and p and 20 <= c[2] - p[2] <= 300 and c[0] >= p[0] and c[1] >= p[1]:
            dt = c[2] - p[2]
            row[f'{name}_rx_kbps'], row[f'{name}_tx_kbps'] = round((c[0] - p[0]) * 8 / dt / 1000), round((c[1] - p[1]) * 8 / dt / 1000)
    ping = parse_ping(sh(['ping', '-n', '-q', '-c', '4', '-i', '0.2', LOADED_PING_TARGET], 15))
    row.update(cf_avg=ping['avg'], cf_max=ping['max'], cf_loss=ping['loss'])
    json.dump(cur, open(MINUTE_STATE_PATH, 'w'))
    new = not os.path.exists(MINUTE_PATH)
    with open(MINUTE_PATH, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=MINUTE_COLUMNS, extrasaction='ignore')
        if new: w.writeheader()
        w.writerow(row)


def buffering_events(state):
    """New 'client reported state buffering' lines from r9's Plex logs (they rotate in ~6 h, a 30-min scan misses nothing).
    startup=1 when playback was within 3 s of the start: that is start-up wait, not a mid-play stall."""
    cmd = ("d='/srv/docker/plex/Library/Application Support/Plex Media Server/Logs'; sudo sh -c "
           "'cat \"'\"$d\"'\"/Plex\\ Media\\ Server*.log* 2>/dev/null' | grep -a 'reporting timeline state buffering'")
    raw = sh(R9_SSH + [cmd], 30)
    last, events, newest = state.get('last_buffer_ts', 0), [], 0
    for line in raw.splitlines():
        m = re.match(r'(\w{3} \d{2}, \d{4} \d\d:\d\d:\d\d)\.\d+ .*Client \[([^\]]+)\].*progress of (\d+)/(\d+)ms.*playbackTime=(\d+)ms ratingKey=(\d+)', line)
        if not m: continue
        try: ts = datetime.datetime.strptime(m.group(1), '%b %d, %Y %H:%M:%S')
        except ValueError: continue
        e = ts.timestamp()
        if e > last:
            events.append({'ts_local': ts.strftime('%F %T'), 'client': m.group(2), 'playback_ms': m.group(5),
                           'startup': int(int(m.group(5)) < 3000), 'rating_key': m.group(6),
                           'position_ms': m.group(3), 'duration_ms': m.group(4)})
            newest = max(newest, e)
    if events:
        events.sort(key=lambda r: r['ts_local'])
        if os.path.exists(BUFFER_PATH) and open(BUFFER_PATH).readline().strip() != ','.join(BUFFER_COLUMNS):
            os.rename(BUFFER_PATH, f"{LOG_DIR}/buffering-events-old-{int(time.time())}.csv")   # schema changed: keep old rows
        new = not os.path.exists(BUFFER_PATH)
        with open(BUFFER_PATH, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=BUFFER_COLUMNS)
            if new: w.writeheader()
            w.writerows(events)
        state['last_buffer_ts'] = newest
    elif raw.strip() and 'last_buffer_ts' not in state:
        state['last_buffer_ts'] = 0


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

    plex, sessions, active = plex_state()
    row.update(plex)
    buffering_events(state)
    modem_row, modem_detail = modem_stats(state)
    row.update(modem_row)
    row.update(idle_probe())
    row.update(container_rates({'gluetun': ('gluetun', 'tun0'), 'sab': ('sabnzbd', 'eth0')}, secs=3))

    t = time.time()
    want_up = t - state.get('last_up', 0) >= UPLOAD_EVERY_S
    want_down = want_up and t - state.get('last_down', 0) >= DOWNLOAD_EVERY_S
    detail = {'ts': row['ts_utc'], 'plex_sessions': sessions, 'modem': modem_detail}
    if want_up and active is not False:                      # None (Plex unreadable) also blocks: never risk viewers
        row['skip_reason'] = 'streams_active' if active else 'plex_unreadable'
    elif want_up:
        row['kind'] = 'full' if want_down else 'upload'
        row['test_start'] = datetime.datetime.now().strftime('%F %T')
        for prefix, server in (('pin', PINNED_SERVER), ('alt', ALT_SERVER)):
            data, err, loaded = run_test(server, want_down)
            if not data and server:                          # fixed server gone: don't lose the run
                data, err, loaded = run_test(None, want_down); err = f'{server} failed, used nearest. {err}'
            row.update(test_columns(prefix, data, err, loaded, want_down))
            detail[prefix] = data
        row['test_end'] = datetime.datetime.now().strftime('%F %T')
        if row.get('pin_up_mbps') or row.get('alt_up_mbps'):
            state['last_up'] = t
            if want_down and (row.get('pin_down_mbps') or row.get('alt_down_mbps')): state['last_down'] = t
    json.dump(state, open(STATE_PATH, 'w'))

    if os.path.exists(CSV_PATH) and open(CSV_PATH).readline().strip() != ','.join(COLUMNS):
        os.rename(CSV_PATH, f"{LOG_DIR}/netmon-{now.strftime('%Y%m%d-%H%M%S')}.csv")   # schema changed: keep the old rows, start fresh
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
        if new: w.writeheader()
        w.writerow(row)
    with open(JSONL_PATH, 'a') as f:
        f.write(json.dumps({'row': row, **detail}) + '\n')


if __name__ == '__main__':
    sample_minute() if '--sample' in sys.argv else main()
