# Epson WorkForce ES-580W → footpipe-XL

This guide wires an **Epson ES-580W** to the footpipe landing zone. The ES-580W cannot upload directly to S3/MinIO; it supports **Scan to Network Folder (SMB)**, **FTP/FTPS**, and **Epson Scan to Cloud** (Dropbox, Google Drive, Box — not S3).

**Recommended path:** Scan to a shared folder on your footpipe host, then run [`scripts/landing-watch.py`](../scripts/landing-watch.py) to upload PDFs into `landing/.../original.pdf`.

See also: [`ops-setup.md`](ops-setup.md) (general landing layout, separators, backups).

---

## Architecture

```text
ES-580W  --Wi‑Fi/LAN-->  SMB share on footpipe host (/srv/scan-inbox)
                              |
                              v
                    landing-watch.py  -->  MinIO/S3  landing/{date}/{batch}/original.pdf
                              |
                              v
                         footpipe worker (poller)
```

---

## 1. Prepare the footpipe host

On the machine running `make up` (Mac, Linux NAS, or VPS on the same LAN as the scanner):

### Create scanner inbox + archive folders

```bash
sudo mkdir -p /srv/scan-inbox/uploaded
sudo chown "$USER:$USER" /srv/scan-inbox
```

### SMB share (Linux example)

Install Samba, then add a share (adjust user/password):

```ini
# /etc/samba/smb.conf.d/footpipe-scan.conf
[footpipe-scan]
   path = /srv/scan-inbox
   browseable = yes
   read only = no
   guest ok = no
   valid users = scanner
   force user = scanner
   create mask = 0664
```

```bash
sudo useradd -m scanner   # if needed
sudo smbpasswd -a scanner
sudo systemctl restart smbd
```

**Windows:** right-click a folder → Properties → Sharing → share `scan-inbox` with a dedicated user.

Note the UNC path for the scanner, e.g. `\\footpipe-host\footpipe-scan` (Windows) or `smb://footpipe-host/footpipe-scan`.

### Start footpipe

```bash
cd footpipe-XL
cp .env.example .env   # Azure + OpenAI keys for production
make up
```

---

## 2. Configure the ES-580W (Web Config)

1. Connect the scanner to the same network (Wi‑Fi or Ethernet).
2. On the scanner: **Settings → Wi‑Fi/Network** and note the **IP address**.
3. On a PC browser: `http://<scanner-ip>/` → **Administrator Login** (default is often `admin` / printer serial — check your label or manual).
4. Open the **Scan** tab.

### Register a contact (network folder)

1. **Scan → Contacts** → pick an empty slot → **Edit**.
2. **Name:** `Footpipe Inbox`
3. **Type:** **Network Folder (SMB)**
4. **Folder path** (examples):
   - Windows share: `\\FOOTPIPE-HOST\footpipe-scan`
   - Samba on Linux: `\\footpipe-host\footpipe-scan`
5. **User name / Password:** the SMB user (`scanner` in the example above).
6. **Apply**.

Epson docs: [Setting up scan to network folder](https://files.support.epson.com/docid/cpd5/cpd59234/source/scanners/source/scanning_buttons/container_topics/setup_net_ftp_folder_container.html)  
Video: [ES-580W scan to folder (Epson)](https://www.youtube.com/watch?v=Gi_xHJS88oQ)

### Create a preset (one-tap on scanner screen)

1. **Scan → Presets** → empty slot → **Edit**.
2. **Type:** **Scan to Network Folder/FTP**
3. **Contact:** `Footpipe Inbox`
4. **Name:** `Mailroom Batch` (or similar)
5. **Quick Send Setting:** On (starts scan immediately)
6. **Scan settings** (important):

| Setting | Value |
|---------|--------|
| **File format** | PDF |
| **Multi-page** | Save all pages as **one file** |
| **Resolution** | 300 dpi (200 dpi acceptable for plain text) |
| **Color** | Auto or B&W for text-heavy mail |
| **PDF password** | **Off** (password PDFs break the pipeline) |
| **Blank page removal** | **Off** if you use blank separator sheets |

7. **Add or Remove Icon on Home** → add preset to home screen.
8. **Apply**.

### Separator sheets (between logical documents)

Feed a **blank page** or a sheet printed with `@@SEP@@` or `*** SEPARATOR ***` between stacks. See [`ops-setup.md` §4](ops-setup.md#4-separator-sheets-strongly-recommended).

---

## 3. Run the landing watcher

On the footpipe host, with object-store env vars set (from `.env` or Compose defaults):

```bash
# Load .env into shell (optional)
set -a && source .env && set +a

# Continuous watch (leave running in tmux/systemd)
python3 scripts/landing-watch.py /srv/scan-inbox
```

One-shot (process files already in the inbox):

```bash
python3 scripts/landing-watch.py /srv/scan-inbox --once
```

Or inside the api container (boto3 already installed):

```bash
docker compose exec -T api python /app/scripts/landing-watch.py /srv/scan-inbox
```

Mount the inbox into the container if you use this path — add to `docker-compose.yml` under `worker`/`api`:

```yaml
volumes:
  - /srv/scan-inbox:/scan-inbox:ro
```

The watcher uploads to `landing/{YYYY}/{MM}/{DD}/{batch_id}/original.pdf` and moves each file to `uploaded/` so it is not sent twice.

---

## 4. Test scan workflow

1. `make up` and start `landing-watch.py`.
2. Place a few pages in the ADF (include a blank separator between two stacks if testing split).
3. On the ES-580W home screen: tap **Mailroom Batch** preset.
4. Watch watcher output — you should see `[OK] ... -> s3://footpipe/landing/...`.
5. `make logs` or `docker compose logs -f worker` — batch should reach `completed`.
6. Open Paperless: http://localhost:8000 — documents should appear.
7. On failure: `curl http://localhost:8080/batches/<uuid>` (batch id from worker logs).

---

## Alternatives (not recommended for v1)

| Method | Why |
|--------|-----|
| **Epson Scan to Cloud** (Dropbox/Drive) | No native S3; requires extra sync from cloud → bucket. |
| **Epson ScanSmart on a PC** | PC must stay on; save folder + `landing-watch.py` works but adds a hop. |
| **FTP/FTPS to host** | Works if you run `vsftpd` on the footpipe host; SMB is simpler on LAN. |
| **WebDAV** | ES-580W supports HTTPS WebDAV; you'd need a WebDAV server — more setup than SMB. |

---

## Troubleshooting (ES-580W)

| Symptom | Fix |
|---------|-----|
| Scanner cannot find network folder | Same subnet/VLAN; ping host; verify `\\host\share` in Web Config test |
| Authentication failed | Recreate SMB user/password; no special chars in password (Epson limit ~20 chars) |
| PDF lands in inbox but pipeline idle | Is `landing-watch.py` running? Path must become `.../original.pdf` in bucket |
| Batch `skipped_duplicate` | Re-scan with different content or metadata |
| Garbled OCR | Increase DPI; use color for mixed mail |
| One giant document | Add blank separator sheets between stacks |

---

## systemd service (optional)

Run the watcher on boot:

```ini
# /etc/systemd/system/footpipe-landing-watch.service
[Unit]
Description=footpipe landing zone watcher (Epson ES-580W inbox)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=scanner
WorkingDirectory=/opt/footpipe-XL
EnvironmentFile=/opt/footpipe-XL/.env
ExecStart=/usr/bin/python3 /opt/footpipe-XL/scripts/landing-watch.py /srv/scan-inbox
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now footpipe-landing-watch
```
