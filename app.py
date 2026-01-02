from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import json
import os
import time
import uuid
import hashlib
import requests
from datetime import datetime, timedelta
import logging
from functools import wraps

app = Flask(__name__, static_folder='static')

# Generate a stable secret key
_secret_base = os.getenv('SECRET_KEY') or os.getenv('ADMIN_PASSWORD') or 'kiro-account-manager-default-key'
app.secret_key = hashlib.sha256(_secret_base.encode()).hexdigest()

# Session configuration
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

CORS(app, supports_credentials=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
ACCOUNTS_FILE = os.getenv('ACCOUNTS_FILE', 'accounts.json')
SETTINGS_FILE = os.getenv('SETTINGS_FILE', 'settings.json')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', None)

# Default settings
DEFAULT_SETTINGS = {
    "autoRefresh": {
        "enabled": True,
        "interval": 3600,  # seconds (1 hour default)
        "refreshBeforeExpiry": 300  # refresh 5 minutes before expiry
    },
    "autoSwitch": {
        "enabled": False,
        "checkInterval": 1800,  # check every 30 minutes
        "switchThreshold": 90,  # switch when usage > 90%
        "currentAccountId": None
    },
    "notifications": {
        "onRefreshFail": True,
        "onAutoSwitch": True
    }
}

# Global scheduler
scheduler = BackgroundScheduler()
scheduler_jobs = {}

# ==================== Helper Functions ====================

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

def load_settings():
    """Load settings from JSON file"""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            # Merge with defaults
            settings = DEFAULT_SETTINGS.copy()
            for key in settings:
                if key in saved:
                    if isinstance(settings[key], dict):
                        settings[key].update(saved[key])
                    else:
                        settings[key] = saved[key]
            return settings
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    """Save settings to JSON file"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

def generate_machine_id():
    """Generate a unique machine ID"""
    return str(uuid.uuid4()).replace('-', '')[:32]

def refresh_token(account):
    """Refresh access token for an account"""
    try:
        credentials = account.get('credentials', {})
        refresh_token_value = credentials.get('refreshToken')
        client_id = credentials.get('clientId')
        region = credentials.get('region', 'us-east-1')
        
        if not refresh_token_value:
            return False, "No refresh token available"
        
        url = f"https://oidc.{region}.amazonaws.com/token"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'grant_type': 'refresh_token', 'refresh_token': refresh_token_value}
        
        if client_id:
            data['client_id'] = client_id
        
        response = requests.post(url, data=data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            token_data = response.json()
            credentials['accessToken'] = token_data.get('access_token', credentials.get('accessToken'))
            if token_data.get('refresh_token'):
                credentials['refreshToken'] = token_data.get('refresh_token')
            credentials['expiresAt'] = int(time.time() * 1000) + (token_data.get('expires_in', 3600) * 1000)
            account['lastCheckedAt'] = int(time.time() * 1000)
            
            # Try to update usage info
            update_account_usage(account)
            
            logger.info(f"Token refreshed for {account.get('email')}")
            return True, "Token refreshed successfully"
        else:
            error_msg = f"HTTP {response.status_code}"
            try:
                error_data = response.json()
                error_msg = error_data.get('error_description', error_data.get('error', error_msg))
            except:
                pass
            logger.error(f"Failed to refresh token for {account.get('email')}: {error_msg}")
            return False, error_msg
            
    except requests.exceptions.Timeout:
        return False, "Request timeout"
    except Exception as e:
        logger.error(f"Error refreshing token for {account.get('email')}: {str(e)}")
        return False, str(e)

def update_account_usage(account):
    """Update account usage information after token refresh"""
    # This would typically call an API to get updated usage
    # For now, just update the lastCheckedAt timestamp
    account['lastCheckedAt'] = int(time.time() * 1000)

def get_account_usage_percent(account):
    """Get account usage percentage"""
    usage = account.get('usage', {})
    limit = usage.get('limit', 0)
    current = usage.get('current', 0)
    if limit > 0:
        return (current / limit) * 100
    return 0

def find_best_account():
    """Find the account with lowest usage percentage"""
    data = load_accounts()
    accounts = [a for a in data.get('accounts', []) if a.get('status') == 'active']
    
    if not accounts:
        return None
    
    # Sort by usage percentage (ascending)
    accounts.sort(key=lambda a: get_account_usage_percent(a))
    return accounts[0]

# ==================== Scheduled Tasks ====================

def auto_refresh_tokens_task():
    """Automatically refresh tokens for all accounts"""
    logger.info("🔄 Starting automatic token refresh...")
    data = load_accounts()
    settings = load_settings()
    
    refresh_before = settings['autoRefresh'].get('refreshBeforeExpiry', 300) * 1000
    current_time = int(time.time() * 1000)
    
    refreshed = 0
    failed = 0
    
    for account in data.get('accounts', []):
        if account.get('status') != 'active':
            continue
            
        credentials = account.get('credentials', {})
        expires_at = credentials.get('expiresAt', 0)
        
        # Check if token is expired or will expire soon
        if expires_at - current_time < refresh_before:
            success, msg = refresh_token(account)
            if success:
                refreshed += 1
            else:
                failed += 1
                logger.warning(f"Auto refresh failed for {account.get('email')}: {msg}")
    
    save_accounts(data)
    logger.info(f"✅ Token refresh completed: {refreshed} refreshed, {failed} failed")

def auto_switch_account_task():
    """Check accounts and switch to one with lower usage if needed"""
    logger.info("🔀 Checking for auto account switch...")
    settings = load_settings()
    
    if not settings['autoSwitch']['enabled']:
        return
    
    threshold = settings['autoSwitch'].get('switchThreshold', 90)
    current_id = settings['autoSwitch'].get('currentAccountId')
    
    data = load_accounts()
    
    # Find current account
    current_account = None
    if current_id:
        current_account = next((a for a in data['accounts'] if a.get('id') == current_id), None)
    
    # Check if current account usage is above threshold
    if current_account:
        usage_percent = get_account_usage_percent(current_account)
        if usage_percent < threshold:
            logger.info(f"Current account usage ({usage_percent:.1f}%) is below threshold ({threshold}%)")
            return
    
    # Find best account
    best_account = find_best_account()
    if best_account and best_account.get('id') != current_id:
        best_usage = get_account_usage_percent(best_account)
        if best_usage < threshold:
            settings['autoSwitch']['currentAccountId'] = best_account.get('id')
            save_settings(settings)
            logger.info(f"🔀 Switched to account: {best_account.get('email')} (usage: {best_usage:.1f}%)")

def setup_scheduler():
    """Setup scheduler with current settings"""
    global scheduler_jobs
    
    settings = load_settings()
    
    # Remove existing jobs
    for job_id in list(scheduler_jobs.keys()):
        try:
            scheduler.remove_job(job_id)
        except:
            pass
    scheduler_jobs.clear()
    
    # Add auto refresh job
    if settings['autoRefresh']['enabled']:
        interval = settings['autoRefresh']['interval']
        job = scheduler.add_job(
            func=auto_refresh_tokens_task,
            trigger=IntervalTrigger(seconds=interval),
            id='auto_refresh',
            replace_existing=True
        )
        scheduler_jobs['auto_refresh'] = job
        logger.info(f"📅 Auto refresh scheduled every {interval} seconds")
    
    # Add auto switch job
    if settings['autoSwitch']['enabled']:
        interval = settings['autoSwitch']['checkInterval']
        job = scheduler.add_job(
            func=auto_switch_account_task,
            trigger=IntervalTrigger(seconds=interval),
            id='auto_switch',
            replace_existing=True
        )
        scheduler_jobs['auto_switch'] = job
        logger.info(f"📅 Auto switch check scheduled every {interval} seconds")

# ==================== Auth Decorator ====================

def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return f(*args, **kwargs)
        if not session.get('authenticated'):
            return jsonify({"success": False, "error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function

# ==================== Routes ====================

@app.route('/')
def index():
    if ADMIN_PASSWORD and not session.get('authenticated'):
        return send_from_directory('static', 'login.html')
    return send_from_directory('static', 'index.html')

@app.route('/login')
def login_page():
    return send_from_directory('static', 'login.html')

@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    return jsonify({
        "authRequired": ADMIN_PASSWORD is not None,
        "authenticated": session.get('authenticated', False)
    })

@app.route('/api/auth/login', methods=['POST'])
def login():
    if not ADMIN_PASSWORD:
        session['authenticated'] = True
        session.permanent = True
        return jsonify({"success": True})
    
    password = request.json.get('password', '')
    if password == ADMIN_PASSWORD:
        session.clear()
        session['authenticated'] = True
        session.permanent = True
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid password"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.pop('authenticated', None)
    return jsonify({"success": True})

# ==================== Settings Routes ====================

@app.route('/api/settings', methods=['GET'])
@require_auth
def get_settings():
    """Get current settings"""
    settings = load_settings()
    
    # Add scheduler status
    settings['scheduler'] = {
        'running': scheduler.running,
        'jobs': []
    }
    
    for job in scheduler.get_jobs():
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        settings['scheduler']['jobs'].append({
            'id': job.id,
            'nextRun': next_run
        })
    
    return jsonify({"success": True, "settings": settings})

@app.route('/api/settings', methods=['PUT'])
@require_auth
def update_settings():
    """Update settings"""
    try:
        new_settings = request.json
        current_settings = load_settings()
        
        # Update settings
        for key in new_settings:
            if key in current_settings:
                if isinstance(current_settings[key], dict) and isinstance(new_settings[key], dict):
                    current_settings[key].update(new_settings[key])
                else:
                    current_settings[key] = new_settings[key]
        
        save_settings(current_settings)
        
        # Restart scheduler with new settings
        setup_scheduler()
        
        return jsonify({"success": True, "settings": current_settings})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/settings/refresh-interval', methods=['PUT'])
@require_auth
def update_refresh_interval():
    """Update auto refresh interval"""
    try:
        interval = request.json.get('interval', 3600)
        enabled = request.json.get('enabled', True)
        
        settings = load_settings()
        settings['autoRefresh']['interval'] = int(interval)
        settings['autoRefresh']['enabled'] = bool(enabled)
        save_settings(settings)
        
        setup_scheduler()
        
        return jsonify({
            "success": True, 
            "interval": interval,
            "enabled": enabled,
            "message": f"Refresh interval set to {interval} seconds"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/settings/auto-switch', methods=['PUT'])
@require_auth
def update_auto_switch():
    """Update auto switch settings"""
    try:
        settings = load_settings()
        
        if 'enabled' in request.json:
            settings['autoSwitch']['enabled'] = bool(request.json['enabled'])
        if 'checkInterval' in request.json:
            settings['autoSwitch']['checkInterval'] = int(request.json['checkInterval'])
        if 'switchThreshold' in request.json:
            settings['autoSwitch']['switchThreshold'] = int(request.json['switchThreshold'])
        if 'currentAccountId' in request.json:
            settings['autoSwitch']['currentAccountId'] = request.json['currentAccountId']
        
        save_settings(settings)
        setup_scheduler()
        
        return jsonify({"success": True, "autoSwitch": settings['autoSwitch']})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/scheduler/trigger/<job_id>', methods=['POST'])
@require_auth
def trigger_job(job_id):
    """Manually trigger a scheduled job"""
    try:
        if job_id == 'auto_refresh':
            auto_refresh_tokens_task()
            return jsonify({"success": True, "message": "Token refresh triggered"})
        elif job_id == 'auto_switch':
            auto_switch_account_task()
            return jsonify({"success": True, "message": "Auto switch check triggered"})
        else:
            return jsonify({"success": False, "error": "Unknown job"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

# ==================== Account Routes ====================

@app.route('/api/accounts', methods=['GET'])
@require_auth
def get_accounts():
    data = load_accounts()
    settings = load_settings()
    
    # Add current account indicator
    current_id = settings['autoSwitch'].get('currentAccountId')
    for account in data.get('accounts', []):
        account['isCurrent'] = account.get('id') == current_id
    
    return jsonify(data)

@app.route('/api/accounts/import', methods=['POST'])
@require_auth
def import_accounts():
    try:
        imported_data = request.json
        current_data = load_accounts()
        
        imported_count = 0
        for account in imported_data.get('accounts', []):
            if 'machineId' not in account:
                account['machineId'] = generate_machine_id()
            
            existing = next((a for a in current_data['accounts'] 
                           if a.get('email') == account.get('email') and a.get('idp') == account.get('idp')), None)
            
            if existing:
                existing.update(account)
            else:
                current_data['accounts'].append(account)
            imported_count += 1
        
        save_accounts(current_data)
        return jsonify({"success": True, "message": f"Imported {imported_count} accounts"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>', methods=['PUT'])
@require_auth
def update_account(account_id):
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        account.update(request.json)
        save_accounts(data)
        return jsonify({"success": True, "account": account})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>', methods=['DELETE'])
@require_auth
def delete_account(account_id):
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
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        success, message = refresh_token(account)
        save_accounts(data)
        return jsonify({"success": success, "message": message, "account": account})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>/details', methods=['GET'])
@require_auth
def get_account_details(account_id):
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        return jsonify({"success": True, "account": account})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>/machine-id', methods=['POST'])
@require_auth
def regenerate_machine_id(account_id):
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

@app.route('/api/accounts/<account_id>/set-current', methods=['POST'])
@require_auth
def set_current_account(account_id):
    """Set an account as the current active account"""
    try:
        data = load_accounts()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        
        settings = load_settings()
        settings['autoSwitch']['currentAccountId'] = account_id
        save_settings(settings)
        
        return jsonify({"success": True, "message": f"Set {account.get('email')} as current account"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/export', methods=['GET'])
@require_auth
def export_accounts():
    data = load_accounts()
    return jsonify(data)

@app.route('/api/stats', methods=['GET'])
@require_auth
def get_stats():
    data = load_accounts()
    settings = load_settings()
    accounts = data.get('accounts', [])
    
    stats = {
        "total": len(accounts),
        "active": len([a for a in accounts if a.get('status') == 'active']),
        "totalCredits": sum(a.get('usage', {}).get('limit', 0) for a in accounts),
        "usedCredits": sum(a.get('usage', {}).get('current', 0) for a in accounts),
        "byProvider": {},
        "currentAccountId": settings['autoSwitch'].get('currentAccountId'),
        "autoRefreshEnabled": settings['autoRefresh']['enabled'],
        "autoRefreshInterval": settings['autoRefresh']['interval'],
        "autoSwitchEnabled": settings['autoSwitch']['enabled']
    }
    
    for account in accounts:
        provider = account.get('idp', 'Unknown')
        stats['byProvider'][provider] = stats['byProvider'].get(provider, 0) + 1
    
    return jsonify(stats)

# ==================== Initialize ====================

# Start scheduler
if not scheduler.running:
    scheduler.start()
    setup_scheduler()
    logger.info("🚀 Scheduler started")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
