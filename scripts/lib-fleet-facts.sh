#!/bin/bash
# Shared fact collection for fleet-inventory.sh and issue-env.sh (2026-09-25). Output is markdown and goes ONLY to the
# private alleyneja/mediahub-issues repo: exact software versions are useful to attackers, so never publish it.
# fleet_host_ssh <host>  -> how to reach a machine ("local" or an ssh target)
declare -A FLEET_HOSTS=([production]=local [r9]=jay@192.168.0.22 [staging]=jay@192.168.0.20)
FLEET_ORDER=(production r9 staging)

fleet_run(){ # fleet_run <machine> <bash snippet>
  local t="${FLEET_HOSTS[$1]}"
  if [ "$t" = local ]; then bash -c "$2"; else ssh -o BatchMode=yes -o ConnectTimeout=5 "$t" "$2"; fi
}

# One line of "key=value|..." system facts.
FLEET_SYS_SNIPPET='
os=$(lsb_release -ds 2>/dev/null); k=$(uname -r); up=$(uptime -p | sed "s/^up //")
d=$(docker version -f "{{.Server.Version}}" 2>/dev/null); dc=$(docker compose version --short 2>/dev/null)
nv=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)
lp=$(canonical-livepatch status 2>/dev/null | awk "/patchState:/{print \$2; exit}")
pend=$(apt list --upgradable 2>/dev/null | grep -c upgradable); rb=$([ -f /var/run/reboot-required ] && echo yes || echo no)
holds=$(apt-mark showhold 2>/dev/null | grep -vcE "nvidia|xserver-xorg-video-nvidia"); nvh=$(apt-mark showhold 2>/dev/null | grep -cE "nvidia")
echo "os=$os|kernel=$k|uptime=$up|docker=$d|compose=$dc|gpu=${nv:-none}|livepatch=${lp:-n/a}|pending_updates=$pend|reboot_required=$rb|held_nvidia_pkgs=$nvh|other_holds=$holds"'

# Tab-separated container rows: name, image, version, state, stack folder, created.
FLEET_CTR_SNIPPET='
for c in $(docker ps -a --format "{{.Names}}" | sort); do
  docker inspect -f "{{.Name}}	{{.Config.Image}}	{{index .Config.Labels \"org.opencontainers.image.version\"}}	{{.State.Status}}	{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}	{{.Created}}" "$c" 2>/dev/null
done | sed -e "s|^/||" -e "s|/home/jay/mediahub-configs/||" -e "s|<no value>|-|g"'

fleet_sys_md(){ # markdown bullet list of system facts for one machine
  fleet_run "$1" "$FLEET_SYS_SNIPPET" 2>/dev/null | tr '|' '\n' | sed -E 's/^([^=]+)=(.*)$/- **\1:** \2/'
}
fleet_ctr_md(){ # markdown table of containers; optional filter regex on name
  echo "| container | image | version | state | stack | created |"; echo "|---|---|---|---|---|---|"
  fleet_run "$1" "$FLEET_CTR_SNIPPET" 2>/dev/null | { [ -n "${2:-}" ] && grep -E "^(${2})	" || cat; } \
    | awk -F'\t' '{printf "| %s | %s | %s | %s | %s | %s |\n", $1, $2, ($3==""?"-":$3), $4, ($5==""?"-":$5), substr($6,1,10)}'
}
