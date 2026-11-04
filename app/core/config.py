"""Paths and settings, from the environment (set in compose.yaml).

Defaults match the current server layout, so the bot still runs if a value is missing.
"""
import os

FOUNDRY_DIR = os.getenv("FOUNDRY_DIR", "/home/codingsheep/projects/foundry")
QUARTZ_DIR = os.getenv("QUARTZ_DIR", "/home/codingsheep/projects/obsidian/quartz")
BACKUP_ROOT = os.getenv("BACKUP_ROOT", "/mnt/backup-drive")

# Shared with the host backup script: lock, requests, status files.
RUN_DIR = os.getenv("RUN_DIR", "/home/codingsheep/projects/.infra/run")
BACKUP_LOCK = os.path.join(RUN_DIR, "backup.lock")
BACKUP_REQUEST = os.path.join(RUN_DIR, "backup-request")
BACKUP_STATUS = os.path.join(RUN_DIR, "backup-status.json")
DDNS_STATUS = os.path.join(RUN_DIR, "ddns-status.json")
ALERT_STATE = os.path.join(RUN_DIR, "bot-alert-state.json")
# Written by the host's server-watchdog.sh (failed systemd units), and the bot's
# heartbeat that the watchdog checks.
HOST_STATUS = os.path.join(RUN_DIR, "host-status.json")
BOT_HEARTBEAT = os.path.join(RUN_DIR, "bot-heartbeat")

# Containers that should always be running. The Foundry containers aren't listed:
# they're started and stopped on purpose (/foundry start|stop, restores).
ALWAYS_ON = {n.strip() for n in os.getenv(
    "ALWAYS_ON", "nginx-proxy,certbot,obsidian-couchdb,music-player").split(",") if n.strip()}
DISK_WARN_PCT = int(os.getenv("DISK_WARN_PCT", "90"))

FOUNDRY_ENV_PATH = os.path.join(FOUNDRY_DIR, ".env")
FOUNDRY_COMPOSE_PATH = os.path.join(FOUNDRY_DIR, "compose.yaml")
# Player wiki login, read by nginx-proxy (mounted at /etc/nginx/auth). Not in git.
WIKI_HTPASSWD = os.getenv("WIKI_HTPASSWD", os.path.join(FOUNDRY_DIR, "nginx-auth", "wiki.htpasswd"))

# Cert check: connect to nginx on the host. The name to check is read from
# FOUNDRY_DOMAIN_NAME in foundry/.env, so no domain lives in this repo.
CERT_CHECK_HOST = os.getenv("CERT_CHECK_HOST", "host.docker.internal")
CERT_WARN_DAYS = int(os.getenv("CERT_WARN_DAYS", "14"))

# Must match RECENT/DAILY/WEEKLY in the host's server-backup.sh.
RETAIN_RECENT = 5
RETAIN_DAILY = 7
RETAIN_WEEKLY = 4
