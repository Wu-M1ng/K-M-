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

# Optional CBOR support for Kiro API
try:
    import cbor2
    CBOR_AVAILABLE = True
except ImportError:
    CBOR_AVAILABLE = False
    logging.warning("cbor2 not installed, usage fetching will be disabled")

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

def get_empty_accounts():
    """Return empty accounts structure"""
    return {"version": "1.3.1", "exportedAt": int(time.time() * 1000), "accounts": [], "groups": [], "tags": []}

def load_accounts():
    """Load accounts from JSON file with error handling"""
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    logger.warning(f"Accounts file is empty, returning default")
                    return get_empty_accounts()
                return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Corrupted accounts file: {e}")
            # Backup corrupted file
            backup_file = f"{ACCOUNTS_FILE}.corrupted.{int(time.time())}"
            try:
                os.rename(ACCOUNTS_FILE, backup_file)
                logger.info(f"Corrupted file backed up to {backup_file}")
            except:
                pass
            return get_empty_accounts()
        except Exception as e:
            logger.error(f"Error loading accounts: {e}")
            return get_empty_accounts()
    return get_empty_accounts()

def save_accounts(data):
    """Save accounts to JSON file safely"""
    data['exportedAt'] = int(time.time() * 1000)
    # Write to temp file first, then rename (atomic operation)
    temp_file = f"{ACCOUNTS_FILE}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Atomic rename
        if os.path.exists(ACCOUNTS_FILE):
            os.replace(temp_file, ACCOUNTS_FILE)
        else:
            os.rename(temp_file, ACCOUNTS_FILE)
    except Exception as e:
        logger.error(f"Error saving accounts: {e}")
        # Clean up temp file
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
        raise

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

# Kiro Auth Service endpoint for social login (GitHub/Google)
KIRO_AUTH_ENDPOINT = 'https://prod.us-east-1.auth.desktop.kiro.dev'
# Kiro API endpoint for usage info
KIRO_API_BASE = 'https://app.kiro.dev/service/KiroWebPortalService/operation'

def generate_invocation_id():
    """Generate a UUID for API invocation"""
    return str(uuid.uuid4())

def convert_to_json_serializable(obj):
    """Convert CBOR decoded objects to JSON serializable types"""
    if isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    else:
        return obj

def kiro_api_request(operation, body, access_token, idp='BuilderId'):
    """Call Kiro API with CBOR format"""
    if not CBOR_AVAILABLE:
        return {'success': False, 'error': 'cbor2 not installed'}
    
    try:
        url = f"{KIRO_API_BASE}/{operation}"
        headers = {
            'accept': 'application/cbor',
            'content-type': 'application/cbor',
            'smithy-protocol': 'rpc-v2-cbor',
            'amz-sdk-invocation-id': generate_invocation_id(),
            'amz-sdk-request': 'attempt=1; max=1',
            'x-amz-user-agent': 'aws-sdk-js/1.0.0 kiro-account-manager/1.0.0',
            'authorization': f'Bearer {access_token}',
            'cookie': f'Idp={idp}; AccessToken={access_token}'
        }
        
        # Encode body as CBOR
        cbor_body = cbor2.dumps(body)
        
        response = requests.post(url, data=cbor_body, headers=headers, timeout=30)
        
        if response.ok:
            # Decode CBOR response and convert to JSON serializable
            result = cbor2.loads(response.content)
            result = convert_to_json_serializable(result)
            return {'success': True, 'data': result}
        else:
            error_msg = f"HTTP {response.status_code}"
            try:
                error_data = cbor2.loads(response.content)
                if error_data.get('message'):
                    error_msg = error_data.get('message')
            except:
                pass
            return {'success': False, 'error': error_msg}
    except Exception as e:
        logger.error(f"Kiro API error: {str(e)}")
        return {'success': False, 'error': str(e)}

