#!/usr/bin/env bash
# Idempotent one-time provisioning for a fresh Ubuntu 24.04 LTS VM.
# Run as root: sudo bash scripts/provision-vm.sh
#
# Sets up: docker, docker compose, ufw, the `extractor` user, the
# /srv/extractor and /srv/backups directories, and clones the repo.
#
# HTTP-only LAN deployment — no TLS. For a public deployment with Let's
# Encrypt, re-introduce the certbot package install, the certbot deploy hook,
# and `ufw allow 443/tcp` (see git history before commit changing this).
#
# Does NOT do (manual handoff steps printed at the end):
#   - copy .env from your laptop
#   - docker login ghcr.io with a personal access token
#   - first `make prod-up`
#   - change DataLens admin password via UI

set -euo pipefail

if [ "$(id -u)" != "0" ]; then
    echo "Run as root (sudo bash $0)." >&2
    exit 1
fi

# Run apt non-interactively (Ubuntu 24.04 ships needrestart, which would
# otherwise intercept apt-get upgrade and hang on the service-restart dialog).
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

REPO_URL="${REPO_URL:-https://github.com/lychagin/OpenProjectExtractor.git}"
EXTRACTOR_HOME=/srv/extractor
BACKUP_DIR=/srv/backups

echo "==> System updates"
apt-get update -y
apt-get upgrade -y

echo "==> Base packages"
apt-get install -y \
    curl ca-certificates gnupg lsb-release \
    ufw fail2ban openssl make

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
ufw allow 80/tcp comment 'HTTP (LAN)'
ufw --force enable

echo "==> repo clone"
if [ ! -d "$EXTRACTOR_HOME/.git" ]; then
    # Disable git's credential prompts — fail fast if auth is needed.
    if ! sudo -u extractor -H GIT_TERMINAL_PROMPT=0 \
            git clone "$REPO_URL" "$EXTRACTOR_HOME" 2>&1; then
        echo ""
        echo "    git clone failed — likely a private repo without credentials."
        echo "    Recover by either:"
        echo "      (a) scp the repo from your laptop to $EXTRACTOR_HOME on the VM, OR"
        echo "      (b) re-run with auth: REPO_URL=https://<PAT>@github.com/lychagin/OpenProjectExtractor.git \\"
        echo "          sudo bash $0"
        echo ""
        echo "    Continuing — cron file and other infrastructure will still be set up."
    fi
else
    echo "    repo already cloned, fetching latest"
    sudo -u extractor -H git -C "$EXTRACTOR_HOME" fetch --all --prune || \
        echo "    fetch failed (auth?), continuing"
    sudo -u extractor -H git -C "$EXTRACTOR_HOME" pull --ff-only || \
        echo "    pull failed (diverged or auth?), continuing"
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
   sudo -u extractor chmod 600 /srv/extractor/.env
   # .cert/*.pem must stay world-readable (644) — the extractor container
   # runs as a non-root UID different from the host's `extractor` user,
   # and the CA bundle is public anyway (no private keys in there).

2. Edit /srv/extractor/.env on the VM, ensure:
   - GHCR_OWNER=<your github username, lowercase>
   - SERVER_NAME=<VM hostname or IP, e.g. 192.168.1.31>
   - AUTH_ADMIN_PASSWORD=<initial DataLens admin password>
   - All other secrets carried over from your local .env.

3. Authenticate Docker to GitHub Container Registry:

   sudo -u extractor -i
   # Generate a GitHub PAT with read:packages scope at
   # https://github.com/settings/tokens (classic, no expiry preferred)
   echo "<YOUR_GH_PAT>" | docker login ghcr.io -u <your_github_username> --password-stdin

4. Bring up the production stack (as extractor user):

   sudo -u extractor -i
   cd /srv/extractor
   make prod-up
   docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
       logs -f extractor nginx ui

5. Visit http://<SERVER_NAME>/ and log in as admin / <AUTH_ADMIN_PASSWORD>.
   Change the admin password via the DataLens UI if you want a different one.

6. Sanity-check the cron is running:

   tail -f /srv/extractor/cron.log     # should see "no new image" pulls every 5 min
   tail -f /srv/backups/backup.log     # populated after first 03:00 run
============================================================

EOF
