# Declutter

**Declutter** is a self-hosted photo management tool that helps you sort, review, and archive your photo collection — straight from your browser.

![Docker Pulls](https://img.shields.io/docker/pulls/neforce/declutter)
![Docker Image Size](https://img.shields.io/docker/image-size/neforce/declutter/latest)

---

<img width="1440" height="791" alt="Declutter desktop" src="https://github.com/user-attachments/assets/608b12f5-1584-4f79-8004-ccabb6ee2940" />

<img width="526" height="348" alt="Declutter folder tree" src="https://github.com/user-attachments/assets/6626418c-ed5f-45cb-9cd3-f799e8e9dda6" />

<img width="570" height="743" alt="Declutter mobile" src="https://github.com/user-attachments/assets/144392eb-ece9-4644-9d84-daff22e2ec18" />

<img width="558" height="590" alt="Declutter lightbox" src="https://github.com/user-attachments/assets/1722ce71-3866-4cf0-9179-e5480f0af70f" />

---

## Features

- 📁 Browse your photo collection by month or folder
- ⚡ Presort raw dumps into dated folders automatically
- 💥 Burst detection — compare similar photos side by side in a lightbox
- 🗂 Drag & drop photos into albums (desktop) or use the move picker (mobile)
- 🧠 Smart date prefill — selected photos fill in the date range automatically
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
| `RAW_DIR` | `ruwe_data` | Raw dump subfolder (relative to BASE_DIR) |
| `INBOX_DIR` | `in_behandeling` | Sorting/inbox subfolder |
| `ARCHIVE_DIR` | `verwerkt` | Archive subfolder |
| `DATELESS_DIR` | `datumloos` | Archive subfolder for items without a date |
| `TRASH_DIR` | `prullenbak` | Trash subfolder |
| `DATA_DIR` | `/data` | Cache & app data (leave as-is for Docker) |
| `PORT` | `8765` | Web interface port |
| `IMMICH_URL` | — | Immich instance URL (optional) |
| `IMMICH_API_KEY` | — | Immich API key (optional) |
| `DEBUG` | `false` | Enable debug logging |
| `PRESORT_DEBUG` | `false` | Log detailed presort output per file |

> **Upgrading from an older version?** The Dutch variable names `ARCHIEF_DIR`, `UITZOEKEN_DIR`, `DUMP_DIR`, `PRULLENBAK_DIR` and `DATUMLOOS_DIR` still work but are deprecated. Declutter will print a warning on startup. Please rename them to the English equivalents listed above at your earliest convenience.

---

## Power features

A tour of the smarter features you might not notice at first glance.

### 📅 Date prefilled when creating a folder

Select one or more photos and click **+** to create a new folder. The date is filled in automatically based on your selection:

- One photo → the exact date: `2026-03-15 `
- Multiple photos spanning different days → the full range: `2026-03-15 - 2026-03-22 `

All you need to add is a name, e.g. `2026-03-15 - 2026-03-22 Holiday`.

### 📂 Create a folder and move photos in one step

If photos are selected when you create a folder, the button changes from **Create** to **Create and move** — the photos land there immediately. No extra step needed.

### 🖱️ Shift+click range selection

Click a photo, then hold **Shift** and click another: everything in between is selected (or deselected) in one go.

### 🔒 Selection persists across folders

Your selection is not lost when you switch to a different month or folder. Useful for gathering photos from multiple periods to move them all at once.

### 📍 Session restored on next visit

The browser remembers which month or folder you last viewed. On reopening, that view is loaded automatically — including a **red bookmark** ("last visited here") placed at the photo you had scrolled to.

### 💥 Compare burst groups in the lightbox

Photos taken within a few seconds of each other are automatically grouped. Click a burst group to open the lightbox:

- Give the photos you want to keep a **heart** (click or press **H**)
- Click **Confirm** — photos without a heart go to the trash
- Use **← →** to navigate through the group

### ℹ️ Photo info per thumbnail

Click the **ℹ** icon on any thumbnail for detailed information: exact capture date and time, file size, and — if the photo contains GPS data — the location on a map.

### 🔍 How dates are determined

Declutter reads the date in this order: EXIF original → EXIF digitized → date pattern in the filename → file modification time. You always get the most reliable date available.

### ⏳ Review later

Not sure about a photo? Click **⏳ Later** to move the selection to the dedicated *Review later* folder. It sits at the top of the *To sort* section so you can always find it quickly.

### 🧹 Clean up with preview

The **Clean up** button removes empty YYYY-MM folders from *To sort* (useful after finishing a period). Before anything is deleted, you see a list of exactly which folders will be removed — so you never accidentally clean up too much.

### ↔️ Adjustable panel widths

- Drag the **divider** between the folder tree and the photo grid to resize the panel.
- Use the **size slider** in the toolbar to make thumbnails larger or smaller.

Both settings are saved and restored the next time you open the app.

### 🗂 Move folders, not just photos

You can drag **folders themselves** to a different folder or to a section header (*Archived* or *No date*) to reorganise your structure.

### ✎ Rename folders inline

Double-click a folder name in the tree, or click the **✎** icon. The rename is applied to disk immediately.

### 📱 Mobile support

Dragging doesn't work well on touch screens. Instead:
1. Tap thumbnails to select photos
2. A green bar **📁 Move to…** appears at the bottom of the screen
3. Tap it to open a searchable list of all available folders

### 🔴 Live updates

Declutter watches the folder structure in the background. If files appear or disappear outside the app (e.g. via Immich or another tool), the tree updates automatically — no page reload needed. All moved files are visible in the **activity log** at the bottom of the page.

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
