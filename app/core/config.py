"""Paths and settings, from the environment (set in compose.yaml).

Defaults match the current server layout, so the bot still runs if a value is missing.
"""
import os

FOUNDRY_DIR = os.getenv("FOUNDRY_DIR", "/home/codingsheep/docker-projects/foundry")
QUARTZ_DIR = os.getenv("QUARTZ_DIR", "/home/codingsheep/docker-projects/obsidian/quartz")
BACKUP_ROOT = os.getenv("BACKUP_ROOT", "/mnt/backup-drive")

# Shared with the host backup script: lock, requests, status files.
RUN_DIR = os.getenv("RUN_DIR", "/home/codingsheep/docker-projects/.infra/run")
BACKUP_LOCK = os.path.join(RUN_DIR, "backup.lock")
BACKUP_REQUEST = os.path.join(RUN_DIR, "backup-request")
BACKUP_STATUS = os.path.join(RUN_DIR, "backup-status.json")
DDNS_STATUS = os.path.join(RUN_DIR, "ddns-status.json")
ALERT_STATE = os.path.join(RUN_DIR, "bot-alert-state.json")

FOUNDRY_ENV_PATH = os.path.join(FOUNDRY_DIR, ".env")
FOUNDRY_COMPOSE_PATH = os.path.join(FOUNDRY_DIR, "compose.yaml")

# Cert check: connect to nginx on the host. The name to check is read from
# FOUNDRY_DOMAIN_NAME in foundry/.env, so no domain lives in this repo.
CERT_CHECK_HOST = os.getenv("CERT_CHECK_HOST", "host.docker.internal")
CERT_WARN_DAYS = int(os.getenv("CERT_WARN_DAYS", "14"))

# Must match RECENT/DAILY/WEEKLY in the host's server-backup.sh.
RETAIN_RECENT = 5
RETAIN_DAILY = 7
RETAIN_WEEKLY = 4