def fetch_account_usage(access_token, idp='BuilderId'):
    """Fetch account usage from Kiro API"""
    result = kiro_api_request(
        'GetUserUsageAndLimits',
        {'isEmailRequired': True, 'origin': 'KIRO_IDE'},
        access_token,
        idp
    )
    
    if not result['success']:
        return None
    
    data = result['data']
    usage_info = {}
    
    # Parse usageBreakdownList
    usage_list = data.get('usageBreakdownList', [])
    credit_usage = next((u for u in usage_list if u.get('resourceType') == 'CREDIT' or u.get('displayName') == 'Credits'), None)
    
    if credit_usage:
        # Base usage
        usage_info['limit'] = credit_usage.get('usageLimitWithPrecision') or credit_usage.get('usageLimit') or 0
        usage_info['current'] = credit_usage.get('currentUsageWithPrecision') or credit_usage.get('currentUsage') or 0
        
        # Free trial info
        free_trial = credit_usage.get('freeTrialInfo', {})
        if free_trial.get('freeTrialStatus') == 'ACTIVE':
            usage_info['freeTrialLimit'] = free_trial.get('usageLimitWithPrecision') or free_trial.get('usageLimit') or 0
            usage_info['freeTrialCurrent'] = free_trial.get('currentUsageWithPrecision') or free_trial.get('currentUsage') or 0
            usage_info['freeTrialExpiry'] = free_trial.get('freeTrialExpiry')
        
        # Bonuses
        bonuses = credit_usage.get('bonuses', [])
        if bonuses:
            usage_info['bonuses'] = []
            for bonus in bonuses:
                if bonus.get('status') == 'ACTIVE':
                    usage_info['bonuses'].append({
                        'code': bonus.get('bonusCode', ''),
                        'name': bonus.get('displayName', ''),
                        'current': bonus.get('currentUsageWithPrecision') or bonus.get('currentUsage') or 0,
                        'limit': bonus.get('usageLimitWithPrecision') or bonus.get('usageLimit') or 0,
                        'expiresAt': bonus.get('expiresAt')
                    })
    
    # Subscription info
    sub_info = data.get('subscriptionInfo', {})
    subscription = {
        'type': sub_info.get('subscriptionTitle') or sub_info.get('type') or 'Unknown',
        'upgradeCapability': sub_info.get('upgradeCapability'),
        'overageCapability': sub_info.get('overageCapability')
    }
    
    # Days until reset
    if data.get('nextDateReset'):
        try:
            from datetime import datetime
            reset_date = datetime.fromisoformat(data['nextDateReset'].replace('Z', '+00:00'))
            days_remaining = (reset_date - datetime.now(reset_date.tzinfo)).days
            subscription['daysRemaining'] = max(0, days_remaining)
            usage_info['nextDateReset'] = data['nextDateReset']
        except:
            pass
    
    return {'usage': usage_info, 'subscription': subscription}

def refresh_oidc_token(refresh_token_value, client_id, client_secret, region='us-east-1'):
    """Refresh token using AWS OIDC endpoint (for BuilderId/IdC login)"""
    url = f"https://oidc.{region}.amazonaws.com/token"
    
    # AWS OIDC uses JSON format with camelCase field names
    payload = {
        'clientId': client_id,
        'clientSecret': client_secret,
        'refreshToken': refresh_token_value,
        'grantType': 'refresh_token'
    }
    
    headers = {'Content-Type': 'application/json'}
    
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    
    if response.ok:
        data = response.json()
        return {
            'success': True,
            'accessToken': data.get('accessToken'),
            'refreshToken': data.get('refreshToken') or refresh_token_value,
            'expiresIn': data.get('expiresIn', 3600)
        }
    else:
        error_text = response.text
        try:
            error_data = response.json()
            error_text = error_data.get('error_description', error_data.get('error', error_text))
        except:
            pass
        return {'success': False, 'error': f"HTTP {response.status_code}: {error_text}"}

