# Declutter

**Declutter** is a self-hosted photo management tool that helps you sort, review, and archive your photo collection — straight from your browser.

![Docker Pulls](https://img.shields.io/docker/pulls/neforce/declutter)
![Docker Image Size](https://img.shields.io/docker/image-size/neforce/declutter/latest)

---

## Features

- 📁 Browse your photo collection by month or folder
- ⚡ Presort raw dumps into dated folders automatically
- 💥 Burst detection — compare similar photos side by side
- 🗂 Drag & drop photos into albums
- 🌍 English & Dutch interface
- 🔗 Optional Immich integration
- 🐳 Runs entirely in Docker — no installation required

---

## Quick start

**1. Download the two required files**

```bash
curl -O https://raw.githubusercontent.com/neforce/declutter/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/neforce/declutter/main/.env.example
```

**2. Create your `.env`**

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```env
BASE_DIR=/photos
```

> `/photos` is the path *inside* the container. You map your actual photo folder in `docker-compose.yml` (see below).

**3. Set your photo folder in `docker-compose.yml`**

```yaml
volumes:
  - /your/photos/path:/photos   # ← change this to your actual folder
```

**4. Start**

```bash
docker compose up -d
```

Open **http://localhost:8765** in your browser.

---

## Updating

```bash
docker compose pull && docker compose up -d
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BASE_DIR` | — | Photo folder path (inside container: `/photos`) |
| `DUMP_DIR` | `ruwe_data` | Raw dump subfolder (relative to BASE_DIR) |
| `UITZOEKEN_DIR` | `in_behandeling` | Sorting subfolder |
| `ARCHIEF_DIR` | `verwerkt` | Archive subfolder |
| `PRULLENBAK_DIR` | `prullenbak` | Trash subfolder |
| `DATA_DIR` | `/data` | Cache & app data (leave as-is for Docker) |
| `PORT` | `8765` | Web interface port |
| `IMMICH_URL` | — | Immich instance URL (optional) |
| `IMMICH_API_KEY` | — | Immich API key (optional) |
| `DEBUG` | `false` | Enable debug logging |

---

## Docker Hub

[hub.docker.com/r/neforce/declutter](https://hub.docker.com/r/neforce/declutter)

---

## Support

If Declutter saves you time, consider buying me a coffee ☕

**Bitcoin:** `32u7kzd2vEDWDddTcXcEaUbuZGxQoynu5y`

[![Donate BTC](https://img.shields.io/badge/Donate-Bitcoin-f7931a?logo=bitcoin&logoColor=white)](bitcoin:32u7kzd2vEDWDddTcXcEaUbuZGxQoynu5y)

---

## License

MIT
