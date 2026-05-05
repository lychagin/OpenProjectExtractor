#!/usr/bin/env bash
# Idempotent one-time provisioning for a fresh Ubuntu 24.04 LTS VM.
# Run as root: sudo bash scripts/provision-vm.sh
#
# Sets up: docker, docker compose, ufw, certbot, the `extractor` user, the
# /srv/extractor and /srv/backups directories, and clones the repo.
#
# Does NOT do (manual handoff steps printed at the end):
#   - copy .env and .cert/bundle.pem from your laptop
#   - docker login ghcr.io with a personal access token
#   - certbot certonly to obtain the TLS certificate
#   - first `make prod-up`
#   - change DataLens admin password via UI

set -euo pipefail

if [ "$(id -u)" != "0" ]; then
    echo "Run as root (sudo bash $0)." >&2
    exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/lychagin/OpenProjectExtractor.git}"
EXTRACTOR_HOME=/srv/extractor
BACKUP_DIR=/srv/backups

echo "==> System updates"
apt-get update -y
apt-get upgrade -y

echo "==> Base packages"
apt-get install -y \
    curl ca-certificates gnupg lsb-release \
    ufw fail2ban certbot openssl

echo "==> Docker engine + compose plugin"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
else
    echo "    docker already installed: $(docker --version)"
fi

echo "==> extractor user"
if ! id -u extractor >/dev/null 2>&1; then
    useradd -m -s /bin/bash extractor
fi
usermod -aG docker extractor

echo "==> directories"
mkdir -p "$EXTRACTOR_HOME" "$BACKUP_DIR"
chown -R extractor:extractor "$EXTRACTOR_HOME" "$BACKUP_DIR"
chmod 750 "$EXTRACTOR_HOME" "$BACKUP_DIR"

echo "==> firewall (ufw)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP redirect + Lets Encrypt challenge'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

echo "==> repo clone"
if [ ! -d "$EXTRACTOR_HOME/.git" ]; then
    sudo -u extractor -H git clone "$REPO_URL" "$EXTRACTOR_HOME"
else
    echo "    repo already cloned, fetching latest"
    sudo -u extractor -H git -C "$EXTRACTOR_HOME" fetch --all --prune
    sudo -u extractor -H git -C "$EXTRACTOR_HOME" pull --ff-only
fi

echo "==> cron entries (if not present)"
CRON_FILE="/etc/cron.d/openproject-extractor"
if [ ! -f "$CRON_FILE" ]; then
    cat > "$CRON_FILE" <<'EOF'
# OpenProjectExtractor production cron (managed by provision-vm.sh).
# Runs as the extractor user.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Auto-pull the latest extractor image every 5 minutes; restarts only if image changed.
*/5 * * * * extractor cd /srv/extractor && docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml pull --quiet extractor && docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml up -d extractor >> /srv/extractor/cron.log 2>&1

# Daily Postgres backups at 03:00.
0 3 * * * extractor /srv/extractor/scripts/backup.sh >> /srv/backups/backup.log 2>&1
EOF
    chmod 644 "$CRON_FILE"
    echo "    wrote $CRON_FILE"
else
    echo "    $CRON_FILE already present, leaving as-is"
fi

cat <<'EOF'

============================================================
PROVISIONING DONE. Next steps (manual):
============================================================

1. Copy secrets to the VM (from your laptop):

   scp .env       extractor@<VM-IP>:/srv/extractor/.env
   scp -r .cert   extractor@<VM-IP>:/srv/extractor/

   On the VM:
   sudo -u extractor chmod 600 /srv/extractor/.env /srv/extractor/.cert/bundle.pem

2. Edit /srv/extractor/.env on the VM, ensure:
   - GHCR_OWNER=<your github username, lowercase>
   - SERVER_NAME=<your.subdomain.example.com>
   - All other secrets carried over from your local .env.

3. Authenticate Docker to GitHub Container Registry:

   sudo -u extractor -i
   # Generate a GitHub PAT with read:packages scope at
   # https://github.com/settings/tokens (classic, no expiry preferred)
   echo "<YOUR_GH_PAT>" | docker login ghcr.io -u <your_github_username> --password-stdin

4. Obtain TLS certificate (port 80 must be free at this point — nothing on the VM listens
   there yet):

   certbot certonly --standalone \
       -d <your.subdomain.example.com> \
       --agree-tos -m <your-email>

5. Bring up the production stack (as extractor user):

   sudo -u extractor -i
   cd /srv/extractor
   make prod-up
   docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
       logs -f extractor nginx ui

6. Visit https://<your.subdomain.example.com>/ and log in as admin/admin.
   Immediately go to Settings -> Users and change the admin password.
   Update DATALENS_ADMIN_PASSWORD in /srv/extractor/.env if you keep it there.

7. Sanity-check the cron is running:

   tail -f /srv/extractor/cron.log     # should see "no new image" pulls every 5 min
   tail -f /srv/backups/backup.log     # populated after first 03:00 run
============================================================

EOF