def refresh_social_token(refresh_token_value):
    """Refresh token using Kiro Auth Service (for GitHub/Google social login)"""
    url = f"{KIRO_AUTH_ENDPOINT}/refreshToken"
    
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'kiro-account-manager/1.0.0'
    }
    
    response = requests.post(url, json={'refreshToken': refresh_token_value}, headers=headers, timeout=30)
    
    if response.ok:
        data = response.json()
        return {
            'success': True,
            'accessToken': data.get('accessToken'),
            'refreshToken': data.get('refreshToken') or refresh_token_value,
            'expiresIn': data.get('expiresIn', 3600)
        }
    else:
        error_text = response.text
        try:
            error_data = response.json()
            error_text = error_data.get('error_description', error_data.get('error', error_text))
        except:
            pass
        return {'success': False, 'error': f"HTTP {response.status_code}: {error_text}"}

def refresh_token(account):
    """Refresh access token for an account - auto-detect auth method"""
    try:
        credentials = account.get('credentials', {})
        refresh_token_value = credentials.get('refreshToken')
        client_id = credentials.get('clientId')
        client_secret = credentials.get('clientSecret')
        region = credentials.get('region', 'us-east-1')
        auth_method = credentials.get('authMethod')
        idp = account.get('idp', 'BuilderId')
        
        if not refresh_token_value:
            return False, "No refresh token available"
        
        # Determine auth method: social login (GitHub/Google) or IdC (BuilderId)
        is_social = auth_method == 'social' or idp in ['Github', 'Google']
        
        if is_social:
            # Social login uses Kiro Auth Service
            logger.info(f"Refreshing social token for {account.get('email')} (idp: {idp})")
            result = refresh_social_token(refresh_token_value)
        else:
            # IdC/BuilderId uses AWS OIDC
            if not client_id or not client_secret:
                return False, "Missing OIDC credentials (clientId/clientSecret)"
            logger.info(f"Refreshing OIDC token for {account.get('email')} (region: {region})")
            result = refresh_oidc_token(refresh_token_value, client_id, client_secret, region)
        
        if result['success']:
            credentials['accessToken'] = result['accessToken']
            if result.get('refreshToken'):
                credentials['refreshToken'] = result['refreshToken']
            credentials['expiresAt'] = int(time.time() * 1000) + (result.get('expiresIn', 3600) * 1000)
            account['lastCheckedAt'] = int(time.time() * 1000)
            
            # Try to update usage info
            update_account_usage(account)
            
            logger.info(f"Token refreshed successfully for {account.get('email')}")
            return True, "Token refreshed successfully"
        else:
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"Failed to refresh token for {account.get('email')}: {error_msg}")
            return False, error_msg
            
    except requests.exceptions.Timeout:
        return False, "Request timeout"
    except Exception as e:
        logger.error(f"Error refreshing token for {account.get('email')}: {str(e)}")
        return False, str(e)

def update_account_usage(account):
    """Update account usage information by calling Kiro API"""
    try:
        credentials = account.get('credentials', {})
        access_token = credentials.get('accessToken')
        idp = account.get('idp', 'BuilderId')
        
        if not access_token:
            logger.warning(f"No access token for {account.get('email')}, skipping usage update")
            account['lastCheckedAt'] = int(time.time() * 1000)
            return
        
        logger.info(f"Fetching usage for {account.get('email')}...")
        result = fetch_account_usage(access_token, idp)
        
        if result:
            # Update usage info
            if result.get('usage'):
                account['usage'] = result['usage']
            if result.get('subscription'):
                account['subscription'] = result['subscription']
            logger.info(f"Usage updated for {account.get('email')}: {result.get('usage', {}).get('current', 0)}/{result.get('usage', {}).get('limit', 0)}")
        else:
            logger.warning(f"Failed to fetch usage for {account.get('email')}")
        
        account['lastCheckedAt'] = int(time.time() * 1000)
    except Exception as e:
        logger.error(f"Error updating usage for {account.get('email')}: {str(e)}")
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
