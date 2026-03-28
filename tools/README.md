# Role Distribution Config Server (local)

This is a tiny Flask server + static UI to edit and save `roles/role_distribution.json` for the werewolf bot.

Placement: put this `tools/` folder under the repository root (it is already created by the assistant).

Quick start (Windows PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r tools\requirements.txt
python tools\config_server.py
```

Open http://127.0.0.1:8000/ in your browser. Edit JSON and press 保存.

## Optional: HTTPS (local TLS) with mkcert
If you want encrypted local connections (recommended when testing authentication
or running the UI on a machine with multiple users), use `mkcert` to create a
locally-trusted certificate and run the Flask server with TLS.

1. Install mkcert (Windows):

```powershell
# using chocolatey
choco install mkcert
# or using scoop
scoop install mkcert
```

2. Install the local CA (one-time):

```powershell
mkcert -install
```

3. Create a certificate for localhost and 127.0.0.1 (run in the repo/tools folder):

```powershell
mkcert 127.0.0.1 localhost
# this produces files like: 127.0.0.1+2.pem and 127.0.0.1+2-key.pem
```

4. Export env vars and run the server with TLS enabled:

```powershell
$env:WW_ENABLE_HTTPS = '1'
$env:WW_SSL_CERT = "$(Resolve-Path tools\127.0.0.1+2.pem)"
$env:WW_SSL_KEY = "$(Resolve-Path tools\127.0.0.1+2-key.pem)"
python tools\config_server.py
```

5. Open https://127.0.0.1:8000/ (mkcert makes the cert trusted by your system so
	 the browser should not show a warning).

- If `WW_ENABLE_HTTPS` is set but the cert/key files are not found, the server
	will fall back to plain HTTP and print a message.
- This setup is for local development only. For production use a proper WSGI
	server (gunicorn) behind a reverse proxy (nginx/Caddy) with certificates from
	a trusted CA.

## Quick-recovery / Troubleshooting & Daily-usage notes
This section documents the exact steps to bring the config server up after a
reboot, or to recover from common errors (missing packages, server not starting,
port conflicts). All commands assume Windows PowerShell (you can adapt to cmd
if needed).

1) Start from the repository root

```powershell
# open PowerShell and cd to repo root
cd C:\Users\<you>\Desktop\hobbies\recorder\werewolf
```

2) (First time) create a virtual environment and install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r tools\requirements.txt
```

3) Start the server (foreground, useful to see errors)

```powershell
# Activate venv if not already
.\.venv\Scripts\Activate.ps1
# Optional: set token for this session only (recommended)
$env:WW_CONFIG_TOKEN = 'paste-your-token-here'
# Start in foreground so you can see logs in the terminal
python tools\config_server.py
```

If you prefer to run the server in the background (and capture logs):

```powershell
.\.venv\Scripts\Activate.ps1
Start-Job -ScriptBlock { & "$(Resolve-Path .\.venv\Scripts\python.exe)" "$(Resolve-Path tools\config_server.py)" > "$(Resolve-Path tools\server.log)" 2>&1 }
```

To follow the background log file in real time:

```powershell
Get-Content .\tools\server.log -Wait
```

4) Persistent token (optional)

If you don't want to paste the token every session, save it as a user
environment variable (persists across reboots):

```powershell
setx WW_CONFIG_TOKEN "your-persistent-token-here"
# After running setx, open a NEW PowerShell terminal for the variable to be visible
```

5) If you see `ModuleNotFoundError: No module named 'flask'`

- Make sure you activated the virtualenv you used to install packages:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r tools\requirements.txt
```

Or run the server with the venv's python explicitly:

```powershell
& ".\.venv\Scripts\python.exe" tools\config_server.py
```

6) If port 8000 is already in use

```powershell
# Show which process is using port 8000
Get-NetTCPConnection -LocalPort 8000 | Format-Table -AutoSize
# Kill the process by PID (use carefuly; replace <pid>)
taskkill /PID <pid> /F
```

7) Stop/inspect background job

```powershell
# list jobs and find the Id
Get-Job
# stop and remove (replace <Id>)
Stop-Job -Id <Id>
Remove-Job -Id <Id>
```

8) Checking the server is responding

```powershell
Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing
# or if HTTPS enabled
Invoke-WebRequest https://127.0.0.1:8000/ -UseBasicParsing
```

9) If you enabled HTTPS with mkcert

- Ensure you set the env vars before starting the server (in the same shell):

```powershell
$env:WW_ENABLE_HTTPS = '1'
$env:WW_SSL_CERT = (Resolve-Path tools\127.0.0.1+2.pem)
$env:WW_SSL_KEY = (Resolve-Path tools\127.0.0.1+2-key.pem)
python tools\config_server.py
```

10) Where the JSON is written

The server writes to `roles/role_distribution.json` under the repository root.
If the bot is running it may need to be restarted or reloaded to pick up changes.

11) Restarting the bot

- If the bot process is running, simplest is to stop and start it again. If
	your bot has a command or admin hook to reload role definitions, use that
	(project-specific). Otherwise restart the bot process.

12) Helpful log locations

- `tools/server.log` (if you started the server as a background job with redirection)
- Terminal output if you run in foreground

13) Security reminders

- Keep `WW_CONFIG_TOKEN` secret. Do not commit it to the repository.
- By default this server binds to 127.0.0.1 only. Do not change to 0.0.0.0
	unless you understand the security implications.

If you want, I can add a small script `tools/start_server.ps1` that performs the
common steps (activate venv, set token from a secure store, and start the server)
— would you like that? 

Security: optional token via environment variable `WW_CONFIG_TOKEN`.
Set it before running the server if you want a simple auth:

```powershell
$env:WW_CONFIG_TOKEN = 'your-secret'
python tools\config_server.py
```

Bot integration:
- This writes to `roles/role_distribution.json` under the repo root. The bot's existing loading logic will find and use it if in the search path.
- If your bot is already running, trigger a settings reload using the admin command `/ww_reload` (the codebase also exposes a `_reload_roles` helper). Otherwise restart the bot.

Notes:
- Bind is to 127.0.0.1 by default to avoid exposing the UI publicly.
- Do not run this on a public IP without HTTPS and proper auth.

Real-time read-only viewer (WebSocket)
-------------------------------------
If you want a simple read-only web UI that updates in real-time when the role JSON changes,
run the WebSocket-enabled server included here:

1) Install dependencies (in the repo root):

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r tools\requirements.txt
```

2) Set a username/password for the UI (required):

```powershell
$env:WW_UI_USER = 'youruser'
$env:WW_UI_PASS = 'yourpassword'
```

3) Start the real-time server (binds 0.0.0.0:8000):

```powershell
python tools\realtime_server.py
```

4) Open the browser to http://<server-ip>:8000/ and enter the username/password when prompted.

Notes:
- This viewer is read-only. To change settings, edit the JSON file under `roles/` and then either restart the bot or use the bot admin command `/ww_reload` to apply changes.
- The server reads `roles/roles.json` if present, otherwise falls back to `roles/role_distribution.json`.
- The UI requires the environment variables `WW_UI_USER` and `WW_UI_PASS`; no credentials are committed to the repository.
