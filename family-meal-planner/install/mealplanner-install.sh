#!/usr/bin/env bash
# License: MIT
# Runs inside the new container. EDIT THE NEXT LINE to point at your own GitHub repository.
REPO_URL="https://github.com/Jacob4Redstone/Family-Meal-Planner.git"
REPO_BRANCH="main"

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing Dependencies"
$STD apt install -y python3 python3-venv git
msg_ok "Installed Dependencies"

msg_info "Installing Meal Planner"
useradd --system --home /opt/mealplanner --shell /usr/sbin/nologin mealplanner || true
$STD git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" /opt/mealplanner-repo
# The app may sit in the repo root or in a subfolder: find it and link it to /opt/mealplanner
APPDIR="$(dirname "$(find /opt/mealplanner-repo -maxdepth 3 -name app.py -not -path '*/venv/*' | head -n1)")"
[ -f "$APPDIR/requirements.txt" ] || { msg_error "app.py / requirements.txt not found in the repository"; exit 1; }
ln -sfn "$APPDIR" /opt/mealplanner
python3 -m venv /opt/mealplanner/venv
$STD /opt/mealplanner/venv/bin/pip install -r /opt/mealplanner/requirements.txt
install -d -o mealplanner -g mealplanner /var/lib/mealplanner
msg_ok "Installed Meal Planner"

msg_info "Creating Service"
cat <<'UNIT' >/etc/systemd/system/mealplanner.service
[Unit]
Description=Meal Planner
After=network.target

[Service]
User=mealplanner
WorkingDirectory=/opt/mealplanner
Environment=DATA_DIR=/var/lib/mealplanner
ExecStart=/opt/mealplanner/venv/bin/gunicorn -b 0.0.0.0:8080 -w 1 --threads 4 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable -q --now mealplanner
msg_ok "Created Service"

motd_ssh
customize
cleanup_lxc
