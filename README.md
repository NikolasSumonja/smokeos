# SmokeOS

Your own free, Hive-style mining OS:

- **Dashboard** you open on your phone (home-screen app) or any computer
- **Hive-style rig pages**: Overview, Flight Sheet, Overclocking, Autofan, Stats, Activity and Settings tabs, plus a rig switcher
- **Flight sheets**: coin + pool + wallet + miner (KRig, SRBMiner, T-Rex or a custom command), applied to any rig in one click
- **Shell**: a root terminal on any rig right in the dashboard (needs a fresh 2FA code), like Hive Shell
- **Instant commands**: buttons reach the rig in about a second
- **Per-card overclocking**: core lock, core offset, memory offset, power limit and fan for each GPU, saved on the rig and re-applied after every reboot
- **USB image**: flash with balenaEtcher, edit one file, boot. The rig adds itself.
- **Auto-updates**: publish a release on GitHub and the server + every rig update themselves
- Private and secure over **Tailscale**: nothing exposed to the internet

```
  rigs (USB, apartment) ─┐                        ┌─ iPhone app
                         ├── Tailscale (private) ─┤
  dashboard server ──────┘          ▲             └─ laptop / any computer
  (your Minecraft machine)          │
            ▲                       │
            └── GitHub releases (signed) ──► server ──► rigs
```

---

## One-time setup

### 1. Tailscale
1. Free account at tailscale.com.
2. Install Tailscale on the **server machine**, your **iPhone** and **every computer** you want to use the dashboard on. Log in with the same account.
3. Admin console → **DNS**: turn on **MagicDNS** and **HTTPS Certificates**.
4. Admin console → **Settings → Keys → Generate auth key**: tick **Reusable**. That's your `TAILSCALE_AUTHKEY` for rigs.

### 2. GitHub repo (for updates)
1. Create a repo, e.g. `smokeos` (private is fine).
2. On your computer, in this folder:
   ```bash
   python3 tools/keygen.py
   ```
   - `signing.pub` is created. **Commit it.**
   - It prints a **secret key**: repo → Settings → Secrets and variables → Actions → New secret
     `SMOKEOS_SIGNING_KEY`. Also save it in your password manager. Never commit it.
3. Push everything:
   ```bash
   git init && git add -A && git commit -m "SmokeOS"
   git remote add origin git@github.com:YOU/smokeos.git && git push -u origin main
   tools/release.sh 1.1.0
   ```
   GitHub Actions builds, signs and publishes **v1.1.0**, plus the USB image
   (`smokeos-1.1.0.img.xz`, ~15–20 min) on the repo's **Releases** page.

### 3. Server (your Minecraft machine)
Needs Python 3.10+. Put the repo folder there (`git clone`), then:
```bash
cd smokeos/server
pip install -r requirements.txt               # Linux: add --break-system-packages, or use a venv
python3 smokeos_server.py setup               # your login + 2FA (scan into an authenticator app)
python3 smokeos_server.py updates --repo YOU/smokeos
#   private repo? add:  --github-token <fine-grained token, read-only "Contents" on that repo>
```
Run it as a service so it survives reboots **and restarts itself after updates**:
```ini
# /etc/systemd/system/smokeos.service
[Unit]
Description=SmokeOS server
After=network-online.target
[Service]
WorkingDirectory=/path/to/smokeos/server
ExecStart=/usr/bin/python3 smokeos_server.py run
Restart=always
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now smokeos
tailscale serve --bg 8420
```
`tailscale serve` prints your dashboard link, like `https://minecraft-box.tail1234.ts.net`.

> Windows server: use `server\run-windows.bat` from Task Scheduler ("At startup"). It restarts the
> server automatically, including after it installs an update.

### 4. Open the dashboard
- **Computer:** open the link in any browser (Tailscale on).
- **iPhone:** open it in Safari → Share → **Add to Home Screen**.

---

## Adding a rig at home: plug and play (nothing to type)

Turn it on once, on the server:
```bash
python3 smokeos_server.py home --auto-add on \
   --public-url https://minecraft-box.tail1234.ts.net \
   --tailscale-key tskey-auth-...          # reusable key from step 1.4
sudo systemctl restart smokeos             # now also listens on your home network
```
(or dashboard → **+ Rig** → **Home plug-and-play**). If the server machine has a firewall,
allow **TCP 8420** and **UDP 8429** from your home network.

Then for each rig: flash the USB, plug it into the rig, connect the rig to your home network, boot.
That's it. The rig:
1. finds the dashboard on your home network by itself,
2. adds itself with an automatic name like `rig-3f9a1c` (rename it on the dashboard; the miner's
   worker name follows),
3. gets your Tailscale key and dashboard link, so when you carry it to the apartment and plug it in
   there, it just keeps reporting.

Re-flashing a rig's USB gives it back the same dashboard entry (history, overclock, name). It's
recognised by its network card.

Anyone on your home Wi-Fi can reach the login page while this is on. They still need your password
and 2FA code.

## Adding a rig away from home: USB (Hive-style)

