#!/usr/bin/env bash
# Meal Planner - Proxmox VE LXC installer (community-scripts style)
#
# Run in the Proxmox VE host shell:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR-USER/family-meal-planner/main/proxmox/mealplanner.sh)"
# Update an existing container:
#   CTID=105 bash mealplanner.sh update
#
# Override defaults with environment variables, e.g.  CT_RAM=1024 CT_DISK=4 bash mealplanner.sh
set -Eeuo pipefail

APP="Meal Planner"
REPO="${REPO:-https://github.com/YOUR-USER/family-meal-planner.git}"
BRANCH="${BRANCH:-main}"
CT_HOSTNAME="${CT_HOSTNAME:-mealplanner}"
CT_CPU="${CT_CPU:-1}"
CT_RAM="${CT_RAM:-512}"
CT_DISK="${CT_DISK:-2}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_NET="${CT_NET:-dhcp}"            # dhcp or CIDR such as 192.168.1.50/24,gw=192.168.1.1

GN=$'\e[1;92m'; RD=$'\e[1;91m'; YW=$'\e[33m'; CL=$'\e[m'
msg_info() { echo -e " ${YW}-${CL} $1..."; }
msg_ok()   { echo -e " ${GN}✔${CL} $1"; }
msg_err()  { echo -e " ${RD}✘${CL} $1" >&2; }
trap 'msg_err "Failed at line $LINENO. Nothing further was changed."' ERR

[[ $EUID -eq 0 ]] || { msg_err "Run as root on the Proxmox VE host."; exit 1; }
command -v pct >/dev/null && command -v pveam >/dev/null || { msg_err "This must run on a Proxmox VE host."; exit 1; }

echo -e "\n  ${GN}${APP}${CL} - family meal planner\n"

if [[ "${1:-}" == "update" ]]; then
  : "${CTID:?Set CTID=<container id> to update}"
  msg_info "Updating ${APP} in CT ${CTID}"
  pct exec "$CTID" -- bash -c 'cd /opt/mealplanner && git pull --ff-only && ./venv/bin/pip install -q -r requirements.txt && systemctl restart mealplanner'
  msg_ok "Updated"
  exit 0
fi

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_STORAGE="${CT_STORAGE:-$(pvesm status -content rootdir | awk 'NR>1{print $1; exit}')}"
TPL_STORAGE="${TPL_STORAGE:-$(pvesm status -content vztmpl | awk 'NR>1{print $1; exit}')}"
[[ -n "$CT_STORAGE" && -n "$TPL_STORAGE" ]] || { msg_err "No suitable storage found. Set CT_STORAGE and TPL_STORAGE."; exit 1; }

msg_info "Looking for a Debian 12 template"
pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -n1)"
[[ -n "$TEMPLATE" ]] || { msg_err "No Debian 12 template available."; exit 1; }
if ! pveam list "$TPL_STORAGE" | grep -q "$TEMPLATE"; then
  pveam download "$TPL_STORAGE" "$TEMPLATE" >/dev/null
fi
msg_ok "Template ready: $TEMPLATE"

msg_info "Creating LXC ${CTID} (${CT_CPU} CPU, ${CT_RAM} MB RAM, ${CT_DISK} GB disk)"
NET0="name=eth0,bridge=${CT_BRIDGE},ip=${CT_NET}"
pct create "$CTID" "${TPL_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" --cores "$CT_CPU" --memory "$CT_RAM" --swap 256 \
  --rootfs "${CT_STORAGE}:${CT_DISK}" --net0 "$NET0" \
  --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
  --tags "mealplanner;community-script" >/dev/null
pct start "$CTID"
msg_ok "Container ${CTID} created"

msg_info "Waiting for network"
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 || { msg_err "The container has no network or DNS."; exit 1; }
msg_ok "Network is up"

msg_info "Installing ${APP} (this takes a minute)"
INSTALL="$(mktemp)"
{
  printf 'REPO=%q\nBRANCH=%q\n' "$REPO" "$BRANCH"
  cat <<'INNER'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv git ca-certificates >/dev/null
useradd --system --home /opt/mealplanner --shell /usr/sbin/nologin mealplanner || true
rm -rf /opt/mealplanner
git clone -q --depth 1 --branch "$BRANCH" "$REPO" /opt/mealplanner
python3 -m venv /opt/mealplanner/venv
/opt/mealplanner/venv/bin/pip install -q -r /opt/mealplanner/requirements.txt
install -d -o mealplanner -g mealplanner /var/lib/mealplanner
cat >/etc/systemd/system/mealplanner.service <<'UNIT'
[Unit]
Description=Meal Planner
After=network.target

[Service]
User=mealplanner
WorkingDirectory=/opt/mealplanner
Environment=DATA_DIR=/var/lib/mealplanner
ExecStart=/opt/mealplanner/venv/bin/gunicorn -b 0.0.0.0:8080 -w 1 --threads 4 app:app
Restart=on-failure
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now mealplanner
apt-get clean
INNER
} >"$INSTALL"
pct push "$CTID" "$INSTALL" /root/install-mealplanner.sh >/dev/null
pct exec "$CTID" -- bash /root/install-mealplanner.sh >/dev/null
pct exec "$CTID" -- rm -f /root/install-mealplanner.sh
rm -f "$INSTALL"
msg_ok "Installed"

IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
echo -e "\n ${GN}${APP} is ready.${CL}\n   Open: ${YW}http://${IP}:8080${CL}\n   Data: /var/lib/mealplanner (inside CT ${CTID}) - back it up with your normal CT backups\n"
