# Sheep Server Discord Bot

A personal Discord bot for managing a self-hosted home server running FoundryVTT, built with Python and discord.py. Provides slash commands for controlling Foundry instances, managing backups, and publishing a player-facing wiki — all from Discord.

## Features

- **Foundry Management** — start, stop, and switch between FoundryVTT v13 and v14 instances via an nginx reverse proxy
- **Server Status** — at-a-glance health check of all Foundry containers and the proxy
- **Backup & Restore** — trigger, list, and restore backups made by the host's backup script (tiered retention on an external drive)
- **Alerts** — DMs the allowed user when a backup or DDNS update fails, when the HTTPS certificate is close to expiry, when the bot loses access to Docker (with the group ID to fix), when a container stops, crashes, restarts or turns unhealthy, when a disk is nearly full, when a host service fails, and when a new kind of error shows up in any container's logs. It writes a heartbeat file so a host-side watchdog can alert (through a webhook) if the bot itself is down.
- **Wiki Publishing** — trigger a Quartz rebuild of the player-facing campaign wiki

## Commands

| Command | Description |
|---|---|
| `/foundry status` | Shows active version and container health for all Foundry services |
| `/foundry start <v13\|v14>` | Starts a Foundry container |
| `/foundry stop <v13\|v14>` | Stops a Foundry container |
| `/foundry switch <v13\|v14>` | Switches the active version nginx proxies to |
| `/foundry update <v13\|v14>` | Pulls the newest Foundry build for that version; if there is one, asks to confirm, backs up, then recreates the container |
| `/foundry backup list <v13\|v14>` | Lists backups for a Foundry version, newest first, with size, age, and retention tier |
| `/foundry backup restore <v13\|v14> <n>` | Stops the container, sets current data aside as `vNN.pre-restore-<time>` (undo), restores backup #n, restarts |
| `/foundry backup undo <v13\|v14>` | Swaps the set-aside data back in; the replaced data is set aside in turn, so running it again switches back |
| `/foundry backup now [source] [force]` | Asks the host to back up now; skips unchanged sources unless `force` |
| `/foundry backup status` | Latest backup per source, last run result, next check, free space |
| `/foundry wiki publish` | Rebuilds the Quartz player wiki (served live, no restart) |

## Tech Stack

- **Python 3.13** with [discord.py](https://discordpy.readthedocs.io/)
- **Docker** (via socket mount) for container control
- **Docker Compose** for switching active Foundry versions
- Runs as its own Docker container on the home server

## Project Structure

```
app/
  bot.py              # Bot init, registers command groups
  commands/
    foundry.py        # /foundry status, start, stop, switch, update
    backup.py         # /foundry backup list, restore, undo, now, status
    wiki.py           # /foundry wiki publish
  core/
    auth.py           # Single-user authorization check
    config.py         # Paths and settings from the environment
    backups.py        # Reading archives, retention tiers, backup requests/status
    docker_ctl.py     # Docker/compose subprocess calls, restore and wiki logic
    monitor.py        # Background alert loops: backup/DDNS failures, cert expiry, Docker access
    health.py         # Container, disk, log-error and host-service checks; heartbeat
    updates.py        # /foundry update: pull, compare, recreate
```

## Setup

### Prerequisites

- Docker and Docker Compose on the host
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- FoundryVTT running via Docker Compose with an nginx reverse proxy
- An external drive mounted at `/mnt/backup-drive` for backups
- [Quartz](https://quartz.jzhao.xyz/) installed on the server for wiki builds
- The host scripts the bot talks to through the shared run folder (`RUN_DIR`): `server-backup.sh` (backups) and `server-watchdog.sh` (failed-service reports, and a webhook alert if the bot is down). They live in the server's config repo, not here.

### Environment Variables

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your-bot-token
DISCORD_ALLOWED_USER=your-discord-user-id
```

Paths and alert settings are set in `compose.yaml` (defaults are in `app/core/config.py`):

| Variable | Default | What |
|---|---|---|
| `FOUNDRY_DIR`, `QUARTZ_DIR`, `BACKUP_ROOT`, `RUN_DIR` | server layout | Mounted paths (same inside and outside the container) |
| `ALWAYS_ON` | `nginx-proxy,certbot,obsidian-couchdb,music-player` | Containers that alert when not running |
| `DISK_WARN_PCT` | `90` | Disk-full alert threshold |
| `CERT_WARN_DAYS` | `14` | Certificate expiry alert threshold |

### Running

```bash
docker compose up -d --build
```

## Part of a Larger Setup

This bot is one component of a self-hosted home server setup that also includes:

- **FoundryVTT v13 + v14** behind an nginx reverse proxy with Let's Encrypt HTTPS
- **Encounter Music Player** (Flask) at a dedicated subdomain
- **Player Wiki** (Quartz/Obsidian) at a dedicated subdomain
- **Obsidian LiveSync** via CouchDB for GM note sync across devices
- **Automated backups** hourly while the server is on (changed data only, tiered retention) via a systemd timer to an external drive
- **Host watchdog** (systemd timer) that posts to a Discord webhook if this bot goes down

## License

This project is licensed under GPL-3.0. See [LICENSE](LICENSE) for details.