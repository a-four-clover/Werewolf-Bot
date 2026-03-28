#!/usr/bin/env python3
"""Flask app that serves a read-only JSON view of role settings with WebSocket updates.

Features:
- Basic auth (username/password provided via env: WW_UI_USER, WW_UI_PASS)
- Serves an HTML page that displays role JSON in a table and translates role names to Japanese
- Uses Flask-SocketIO to push updates to connected clients when the JSON file changes
- Serves static CSS/JS from tools/static

Run:
    set WW_UI_USER=admin
    set WW_UI_PASS=secret
    python tools\realtime_server.py

This binds to 0.0.0.0:8000 by default.
"""
import os
import json
import time
from pathlib import Path
from threading import Lock

# Optional: use eventlet for async workers (recommended)
# IMPORTANT: eventlet.monkey_patch() must be called before importing Flask/werkzeug
try:
    import eventlet
    eventlet.monkey_patch()
    async_mode = 'eventlet'
except Exception:
    async_mode = None

from flask import Flask, render_template, send_from_directory, request, Response
from flask_socketio import SocketIO

app = Flask(__name__, template_folder=str(Path(__file__).parent / 'templates'), static_folder=str(Path(__file__).parent / 'static'))
socketio = SocketIO(app, async_mode=async_mode)

# Basic auth credentials must be provided via environment variables to avoid embedding secrets
UI_USER = os.environ.get('WW_UI_USER')
UI_PASS = os.environ.get('WW_UI_PASS')

# Path to JSON file to display (prefer roles/roles.json then roles/role_distribution.json)
try:
    repo_root = Path(__file__).resolve().parents[1]
except Exception:
    repo_root = Path.cwd()

ROLE_FILE_CANDIDATES = [repo_root / 'roles' / 'roles.json', repo_root / 'roles' / 'role_distribution.json']

def find_role_file():
    for p in ROLE_FILE_CANDIDATES:
        if p.exists():
            return p
    # fallback: first candidate path
    return ROLE_FILE_CANDIDATES[0]


def load_json_file(path: Path):
    try:
        with path.open('r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return None


ROLE_NAME_JP = {
    'werewolf': '人狼',
    'seer': '占い師',
    'villager': '村人',
    'madman': '狂人',
    'medium': '霊媒師',
    'nice_guesser': '善良な予言者',
    'evil_guesser': '邪悪な予言者',
}


def translate_name(en_name: str) -> str:
    return ROLE_NAME_JP.get(en_name, en_name)


def check_auth():
    # If credentials are not configured, deny access for safety
    if not UI_USER or not UI_PASS:
        return False
    auth = request.authorization
    if not auth:
        return False
    return auth.username == UI_USER and auth.password == UI_PASS


def authenticate():
    return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Login"'})


def require_auth(fn):
    def wrapper(*args, **kwargs):
        if not check_auth():
            return authenticate()
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


@app.route('/')
@require_auth
def index():
    role_file = find_role_file()
    data = load_json_file(role_file)
    # Normalize into a list of roles for display
    roles_list = []
    if isinstance(data, dict):
        # If it's a mapping role_id -> {name,faction}
        for rid, info in data.items():
            if isinstance(info, dict):
                name = info.get('name', rid)
                faction = info.get('faction', '')
            else:
                name = str(info)
                faction = ''
            roles_list.append({'id': rid, 'name': name, 'name_ja': translate_name(rid), 'faction': faction})
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                rid = item.get('id') or item.get('name') or str(item)
                name = item.get('name', rid)
                faction = item.get('faction', '')
                roles_list.append({'id': rid, 'name': name, 'name_ja': translate_name(rid), 'faction': faction})
    else:
        # unknown format, show raw
        roles_list = []
    return render_template('index.html', roles=roles_list)


@app.route('/static/<path:filename>')
def static_files(filename):
    # serve bundled static files
    static_dir = Path(__file__).parent / 'static'
    return send_from_directory(str(static_dir), filename)


connected_lock = Lock()
last_mtime = None


def monitor_file_and_emit(interval: float = 1.0):
    global last_mtime
    role_file = find_role_file()
    try:
        last_mtime = role_file.stat().st_mtime
    except Exception:
        last_mtime = None
    while True:
        try:
            time.sleep(interval)
            try:
                m = role_file.stat().st_mtime
            except Exception:
                m = None
            if m != last_mtime:
                last_mtime = m
                new_data = load_json_file(role_file)
                # sanitize: do not include any env or secrets
                socketio.emit('update', {'data': new_data}, broadcast=True)
        except Exception:
            # continue polling even on errors
            time.sleep(1)


@socketio.on('connect')
def on_connect():
    # We don't expose sensitive info on connect; send current JSON snapshot
    role_file = find_role_file()
    data = load_json_file(role_file)
    socketio.emit('update', {'data': data})


def main():
    # Start background monitor
    socketio.start_background_task(monitor_file_and_emit)
    # bind to all interfaces as requested
    socketio.run(app, host='0.0.0.0', port=8000)


if __name__ == '__main__':
    main()
