#!/bin/bash
# Measurement report for the private issue tracker (2026-09-25). Answers: what's open and how old, what by machine and
# service, what keeps coming back, how long fixes take, and who fixes them.
#   issues-report.sh             print to terminal (markdown)
#   issues-report.sh --discord   also post to #service-outages via the Fleet Health webhook (monthly cron)
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
R=alleyneja/mediahub-issues
json=$(gh issue list -R "$R" --state all --limit 1000 --json number,title,state,createdAt,closedAt,labels,body)
report=$(python3 - "$json" <<'EOF'
import json, sys, datetime as dt
issues = json.loads(sys.argv[1]); now = dt.datetime.now(dt.timezone.utc)
P = lambda s: dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
def labels(i, prefix): return [l['name'].split(':', 1)[1] for l in i['labels'] if l['name'].startswith(prefix)] or ['(none)']
op = [i for i in issues if i['state'] == 'OPEN']; cl = [i for i in issues if i['state'] == 'CLOSED']
def count(items, prefix):
    c = {}
    for i in items:
        for v in labels(i, prefix): c[v] = c.get(v, 0) + 1
    return ', '.join(f'{k} {v}' for k, v in sorted(c.items(), key=lambda x: -x[1]))
out = [f'**Issues report {now:%Y-%m-%d}** - {len(op)} open, {len(cl)} closed']
import re
def opened(i):  # migrated issues carry their real start date in the body
    m = re.search(r'Originally added: (\d{4}-\d{2}-\d{2})', i.get('body') or '')
    return P(m.group(1) + 'T00:00:00+00:00') if m else P(i['createdAt'])
ages = [(now - opened(i)).days for i in op]
b = {'<7d': 0, '7-30d': 0, '30-90d': 0, '>90d': 0}
for a in ages: b['<7d' if a < 7 else '7-30d' if a < 30 else '30-90d' if a < 90 else '>90d'] += 1
out.append('Open by age: ' + ', '.join(f'{k} {v}' for k, v in b.items()))
out.append('Open by type: ' + count(op, 'type:'))
out.append('Open by priority: ' + count(op, 'priority:'))
out.append('Open by machine: ' + count(op, 'machine:'))
svc = count(op, 'svc:'); out.append('Open by service: ' + svc)
rec = [i for i in issues if any(l['name'] == 'recurring' for l in i['labels'])]
out.append(f'Recurring: {len(rec)}' + (' - ' + ', '.join(f"#{i['number']}" for i in rec) if rec else ''))
real = [i for i in cl if not any(l['name'] == 'migrated' for l in i['labels'])]
if real:
    d = sorted((P(i['closedAt']) - P(i['createdAt'])).total_seconds() / 86400 for i in real)
    out.append(f'Time to close (non-migrated, n={len(d)}): median {d[len(d)//2]:.1f} days, max {d[-1]:.1f} days')
out.append('Closed by solver: ' + count(cl, 'solved-by:'))
oldest = sorted(op, key=opened)[:3]
if oldest: out.append('Oldest open: ' + '; '.join(f"#{i['number']} {i['title'][:50]}" for i in oldest))
print('\n'.join(out))
EOF
)
echo "$report"
if [ "${1:-}" = --discord ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/fleet-healthcheck.env"
  python3 -c 'import json,sys; print(json.dumps({"username":"Fleet Health","content":sys.argv[1][:1900]}))' "$report" \
    | curl -s -m 10 -H 'Content-Type: application/json' -d @- "$FLEET_HEALTH_WEBHOOK" >/dev/null
fi