1. Download `smokeos-<version>.img.xz` from your repo's Releases.
2. Flash it to a USB stick or SSD (16 GB+) with **balenaEtcher**.
3. Re-plug it: a drive called **SMOKEOS** appears. Open `rig.conf` and set:
   - `RIG_NAME`: e.g. `Little_Smoke` (or leave empty for an automatic name)
   - `SERVER_URL` and `FARM_KEY`: dashboard → **+ Rig** shows both
   - `TAILSCALE_AUTHKEY`: from step 1.4
   - `MINER_CMD`: KRig for Pearl is filled in. SRBMiner (Pearl) and T-Rex (Zano on the P106s) lines are in the file too.
   - `POWER_LIMITS`: e.g. `0:170,1:170,2:140`
4. Boot the rig from the USB. Give it ~3–5 minutes the first time. It then shows up on the dashboard.

Tip: keep one master `rig.conf` with your server, farm key and Tailscale key, and only change `RIG_NAME` per stick.

Remote shell: `ssh root@Little_Smoke` from any of your Tailscale devices (Tailscale SSH, no passwords).

What's on the image: Ubuntu 24.04 minimal, NVIDIA driver 580 (P106 / GTX 10 through RTX 50 / CMP),
KRig, SRBMiner and T-Rex, the SmokeOS agent, logs in RAM (saves the USB), auto-reboot on kernel panic.
It boots on both UEFI and old legacy-BIOS mining boards.
AMD cards (your RX 6600) need the `--amd` image build (bigger, not tested yet).

## Adding an existing Ubuntu / HiveOS rig (no USB)
Dashboard → **+ Rig** → name → copy the token. On the rig:
```bash
curl -fsSL https://tailscale.com/install.sh | sh && tailscale up
sudo bash smokeos/agent/install.sh            # --krig or --srb to also download that miner
sudo nano /etc/smokeos/agent.json             # server_url, token, miner_cmd
sudo systemctl start smokeos-agent
```
On HiveOS set `"manage_miner": false`: SmokeOS only watches and Hive keeps mining.

---

## Updating

```bash
# change code (or ask Claude to), then:
tools/release.sh 1.2.0
```
1. GitHub Actions packages + **signs** the release (and builds a new USB image).
2. Your server checks GitHub every 30 min (or **Updates → Check now**), downloads it and checks the signature.
3. Rigs with `AUTO_UPDATE=yes` install it within a minute. Others: **Updates → Update rigs**.
4. Server: **Updates → Update server** (it restarts itself in ~10 s).

Safety:
- Rigs **only install releases signed with your key** (Ed25519). A tampered download is rejected,
  even if someone got into your dashboard server.
- If a new version can't start 3 times in a row, the rig **rolls back** by itself and the
  dashboard shows a red event.

The USB system itself (kernel, drivers) doesn't change with these updates. Only SmokeOS does.
For a new driver, flash the newer image.

---

## Security

- Dashboard is only reachable inside your Tailscale network.
- Login = password (scrypt-hashed) + 2FA code. 5 wrong tries → 15 min lockout.
- Each rig gets its own token. The farm key can only *add* rigs (it can't control existing ones).
  Leaked farm key? **+ Rig → New farm key**.
- No wallet keys or seed phrases anywhere in SmokeOS.
- **Log** (top bar) lists every login, command, update and new rig.

## Flight sheets

**Flight sheets** (top bar) → **+ New flight sheet**: name, coin, miner, pool and wallet. `%RIG_NAME%` in the
wallet becomes each rig's name, so each rig shows up as its own worker on the pool. The editor shows the exact
command the rig will run. **Apply to rigs** sends it. The miner restarts with it within seconds, and the rig
keeps it after reboots. Editing a sheet updates every rig that runs it. HiveOS rigs in monitor-only mode
can't take flight sheets, because Hive runs their miner.

## Shell

Rig page → **>_ Shell** → enter your 2FA code. You get a root terminal on the rig in the browser (phone too).
It closes after 30 minutes idle. Every shell opened is in the **Log**.

## Autofan

Rig page → **Autofan**: pick a target temperature and a min/max fan speed. Each NVIDIA card's fans
follow its temperature. A fixed Fan % on the Overclocking tab overrides autofan for that card.

## Overclocking

Open a rig on the dashboard → **Overclock (per card)**. Each GPU has its own row:

| field | notes |
|---|---|
| Core lock | MHz. Locks the core clock (best for Pearl). Empty = stock |
| Core offset | MHz, + or − |
| Mem offset | **Same numbers as HiveOS** (Linux units, about 2× MSI Afterburner) |
| Power limit | Watts. Empty = the card's default |
| Fan | %. Empty = auto |

**Copy to same** copies a row to every card of the same model. **Save & apply** pushes it to the rig
within ~15 s. The rig saves it (`/etc/smokeos/oc.json`) and re-applies it after every reboot.
If one setting fails on a card (some older cards don't allow memory offsets), the rest still apply
and the rig's event log says exactly what failed. AMD cards are monitor-only for now.

Lost your 2FA phone? On the server: `python3 smokeos_server.py setup --force`.
