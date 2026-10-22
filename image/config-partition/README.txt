SmokeOS USB
===========

1. Open rig.conf in a text editor (Notepad / TextEdit in plain text mode).
2. Set RIG_NAME, SERVER_URL, FARM_KEY, TAILSCALE_AUTHKEY and MINER_CMD.
3. Save, eject, plug the USB into the rig and boot from it.

Within a few minutes the rig shows up on your SmokeOS dashboard.

First boot takes longer (it sizes the drive, joins Tailscale and
registers with the dashboard). After that, boots take under a minute.

Tip: make one master rig.conf with your server, farm key and Tailscale
key filled in, then just change RIG_NAME for each USB.
