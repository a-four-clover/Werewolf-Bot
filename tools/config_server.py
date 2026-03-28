"""Simple Flask-based config server for editing and saving role_distribution.json

Place this inside your repo (tools/config_server.py). It serves a minimal HTML UI
and provides /save endpoint that writes to ../roles/role_distribution.json.

Security: optional token via env var WW_CONFIG_TOKEN. If set, client must send
X-API-Token header with that token.
"""
from flask import Flask, request, send_from_directory, jsonify, session, redirect, url_for
from pathlib import Path
import json
import os
import secrets

app = Flask(__name__, static_folder='ui_static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = Path(__file__).resolve().parent / 'ui_static'
ROLE_DIR = ROOT / 'roles'
ROLE_DIR.mkdir(parents=True, exist_ok=True)
ROLE_FILE = ROLE_DIR / 'role_distribution.json'
ANNOUNCE_FILE = ROLE_DIR / 'announcements.json'
SETTINGS_FILE = ROLE_DIR / 'game_settings.json'
ROLE_SETTINGS_FILE = ROLE_DIR / 'role_settings.json'

# optional token for simple auth
CONFIG_TOKEN = os.environ.get('WW_CONFIG_TOKEN')
# password for page access
ACCESS_PASSWORD = os.environ.get('WW_ACCESS_PASSWORD')

# Restrict allowed origins that can send requests to this server. Keep localloop
# origins only. If you need to allow additional origins, add them here.
ALLOWED_ORIGINS = {
    'http://127.0.0.1:8000',
    'http://localhost:8000',
}

# Optionally allow https origins if TLS is enabled; these entries will be
# added at runtime if ssl certs are provided or WW_ENABLE_HTTPS is set.


def require_password():
    """Check if user is authenticated via session."""
    if not ACCESS_PASSWORD:
        return None  # No password required
    if session.get('authenticated'):
        return None  # Already authenticated
    return jsonify({'error': 'authentication_required'}), 401


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle password authentication."""
    if request.method == 'POST':
        data = request.get_json() or {}
        password = data.get('password', '')
        if ACCESS_PASSWORD and password == ACCESS_PASSWORD:
            session['authenticated'] = True
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'invalid_password'}), 401
    # GET request - return login page
    return send_from_directory(UI_DIR, 'login.html')


@app.route('/check_auth')
def check_auth():
    """Check if user is authenticated."""
    if not ACCESS_PASSWORD:
        return jsonify({'required': False, 'authenticated': True})
    return jsonify({'required': True, 'authenticated': session.get('authenticated', False)})


@app.after_request
def apply_cors(response):
    """Apply strict CORS headers only for allowed origins.

    This prevents other origins from making browser-based requests to the
    /save endpoint. Since the UI is served by this server, allowed origins
    are localhost variants only.
    """
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

@app.route('/')
def index():
    auth_error = require_password()
    if auth_error:
        return send_from_directory(UI_DIR, 'login.html')
    return send_from_directory(UI_DIR, 'index.html')

@app.route('/save', methods=['POST', 'OPTIONS'])
def save():
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        # reply with allowed headers for the browser preflight
        resp = jsonify({'ok': True})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Token'
            resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            resp.headers['Vary'] = 'Origin'
        return resp

    # token check (if configured)
    if CONFIG_TOKEN:
        token = request.headers.get('X-API-Token')
        # Do NOT log token value anywhere. Only compare and reject if mismatched.
        if token != CONFIG_TOKEN:
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    try:
        # Force JSON parsing but do not echo client headers or token values in
        # error messages or logs to avoid accidental leakage.
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'invalid json', 'detail': str(e)}), 400
    # basic validation
    if not isinstance(data, dict):
        return jsonify({'ok': False, 'error': 'top-level must be object'}), 400
    for k, v in data.items():
        if not isinstance(k, str) or not k.isdigit():
            return jsonify({'ok': False, 'error': 'keys must be integer strings', 'key': k}), 400
        if not isinstance(v, (list, dict)):
            return jsonify({'ok': False, 'error': 'values must be list or dict', 'key': k}), 400
    try:
        ROLE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'write_failed', 'detail': str(e)}), 500

@app.route('/download')
def download():
    if ROLE_FILE.exists():
        return send_from_directory(ROLE_DIR, ROLE_FILE.name, as_attachment=False)
    return jsonify({'ok': False, 'error': 'no_file'}), 404


@app.route('/roles/roles.json')
def serve_roles_json():
    """Serve a canonical roles/roles.json if present in the repo. This makes the
    UI and other tools able to fetch role metadata from a consistent location.
    """
    roles_path = ROLE_DIR / 'roles.json'
    if roles_path.exists():
        return send_from_directory(ROLE_DIR, 'roles.json', as_attachment=False)
    return jsonify({'ok': False, 'error': 'no_roles_file'}), 404


@app.route('/announcements', methods=['GET', 'POST', 'OPTIONS'])
def announcements():
    # CORS preflight for POST
    if request.method == 'OPTIONS':
        resp = jsonify({'ok': True})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Token'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            resp.headers['Vary'] = 'Origin'
        return resp

    if request.method == 'GET':
        if ANNOUNCE_FILE.exists():
            try:
                data = json.loads(ANNOUNCE_FILE.read_text(encoding='utf-8'))
                return jsonify(data)
            except Exception:
                return jsonify([])
        return jsonify([])

    # POST - replace announcements file; require token if configured
    if CONFIG_TOKEN:
        token = request.headers.get('X-API-Token')
        if token != CONFIG_TOKEN:
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'invalid json', 'detail': str(e)}), 400
    # Accept either an array of announcements or an object containing list
    if isinstance(payload, dict) and 'announcements' in payload and isinstance(payload['announcements'], list):
        out = payload['announcements']
    elif isinstance(payload, list):
        out = payload
    else:
        return jsonify({'ok': False, 'error': 'payload must be array or {announcements: []}'}), 400
    try:
        ANNOUNCE_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
        return jsonify({'ok': True})

    except Exception as e:
        return jsonify({'ok': False, 'error': 'write_failed', 'detail': str(e)}), 500


@app.route('/game_settings', methods=['GET', 'POST', 'OPTIONS'])
def game_settings():
    # CORS preflight
    if request.method == 'OPTIONS':
        resp = jsonify({'ok': True})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Token'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            resp.headers['Vary'] = 'Origin'
        return resp

    if request.method == 'GET':
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
                return jsonify(data)
            except Exception:
                return jsonify({})
        return jsonify({})

    # POST - require token if configured
    if CONFIG_TOKEN:
        token = request.headers.get('X-API-Token')
        if token != CONFIG_TOKEN:
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'invalid json', 'detail': str(e)}), 400
    # expect a top-level object (settings map)
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'error': 'payload must be object'}), 400
    try:
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'write_failed', 'detail': str(e)}), 500


@app.route('/role_settings', methods=['GET', 'POST', 'OPTIONS'])
def role_settings():
    # CORS preflight
    if request.method == 'OPTIONS':
        resp = jsonify({'ok': True})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Token'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            resp.headers['Vary'] = 'Origin'
        return resp

    if request.method == 'GET':
        if ROLE_SETTINGS_FILE.exists():
            try:
                data = json.loads(ROLE_SETTINGS_FILE.read_text(encoding='utf-8'))
                return jsonify(data)
            except Exception:
                return jsonify({})
        return jsonify({})

    # POST - require token if configured
    if CONFIG_TOKEN:
        token = request.headers.get('X-API-Token')
        if token != CONFIG_TOKEN:
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'invalid json', 'detail': str(e)}), 400
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'error': 'payload must be object'}), 400
    try:
        ROLE_SETTINGS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'write_failed', 'detail': str(e)}), 500


if __name__ == '__main__':
    # development server bound to localhost only
    # Support optional HTTPS via environment variables:
    # - WW_ENABLE_HTTPS=1 to enable if cert/key are present
    # - WW_SSL_CERT and WW_SSL_KEY to point to cert and key files
    enable_https = os.environ.get('WW_ENABLE_HTTPS')
    ssl_cert = os.environ.get('WW_SSL_CERT')
    ssl_key = os.environ.get('WW_SSL_KEY')

    ssl_context = None
    if enable_https and (ssl_cert and ssl_key):
        cert_path = Path(ssl_cert)
        key_path = Path(ssl_key)
        if cert_path.exists() and key_path.exists():
            ssl_context = (str(cert_path), str(key_path))
            # add https origins to allowed list
            ALLOWED_ORIGINS.add('https://127.0.0.1:8000')
            ALLOWED_ORIGINS.add('https://localhost:8000')
        else:
            print('WW_ENABLE_HTTPS set but certificate files not found; falling back to HTTP')

    if ssl_context:
        app.run(host='127.0.0.1', port=8000, ssl_context=ssl_context)
    else:
        app.run(host='127.0.0.1', port=8000)
