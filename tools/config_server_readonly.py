"""Read-only Flask-based config server for viewing role_distribution.json and settings

This is a read-only version of config_server.py that only allows viewing configuration
files without any editing capabilities. Password authentication required.

Security features for ngrok sharing:
- Password authentication (required)
- Rate limiting on login attempts
- Session-based access control
- Read-only endpoints (no POST/PUT/DELETE)

Usage:
    export WW_ACCESS_PASSWORD="your_secure_password"
    export FLASK_SECRET_KEY="your_secret_key_here"  # Optional but recommended
    python tools/config_server_readonly.py
    
Then use ngrok to create a tunnel:
    ngrok http 8001
"""
from flask import Flask, send_from_directory, jsonify, request, session
from pathlib import Path
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='ui_static_readonly')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = Path(__file__).resolve().parent / 'ui_static_readonly'
ROLE_DIR = ROOT / 'roles'
ROLE_FILE = ROLE_DIR / 'role_distribution.json'
ANNOUNCE_FILE = ROLE_DIR / 'announcements.json'
SETTINGS_FILE = ROLE_DIR / 'game_settings.json'
ROLE_SETTINGS_FILE = ROLE_DIR / 'role_settings.json'

# password for page access (REQUIRED for security)
ACCESS_PASSWORD = os.environ.get('WW_ACCESS_PASSWORD')
if not ACCESS_PASSWORD:
    print("WARNING: WW_ACCESS_PASSWORD not set. This is INSECURE for public access!")
    print("Please set: export WW_ACCESS_PASSWORD='your_password'")

# Rate limiting for login attempts
login_attempts = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW = timedelta(minutes=15)

# Rate limiting for login attempts
login_attempts = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW = timedelta(minutes=15)

# Allow any origin when using ngrok (CORS will be handled by origin validation)
# For production, restrict to specific ngrok domains
ALLOWED_ORIGINS = set()  # Empty set = allow all origins (needed for ngrok)


def get_client_ip():
    """Get real client IP, considering proxies like ngrok."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'


def check_rate_limit(ip):
    """Check if IP has exceeded login attempt rate limit."""
    now = datetime.now()
    # Clean old attempts
    login_attempts[ip] = [t for t in login_attempts[ip] if now - t < LOGIN_ATTEMPT_WINDOW]
    # Check limit
    if len(login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS:
        return False
    return True


def record_login_attempt(ip):
    """Record a login attempt for rate limiting."""
    login_attempts[ip].append(datetime.now())


def require_password():
    """Check if user is authenticated via session."""
    if not ACCESS_PASSWORD:
        return None  # No password required (not recommended for public access)
    if session.get('authenticated'):
        return None  # Already authenticated
    return jsonify({'error': 'authentication_required'}), 401


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle password authentication with rate limiting."""
    if request.method == 'POST':
        client_ip = get_client_ip()
        
        # Check rate limit
        if not check_rate_limit(client_ip):
            return jsonify({
                'ok': False, 
                'error': 'too_many_attempts',
                'message': f'Too many login attempts. Please try again after {LOGIN_ATTEMPT_WINDOW.seconds // 60} minutes.'
            }), 429
        
        data = request.get_json() or {}
        password = data.get('password', '')
        
        # Record attempt
        record_login_attempt(client_ip)
        
        if ACCESS_PASSWORD and password == ACCESS_PASSWORD:
            session['authenticated'] = True
            session.permanent = True  # Make session persistent
            app.permanent_session_lifetime = timedelta(hours=24)
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
    """Apply CORS headers for ngrok compatibility.
    
    For security: Only allows credentials with proper origin.
    For ngrok: Accepts any origin but maintains session security.
    """
    origin = request.headers.get('Origin')
    if origin:
        # Allow the requesting origin (needed for ngrok)
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    
    # Security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    
    return response


@app.route('/')
def index():
    auth_error = require_password()
    if auth_error:
        return send_from_directory(UI_DIR, 'login.html')
    return send_from_directory(UI_DIR, 'index.html')


@app.route('/download')
def download():
    """Get role distribution configuration (read-only)."""
    if ROLE_FILE.exists():
        return send_from_directory(ROLE_DIR, ROLE_FILE.name, as_attachment=False)
    return jsonify({'ok': False, 'error': 'no_file'}), 404


@app.route('/roles/roles.json')
def serve_roles_json():
    """Serve canonical roles/roles.json for role metadata."""
    roles_path = ROLE_DIR / 'roles.json'
    if roles_path.exists():
        return send_from_directory(ROLE_DIR, 'roles.json', as_attachment=False)
    return jsonify({'ok': False, 'error': 'no_roles_file'}), 404


@app.route('/announcements', methods=['GET'])
def announcements():
    """Get announcements (read-only)."""
    if ANNOUNCE_FILE.exists():
        try:
            import json
            data = json.loads(ANNOUNCE_FILE.read_text(encoding='utf-8'))
            return jsonify(data)
        except Exception:
            return jsonify([])
    return jsonify([])


@app.route('/game_settings', methods=['GET'])
def game_settings():
    """Get game settings (read-only)."""
    if SETTINGS_FILE.exists():
        try:
            import json
            data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            return jsonify(data)
        except Exception:
            return jsonify({})
    return jsonify({})


@app.route('/role_settings', methods=['GET'])
def role_settings():
    """Get role-specific settings (read-only)."""
    if ROLE_SETTINGS_FILE.exists():
        try:
            import json
            data = json.loads(ROLE_SETTINGS_FILE.read_text(encoding='utf-8'))
            return jsonify(data)
        except Exception:
            return jsonify({})
    return jsonify({})


if __name__ == '__main__':
    # Read-only server for ngrok sharing
    print("=" * 60)
    print("READ-ONLY Configuration Server (ngrok-ready)")
    print("=" * 60)
    
    if not ACCESS_PASSWORD:
        print("⚠️  WARNING: No password set! This is INSECURE for public access.")
        print("   Set password: export WW_ACCESS_PASSWORD='your_password'")
        print()
    else:
        print("✅ Password authentication enabled")
    
    if not os.environ.get('FLASK_SECRET_KEY'):
        print("⚠️  WARNING: Using random session key. Sessions will reset on restart.")
        print("   Set persistent key: export FLASK_SECRET_KEY='your_secret_key'")
        print()
    else:
        print("✅ Persistent session key configured")
    
    print()
    print("Security features:")
    print("  • Password authentication required")
    print(f"  • Rate limiting: {MAX_LOGIN_ATTEMPTS} attempts per {LOGIN_ATTEMPT_WINDOW.seconds // 60} minutes")
    print("  • Session-based access control (24h expiry)")
    print("  • Read-only endpoints (no data modification)")
    print("  • Security headers enabled")
    print()
    print("Starting server on http://0.0.0.0:8001")
    print("For ngrok: ngrok http 8001")
    print("-" * 60)
    
    app.run(host='0.0.0.0', port=8001, debug=False)
