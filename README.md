# Sheep Server Discord Bot

A personal Discord bot for managing a self-hosted home server running FoundryVTT, built with Python and discord.py. Provides slash commands for controlling Foundry instances, managing backups, and publishing a player-facing wiki — all from Discord.

## Features

- **Foundry Management** — start, stop, and switch between FoundryVTT v13 and v14 instances via an nginx reverse proxy
- **Server Status** — at-a-glance health check of all Foundry containers and the proxy
- **Backup & Restore** — list and restore weekly backups of Foundry data from an external drive
- **Wiki Publishing** — trigger a Quartz rebuild of the player-facing campaign wiki

## Commands

| Command | Description |
|---|---|
| `/foundry status` | Shows active version and container health for all Foundry services |
| `/foundry start <v13\|v14>` | Starts a Foundry container |
| `/foundry stop <v13\|v14>` | Stops a Foundry container |
| `/foundry switch <v13\|v14>` | Switches the active version nginx proxies to |
| `/foundry backup list <v13\|v14>` | Lists available backups for a Foundry version, newest first |
| `/foundry backup restore <v13\|v14> <n>` | Stops container, restores backup #n, restarts container |
| `/foundry wiki publish` | Rebuilds the Quartz player wiki and reloads nginx |

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
    foundry.py        # /foundry status, start, stop, switch
    backup.py         # /foundry backup list, restore
    wiki.py           # /foundry wiki publish
  core/
    auth.py           # Single-user authorization check
    docker_ctl.py     # Docker/compose subprocess calls, backup and wiki logic
```

## Setup

### Prerequisites

- Docker and Docker Compose on the host
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- FoundryVTT running via Docker Compose with an nginx reverse proxy
- An external drive mounted at `/mnt/backup-drive` for backups
- [Quartz](https://quartz.jzhao.xyz/) installed on the server for wiki builds

### Environment Variables

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your-bot-token
DISCORD_ALLOWED_USER=your-discord-user-id
```

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
- **Automated Weekly Backups** via systemd timer to an external drive

## License

This project is licensed under GPL-3.0. See [LICENSE](LICENSE) for details.