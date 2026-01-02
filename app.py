from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import json
import os
import time
import uuid
import requests
from datetime import datetime
import logging
from functools import wraps

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24).hex())
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACCOUNTS_FILE = os.getenv('ACCOUNTS_FILE', 'accounts.json')
REFRESH_INTERVAL = int(os.getenv('REFRESH_INTERVAL', 3600))  # 1 hour default
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', None)  # Password protection

def load_accounts():
    """Load accounts from JSON file"""
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"version": "1.3.1", "exportedAt": int(time.time() * 1000), "accounts": [], "groups": [], "tags": []}

def save_accounts(data):
    """Save accounts to JSON file"""
    data['exportedAt'] = int(time.time() * 1000)
    with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def generate_machine_id():
    """Generate a unique machine ID"""
    return str(uuid.uuid4()).replace('-', '')[:32]

def refresh_token(account):
    """Refresh access token for an account"""
    try:
        credentials = account.get('credentials', {})
        refresh_token = credentials.get('refreshToken')
        
        if not refresh_token:
            logger.warning(f"No refresh token for account {account.get('email')}")
            return False
        
        # AWS Builder ID token refresh endpoint
        url = "https://oidc.us-east-1.amazonaws.com/token"
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': credentials.get('clientId', '')
        }
        
        response = requests.post(url, data=data, timeout=10)
        
        if response.status_code == 200:
            token_data = response.json()
            credentials['accessToken'] = token_data.get('access_token', credentials['accessToken'])
            credentials['expiresAt'] = int(time.time() * 1000) + (token_data.get('expires_in', 3600) * 1000)
            account['lastCheckedAt'] = int(time.time() * 1000)
            logger.info(f"Token refreshed for {account.get('email')}")
            return True
        else:
            logger.error(f"Failed to refresh token for {account.get('email')}: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error refreshing token for {account.get('email')}: {str(e)}")
        return False

def auto_refresh_tokens():
    """Automatically refresh tokens for all accounts"""
    logger.info("Starting automatic token refresh...")
    data = load_accounts()
    
    for account in data.get('accounts', []):
        if account.get('status') == 'active':
            refresh_token(account)
    
    save_accounts(data)
    logger.info("Token refresh completed")

def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # If no password is set, allow access
        if not ADMIN_PASSWORD:
            return f(*args, **kwargs)
        
        # Check if user is authenticated
        if not session.get('authenticated'):
            return jsonify({"success": False, "error": "Authentication required"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """Serve the main page"""
    # Check if authentication is required
    if ADMIN_PASSWORD and not session.get('authenticated'):
        return send_from_directory('static', 'login.html')
    return send_from_directory('static', 'index.html')

@app.route('/login')
def login_page():
    """Serve the login page"""
    return send_from_directory('static', 'login.html')

@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    """Check if authentication is required and if user is authenticated"""
    return jsonify({
        "authRequired": ADMIN_PASSWORD is not None,
        "authenticated": session.get('authenticated', False)
    })

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login with password"""
    if not ADMIN_PASSWORD:
        return jsonify({"success": True, "message": "No authentication required"})
    
    data = request.json
    password = data.get('password', '')
    
    if password == ADMIN_PASSWORD:
        session['authenticated'] = True
        session.permanent = True
        return jsonify({"success": True, "message": "Login successful"})
    else:
        return jsonify({"success": False, "error": "Invalid password"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Logout"""
    session.pop('authenticated', None)
    return jsonify({"success": True, "message": "Logged out"})

@app.route('/api/accounts', methods=['GET'])
@require_auth
def get_accounts():
    """Get all accounts"""
    data = load_accounts()
    return jsonify(data)

@app.route('/api/accounts/import', methods=['POST'])
@require_auth
def import_accounts():
    """Import accounts from JSON"""
    try:
        imported_data = request.json
        current_data = load_accounts()
        
        # Process imported accounts
        for account in imported_data.get('accounts', []):
            # Generate machine ID if not exists
            if 'machineId' not in account:
                account['machineId'] = generate_machine_id()
            
            # Check if account already exists
            existing = next((a for a in current_data['accounts'] if a.get('email') == account.get('email') and a.get('idp') == account.get('idp')), None)
            
            if existing:
                # Update existing account
                existing.update(account)
            else:
                # Add new account
                current_data['accounts'].append(account)
        
        save_accounts(current_data)
        return jsonify({"success": True, "message": f"Imported {len(imported_data.get('accounts', []))} accounts"})
    
    except Exception as e:
        logger.error(f"Import error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>', methods=['PUT'])
@require_auth
def update_account(account_id):
    """Update an account"""
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        
        updates = request.json
        account.update(updates)
        save_accounts(data)
        
        return jsonify({"success": True, "account": account})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>', methods=['DELETE'])
@require_auth
def delete_account(account_id):
    """Delete an account"""
    try:
        data = load_accounts()
        data['accounts'] = [a for a in data['accounts'] if a.get('id') != account_id]
        save_accounts(data)
        
        return jsonify({"success": True})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>/refresh', methods=['POST'])
@require_auth
def refresh_account_token(account_id):
    """Manually refresh token for an account"""
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        
        success = refresh_token(account)
        save_accounts(data)
        
        return jsonify({"success": success, "account": account})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>/machine-id', methods=['POST'])
@require_auth
def regenerate_machine_id(account_id):
    """Regenerate machine ID for an account"""
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        
        account['machineId'] = generate_machine_id()
        save_accounts(data)
        
        return jsonify({"success": True, "machineId": account['machineId']})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/export', methods=['GET'])
@require_auth
def export_accounts():
    """Export accounts as JSON"""
    data = load_accounts()
    return jsonify(data)

@app.route('/api/stats', methods=['GET'])
@require_auth
def get_stats():
    """Get account statistics"""
    data = load_accounts()
    accounts = data.get('accounts', [])
    
    stats = {
        "total": len(accounts),
        "active": len([a for a in accounts if a.get('status') == 'active']),
        "totalCredits": sum(a.get('usage', {}).get('limit', 0) for a in accounts),
        "usedCredits": sum(a.get('usage', {}).get('current', 0) for a in accounts),
        "byProvider": {}
    }
    
    for account in accounts:
        provider = account.get('idp', 'Unknown')
        stats['byProvider'][provider] = stats['byProvider'].get(provider, 0) + 1
    
    return jsonify(stats)

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=auto_refresh_tokens, trigger="interval", seconds=REFRESH_INTERVAL)
scheduler.start()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
