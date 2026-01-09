from flask import Flask, request, jsonify, send_from_directory, session, Response, stream_with_context
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
import api_converters
import kiro_chat

# Optional CBOR support for Kiro API
try:
    import cbor2
    CBOR_AVAILABLE = True
except ImportError:
    CBOR_AVAILABLE = False
    logging.warning("cbor2 not installed, usage fetching will be disabled")

# Optional Redis support for Upstash
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logging.warning("redis not installed, using file storage")

# Optional Cryptography for API key encryption
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logging.warning("cryptography not installed, API key encryption will be disabled")

# Optional Pydantic for request validation
try:
    from pydantic import BaseModel, Field
    from typing import List, Optional, Dict, Any, Union, Literal
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logging.warning("pydantic not installed, request validation will be limited")

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
API_KEYS_FILE = os.getenv('API_KEYS_FILE', 'api_keys.json')
USAGE_LOGS_FILE = os.getenv('USAGE_LOGS_FILE', 'usage_logs.json')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', None)
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', None)

# Upstash Redis configuration
UPSTASH_REDIS_URL = os.getenv('UPSTASH_REDIS_URL')  # e.g., redis://default:xxx@xxx.upstash.io:6379
REDIS_ACCOUNTS_KEY = 'kiro:accounts'
REDIS_SETTINGS_KEY = 'kiro:settings'

# Initialize Redis client
redis_client = None
if REDIS_AVAILABLE and UPSTASH_REDIS_URL:
    try:
        redis_client = redis.from_url(UPSTASH_REDIS_URL, decode_responses=True)
        redis_client.ping()
        logger.info("✅ Connected to Upstash Redis")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Upstash Redis: {e}")
        redis_client = None

# Default settings
DEFAULT_SETTINGS = {
    "autoRefresh": {
        "enabled": True,
        "interval": 300,  # seconds (5 minutes default)
        "refreshBeforeExpiry": 300,  # refresh 5 minutes before expiry
        "minValidTime": 1800  # minimum token valid time: 30 minutes
    },
    "autoSwitch": {
        "enabled": False,
        "checkInterval": 1800,  # check every 30 minutes
        "switchThreshold": 90,  # switch when usage > 90%
        "currentAccountId": None
    },
    "statusCheck": {
        "enabled": True,
        "interval": 300  # check every 5 minutes
    },
    "notifications": {
        "onRefreshFail": True,
        "onAutoSwitch": True
    },
    "perAccountRefresh": {
        "enabled": True  # enable per-account independent refresh
    }
}

# Global scheduler
scheduler = BackgroundScheduler(
    job_defaults={
        'coalesce': True,  # 合并错过的执行
        'max_instances': 1,  # 同一任务最多同时运行1个实例
        'misfire_grace_time': 60  # 错过执行时间60秒内仍然执行
    }
)
scheduler_jobs = {}

# ==================== Helper Functions ====================

def get_empty_accounts():
    """Return empty accounts structure"""
    return {"version": "1.3.1", "exportedAt": int(time.time() * 1000), "accounts": [], "groups": [], "tags": []}

def load_accounts():
    """Load accounts from Redis or JSON file"""
    # Try Redis first
    if redis_client:
        try:
            data = redis_client.get(REDIS_ACCOUNTS_KEY)
            if data:
                return json.loads(data)
            logger.info("No accounts in Redis, returning empty")
            return get_empty_accounts()
        except Exception as e:
            logger.error(f"Redis read error: {e}, falling back to file")
    
    # Fallback to file
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    return get_empty_accounts()
                data = json.loads(content)
                # Migrate to Redis if available
                if redis_client:
                    try:
                        redis_client.set(REDIS_ACCOUNTS_KEY, json.dumps(data, ensure_ascii=False))
                        logger.info("Migrated accounts from file to Redis")
                    except:
                        pass
                return data
        except json.JSONDecodeError as e:
            logger.error(f"Corrupted accounts file: {e}")
            return get_empty_accounts()
        except Exception as e:
            logger.error(f"Error loading accounts: {e}")
            return get_empty_accounts()
    return get_empty_accounts()

def save_accounts(data):
    """Save accounts to Redis and/or JSON file"""
    data['exportedAt'] = int(time.time() * 1000)
    
    # Save to Redis if available
    if redis_client:
        try:
            redis_client.set(REDIS_ACCOUNTS_KEY, json.dumps(data, ensure_ascii=False))
            logger.debug("Accounts saved to Redis")
            return  # Success, no need for file backup
        except Exception as e:
            logger.error(f"Redis write error: {e}, falling back to file")
    
    # Fallback to file
    temp_file = f"{ACCOUNTS_FILE}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if os.path.exists(ACCOUNTS_FILE):
            os.replace(temp_file, ACCOUNTS_FILE)
        else:
            os.rename(temp_file, ACCOUNTS_FILE)
    except Exception as e:
        logger.error(f"Error saving accounts: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
        raise

def load_settings():
    """Load settings from Redis or JSON file"""
    # Try Redis first
    if redis_client:
        try:
            data = redis_client.get(REDIS_SETTINGS_KEY)
            if data:
                saved = json.loads(data)
                settings = DEFAULT_SETTINGS.copy()
                for key in settings:
                    if key in saved:
                        if isinstance(settings[key], dict):
                            settings[key].update(saved[key])
                        else:
                            settings[key] = saved[key]
                return settings
        except Exception as e:
            logger.error(f"Redis read error: {e}, falling back to file")
    
    # Fallback to file
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                settings = DEFAULT_SETTINGS.copy()
                for key in settings:
                    if key in saved:
                        if isinstance(settings[key], dict):
                            settings[key].update(saved[key])
                        else:
                            settings[key] = saved[key]
                # Migrate to Redis if available
                if redis_client:
                    try:
                        redis_client.set(REDIS_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))
                        logger.info("Migrated settings from file to Redis")
                    except:
                        pass
                return settings
        except:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    """Save settings to Redis and/or JSON file"""
    # Save to Redis if available
    if redis_client:
        try:
            redis_client.set(REDIS_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))
            logger.debug("Settings saved to Redis")
            return
        except Exception as e:
            logger.error(f"Redis write error: {e}, falling back to file")
    
    # Fallback to file
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
            
            # Update account status to active after successful refresh
            if account.get('status') == 'expired':
                account['status'] = 'active'
                account['statusReason'] = None
                logger.info(f"Account {account.get('email')} status restored to active")
            
            # Try to update usage info
            update_account_usage(account)
            
            # Re-check status after usage update (might be exhausted)
            check_account_status(account)
            
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

def get_token_remaining_time(account):
    """Get remaining time in seconds for account token"""
    credentials = account.get('credentials', {})
    expires_at = credentials.get('expiresAt', 0)
    if not expires_at:
        return 0
    current_time = int(time.time() * 1000)
    remaining_ms = expires_at - current_time
    return max(0, remaining_ms // 1000)

def should_refresh_account(account, settings):
    """Check if account needs token refresh based on settings"""
    min_valid_time = settings['autoRefresh'].get('minValidTime', 1800)  # 30 minutes default
    refresh_before = settings['autoRefresh'].get('refreshBeforeExpiry', 300)
    
    remaining = get_token_remaining_time(account)
    
    # Refresh if remaining time is less than minValidTime or refreshBeforeExpiry
    threshold = max(min_valid_time, refresh_before)
    return remaining < threshold

def auto_refresh_tokens_task():
    """Automatically refresh tokens for all accounts"""
    try:
        logger.info("🔄 Starting automatic token refresh...")
        data = load_accounts()
        settings = load_settings()
        
        min_valid_time = settings['autoRefresh'].get('minValidTime', 1800)  # 30 minutes
        current_time = int(time.time() * 1000)
        
        refreshed = 0
        failed = 0
        skipped = 0
        
        for account in data.get('accounts', []):
            try:
                # Refresh all accounts regardless of status
                # Check if this account needs refresh
                if should_refresh_account(account, settings):
                    remaining = get_token_remaining_time(account)
                    logger.info(f"Account {account.get('email')} needs refresh (remaining: {remaining}s, min: {min_valid_time}s)")
                    success, msg = refresh_token(account)
                    if success:
                        refreshed += 1
                        # Update last refresh time for this account
                        account['lastRefreshedAt'] = current_time
                    else:
                        failed += 1
                        logger.warning(f"Auto refresh failed for {account.get('email')}: {msg}")
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                logger.error(f"Error refreshing account {account.get('email')}: {str(e)}")
        
        save_accounts(data)
        logger.info(f"✅ Token refresh completed: {refreshed} refreshed, {failed} failed, {skipped} skipped")
    except Exception as e:
        logger.error(f"❌ Auto refresh task failed: {str(e)}")

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

def check_account_status(account):
    """Check and update account status based on various conditions"""
    old_status = account.get('status', 'active')
    new_status = 'active'  # Default to active, then check for issues
    status_reason = None
    
    credentials = account.get('credentials', {})
    usage = account.get('usage', {})
    
    # Check 1: No refresh token (highest priority - invalid)
    if not credentials.get('refreshToken'):
        new_status = 'invalid'
        status_reason = 'No refresh token'
    # Check 2: Token expired
    elif credentials.get('expiresAt', 0) and credentials.get('expiresAt', 0) < int(time.time() * 1000):
        new_status = 'expired'
        status_reason = 'Token expired'
    # Check 3: Usage limit exceeded (total usage)
    else:
        total_limit = (usage.get('limit', 0) or 0) + (usage.get('freeTrialLimit', 0) or 0)
        total_current = (usage.get('current', 0) or 0) + (usage.get('freeTrialCurrent', 0) or 0)
        if total_limit > 0 and total_current >= total_limit:
            new_status = 'exhausted'
            status_reason = 'Usage limit exceeded'
    
    # Update status if changed
    if new_status != old_status:
        account['status'] = new_status
        account['statusReason'] = status_reason if status_reason else None
        logger.info(f"Account {account.get('email')} status changed: {old_status} -> {new_status} ({status_reason or 'recovered'})")
        return True
    
    return False

def auto_status_check_task():
    """Periodically check all account statuses"""
    logger.info("🔍 Checking account statuses...")
    data = load_accounts()
    
    changed = 0
    for account in data.get('accounts', []):
        if check_account_status(account):
            changed += 1
    
    if changed > 0:
        save_accounts(data)
        logger.info(f"✅ Status check completed: {changed} account(s) status changed")
    else:
        logger.info("✅ Status check completed: no changes")

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
            replace_existing=True,
            next_run_time=datetime.now() + timedelta(seconds=interval)
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
            replace_existing=True,
            next_run_time=datetime.now() + timedelta(seconds=interval)
        )
        scheduler_jobs['auto_switch'] = job
        logger.info(f"📅 Auto switch check scheduled every {interval} seconds")
    
    # Add status check job
    status_check = settings.get('statusCheck', {})
    if status_check.get('enabled', True):
        interval = status_check.get('interval', 300)
        job = scheduler.add_job(
            func=auto_status_check_task,
            trigger=IntervalTrigger(seconds=interval),
            id='status_check',
            replace_existing=True,
            next_run_time=datetime.now() + timedelta(seconds=interval)
        )
        scheduler_jobs['status_check'] = job
        logger.info(f"📅 Status check scheduled every {interval} seconds")

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

@app.route('/api-keys')
@require_auth
def api_keys_page():
    return send_from_directory('static', 'api-keys.html')

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
            return jsonify({"success": True, "message": "Token 刷新已触发"})
        elif job_id == 'auto_switch':
            auto_switch_account_task()
            return jsonify({"success": True, "message": "自动换号检查已触发"})
        elif job_id == 'status_check':
            auto_status_check_task()
            return jsonify({"success": True, "message": "状态检查已触发"})
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
    
    # Add current account indicator and token status
    current_id = settings['autoSwitch'].get('currentAccountId')
    min_valid_time = settings['autoRefresh'].get('minValidTime', 1800)
    
    for account in data.get('accounts', []):
        account['isCurrent'] = account.get('id') == current_id
        # Add token remaining time for each account
        account['tokenRemainingSeconds'] = get_token_remaining_time(account)
        account['needsRefresh'] = should_refresh_account(account, settings)
        account['minValidTime'] = min_valid_time
    
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
        settings = load_settings()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        success, message = refresh_token(account)
        if success:
            account['lastRefreshedAt'] = int(time.time() * 1000)
            # Add token remaining time to response
            account['tokenRemainingSeconds'] = get_token_remaining_time(account)
            account['needsRefresh'] = should_refresh_account(account, settings)
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
        
        # Add token remaining time
        account['tokenRemainingSeconds'] = get_token_remaining_time(account)
        
        return jsonify({"success": True, "account": account})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/accounts/<account_id>/token-status', methods=['GET'])
@require_auth
def get_account_token_status(account_id):
    """Get token status for a specific account"""
    try:
        data = load_accounts()
        settings = load_settings()
        account = next((a for a in data['accounts'] if a.get('id') == account_id), None)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
        
        credentials = account.get('credentials', {})
        remaining = get_token_remaining_time(account)
        expires_at = credentials.get('expiresAt', 0)
        min_valid_time = settings['autoRefresh'].get('minValidTime', 1800)
        
        return jsonify({
            "success": True,
            "tokenStatus": {
                "remainingSeconds": remaining,
                "expiresAt": expires_at,
                "needsRefresh": should_refresh_account(account, settings),
                "minValidTime": min_valid_time,
                "lastRefreshedAt": account.get('lastRefreshedAt')
            }
        })
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
    
    # Calculate total credits including free trial
    total_credits = 0
    used_credits = 0
    for a in accounts:
        usage = a.get('usage', {})
        total_credits += (usage.get('limit', 0) or 0) + (usage.get('freeTrialLimit', 0) or 0)
        used_credits += (usage.get('current', 0) or 0) + (usage.get('freeTrialCurrent', 0) or 0)
    
    # Get next refresh time
    next_refresh_time = None
    try:
        refresh_job = scheduler.get_job('auto_refresh')
        if refresh_job and refresh_job.next_run_time:
            next_refresh_time = refresh_job.next_run_time.isoformat()
    except:
        pass
    
    status_check = settings.get('statusCheck', {})
    auto_refresh = settings.get('autoRefresh', {})
    
    stats = {
        "total": len(accounts),
        "active": len([a for a in accounts if a.get('status') == 'active']),
        "expired": len([a for a in accounts if a.get('status') in ['expired', 'trial_expired']]),
        "exhausted": len([a for a in accounts if a.get('status') == 'exhausted']),
        "totalCredits": total_credits,
        "usedCredits": used_credits,
        "byProvider": {},
        "byStatus": {},
        "currentAccountId": settings['autoSwitch'].get('currentAccountId'),
        "autoRefreshEnabled": auto_refresh.get('enabled', True),
        "autoRefreshInterval": auto_refresh.get('interval', 1800),
        "minValidTime": auto_refresh.get('minValidTime', 1800),
        "refreshBeforeExpiry": auto_refresh.get('refreshBeforeExpiry', 300),
        "autoSwitchEnabled": settings['autoSwitch']['enabled'],
        "statusCheckEnabled": status_check.get('enabled', True),
        "statusCheckInterval": status_check.get('interval', 300),
        "nextRefreshTime": next_refresh_time
    }
    
    for account in accounts:
        provider = account.get('idp', 'Unknown')
        status = account.get('status', 'unknown')
        stats['byProvider'][provider] = stats['byProvider'].get(provider, 0) + 1
        stats['byStatus'][status] = stats['byStatus'].get(status, 0) + 1
    
    return jsonify(stats)

# ==================== 2API - API Keys Management ====================

def get_encryption_key():
    if not CRYPTO_AVAILABLE:
        return None
    if not ENCRYPTION_KEY:
        return None
    try:
        return Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)
    except Exception as e:
        logger.error(f"Invalid encryption key: {e}")
        return None

def load_api_keys():
    if redis_client:
        try:
            data = redis_client.get('kiro:api_keys')
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis read error for API keys: {e}")
    
    if os.path.exists(API_KEYS_FILE):
        try:
            with open(API_KEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []

def save_api_keys(keys):
    if redis_client:
        try:
            redis_client.set('kiro:api_keys', json.dumps(keys, ensure_ascii=False))
            return
        except Exception as e:
            logger.error(f"Redis write error for API keys: {e}")
    
    with open(API_KEYS_FILE, 'w', encoding='utf-8') as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)

def generate_api_key():
    return 'sk-' + hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()

def hash_api_key(key):
    return hashlib.sha256(key.encode()).hexdigest()

def verify_api_key_auth():
    auth_header = request.headers.get('Authorization') or request.headers.get('X-Api-Key')
    if not auth_header:
        return None
    
    if auth_header.startswith('Bearer '):
        key = auth_header[7:]
    else:
        key = auth_header
    
    key_hash = hash_api_key(key)
    api_keys = load_api_keys()
    
    for api_key in api_keys:
        if api_key.get('key_hash') == key_hash and api_key.get('is_active', True):
            api_key['last_used_at'] = int(time.time() * 1000)
            save_api_keys(api_keys)
            return api_key
    
    return None

@app.route('/api/api-keys', methods=['GET'])
@require_auth
def get_api_keys():
    keys = load_api_keys()
    return jsonify({'success': True, 'keys': [
        {k: v for k, v in key.items() if k != 'key_hash'} 
        for key in keys
    ]})

@app.route('/api/api-keys', methods=['POST'])
@require_auth
def create_api_key():
    try:
        data = request.json or {}
        name = data.get('name', 'Unnamed Key')
        description = data.get('description', '')
        
        new_key = generate_api_key()
        key_hash = hash_api_key(new_key)
        
        api_key_obj = {
            'id': str(uuid.uuid4()),
            'name': name,
            'description': description,
            'key_hash': key_hash,
            'key_prefix': new_key[:12] + '...',
            'created_at': int(time.time() * 1000),
            'last_used_at': None,
            'is_active': True
        }
        
        keys = load_api_keys()
        keys.append(api_key_obj)
        save_api_keys(keys)
        
        return jsonify({
            'success': True,
            'key': new_key,
            'key_info': {k: v for k, v in api_key_obj.items() if k != 'key_hash'}
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/api-keys/<key_id>', methods=['DELETE'])
@require_auth
def delete_api_key(key_id):
    try:
        keys = load_api_keys()
        keys = [k for k in keys if k.get('id') != key_id]
        save_api_keys(keys)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== 2API - Helper Functions ====================

def load_usage_logs():
    if redis_client:
        try:
            data = redis_client.get('kiro:usage_logs')
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis read error for usage logs: {e}")
    
    if os.path.exists(USAGE_LOGS_FILE):
        try:
            with open(USAGE_LOGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []

def save_usage_logs(logs):
    logs = logs[-1000:]
    
    if redis_client:
        try:
            redis_client.set('kiro:usage_logs', json.dumps(logs, ensure_ascii=False))
            return
        except Exception as e:
            logger.error(f"Redis write error for usage logs: {e}")
    
    with open(USAGE_LOGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

def log_usage(model, input_tokens, output_tokens, api_key_id=None):
    logs = load_usage_logs()
    log_entry = {
        'id': str(uuid.uuid4()),
        'timestamp': int(time.time() * 1000),
        'model': model,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'api_key_id': api_key_id
    }
    logs.append(log_entry)
    save_usage_logs(logs)

def get_active_account():
    data = load_accounts()
    settings = load_settings()
    current_id = settings['autoSwitch'].get('currentAccountId')
    
    if current_id:
        account = next((a for a in data['accounts'] if a.get('id') == current_id and a.get('status') == 'active'), None)
        if account:
            return account
    
    active_accounts = [a for a in data['accounts'] if a.get('status') == 'active']
    if active_accounts:
        active_accounts.sort(key=lambda a: get_account_usage_percent(a))
        return active_accounts[0]
    
    return None

# ==================== 2API - Models Endpoint ====================

@app.route('/v1/models', methods=['GET'])
def list_models():
    api_key = verify_api_key_auth()
    if not api_key:
        return jsonify({
            'error': {
                'message': 'Invalid or missing API key',
                'type': 'authentication_error'
            }
        }), 401
    
    models_data = {
        'object': 'list',
        'data': [
            {
                'id': 'kiro-flash',
                'object': 'model',
                'created': 1700000000,
                'owned_by': 'kiro'
            },
            {
                'id': 'kiro-pro',
                'object': 'model',
                'created': 1700000000,
                'owned_by': 'kiro'
            }
        ]
    }
    
    return jsonify(models_data)

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    api_key = verify_api_key_auth()
    if not api_key:
        return jsonify({
            'error': {
                'message': 'Invalid or missing API key',
                'type': 'authentication_error'
            }
        }), 401
    
    try:
        req_data = request.json
        model = req_data.get('model', 'kiro-pro')
        messages = req_data.get('messages', [])
        stream = req_data.get('stream', False)
        max_tokens = req_data.get('max_tokens', 4096)
        temperature = req_data.get('temperature', 1.0)
        
        account = get_active_account()
        if not account:
            return jsonify({
                'error': {
                    'message': 'No active Kiro account available',
                    'type': 'server_error'
                }
            }), 503
        
        credentials = account.get('credentials', {})
        access_token = credentials.get('accessToken')
        if not access_token:
            return jsonify({
                'error': {
                    'message': 'Account access token not available',
                    'type': 'server_error'
                }
            }), 503
        
        kiro_messages = api_converters.openai_to_kiro_messages(messages)
        
        kiro_request = {
            'messages': kiro_messages,
            'max_tokens': max_tokens,
            'temperature': temperature
        }
        
        if stream:
            def generate():
                try:
                    full_content = ''
                    input_tokens = 0
                    output_tokens = 0
                    
                    yield api_converters.create_openai_chunk('', model)
                    
                    for chunk_line in kiro_chat.call_kiro_chat_stream(account, messages, model, max_tokens):
                        parsed = kiro_chat.parse_kiro_stream_chunk(chunk_line)
                        
                        if parsed and parsed.get('type') == 'content':
                            text = parsed.get('text', '')
                            if text:
                                full_content += text
                                output_tokens += len(text.split())
                                yield api_converters.create_openai_chunk(text, model)
                        elif parsed and parsed.get('type') == 'error':
                            logger.error(f"Kiro stream error: {parsed.get('error')}")
                            raise Exception(parsed.get('error', 'Unknown error'))
                    
                    yield api_converters.create_openai_chunk('', model, finish_reason='stop')
                    yield 'data: [DONE]\\n\\n'
                    
                    input_tokens = sum(len(str(m.get('content', '')).split()) for m in messages)
                    log_usage(model, input_tokens, output_tokens, api_key.get('id'))
                    
                except Exception as e:
                    logger.error(f"Streaming error: {e}")
                    error_chunk = {
                        'error': {
                            'message': str(e),
                            'type': 'server_error'
                        }
                    }
                    yield f'data: {json.dumps(error_chunk)}\\n\\n'
            
            return Response(
                stream_with_context(generate()),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
        else:
            full_content = ''
            input_tokens = 0
            output_tokens = 0
            
            for chunk_line in kiro_chat.call_kiro_chat_stream(account, messages, model, max_tokens):
                parsed = kiro_chat.parse_kiro_stream_chunk(chunk_line)
                
                if parsed and parsed.get('type') == 'content':
                    text = parsed.get('text', '')
                    if text:
                        full_content += text
                        output_tokens += len(text.split())
                elif parsed and parsed.get('type') == 'error':
                    logger.error(f"Kiro error: {parsed.get('error')}")
                    raise Exception(parsed.get('error', 'Unknown error'))
            
            input_tokens = sum(len(str(m.get('content', '')).split()) for m in messages)
            log_usage(model, input_tokens, output_tokens, api_key.get('id'))
            
            return jsonify(api_converters.create_openai_response(
                full_content,
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens
            ))
            
    except Exception as e:
        logger.error(f"Chat completion error: {e}")
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

@app.route('/v1/messages', methods=['POST'])
def anthropic_messages():
    api_key = verify_api_key_auth()
    if not api_key:
        return jsonify({
            'error': {
                'type': 'authentication_error',
                'message': 'Invalid or missing API key'
            }
        }), 401
    
    try:
        req_data = request.json
        model = req_data.get('model', 'claude-3-5-sonnet-20241022')
        messages = req_data.get('messages', [])
        system = req_data.get('system')
        stream = req_data.get('stream', False)
        max_tokens = req_data.get('max_tokens', 4096)
        
        account = get_active_account()
        if not account:
            return jsonify({
                'error': {
                    'type': 'api_error',
                    'message': 'No active Kiro account available'
                }
            }), 503
        
        openai_messages = api_converters.anthropic_to_openai_messages(messages, system)
        
        if stream:
            def generate():
                try:
                    message_id = f'msg_{uuid.uuid4().hex[:24]}'
                    full_content = ''
                    input_tokens = 0
                    output_tokens = 0
                    
                    start_event = {
                        'type': 'message_start',
                        'message': {
                            'id': message_id,
                            'type': 'message',
                            'role': 'assistant',
                            'content': [],
                            'model': model
                        }
                    }
                    yield f'event: message_start\\ndata: {json.dumps(start_event)}\\n\\n'
                    
                    block_start = {
                        'type': 'content_block_start',
                        'index': 0,
                        'content_block': {'type': 'text', 'text': ''}
                    }
                    yield f'event: content_block_start\\ndata: {json.dumps(block_start)}\\n\\n'
                    
                    for chunk_line in kiro_chat.call_kiro_chat_stream(account, openai_messages, model, max_tokens):
                        parsed = kiro_chat.parse_kiro_stream_chunk(chunk_line)
                        
                        if parsed and parsed.get('type') == 'content':
                            text = parsed.get('text', '')
                            if text:
                                full_content += text
                                output_tokens += len(text.split())
                                yield api_converters.create_anthropic_chunk(text, model, message_id)
                        elif parsed and parsed.get('type') == 'error':
                            logger.error(f"Kiro stream error: {parsed.get('error')}")
                            raise Exception(parsed.get('error', 'Unknown error'))
                    
                    block_end = {'type': 'content_block_stop', 'index': 0}
                    yield f'event: content_block_stop\\ndata: {json.dumps(block_end)}\\n\\n'
                    
                    yield api_converters.create_anthropic_chunk('', model, message_id, finish_reason='end_turn')
                    
                    message_end = {'type': 'message_stop'}
                    yield f'event: message_stop\\ndata: {json.dumps(message_end)}\\n\\n'
                    
                    input_tokens = sum(len(str(m.get('content', '')).split()) for m in messages)
                    log_usage(model, input_tokens, output_tokens, api_key.get('id'))
                    
                except Exception as e:
                    logger.error(f"Anthropic streaming error: {e}")
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'api_error',
                            'message': str(e)
                        }
                    }
                    yield f'event: error\\ndata: {json.dumps(error_event)}\\n\\n'
            
            return Response(
                stream_with_context(generate()),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no',
                    'anthropic-version': request.headers.get('anthropic-version', '2023-06-01')
                }
            )
        else:
            full_content = ''
            input_tokens = 0
            output_tokens = 0
            
            for chunk_line in kiro_chat.call_kiro_chat_stream(account, openai_messages, model, max_tokens):
                parsed = kiro_chat.parse_kiro_stream_chunk(chunk_line)
                
                if parsed and parsed.get('type') == 'content':
                    text = parsed.get('text', '')
                    if text:
                        full_content += text
                        output_tokens += len(text.split())
                elif parsed and parsed.get('type') == 'error':
                    logger.error(f"Kiro error: {parsed.get('error')}")
                    raise Exception(parsed.get('error', 'Unknown error'))
            
            input_tokens = sum(len(str(m.get('content', '')).split()) for m in messages)
            log_usage(model, input_tokens, output_tokens, api_key.get('id'))
            
            return jsonify(api_converters.create_anthropic_response(
                full_content,
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens
            ))
            
    except Exception as e:
        logger.error(f"Anthropic messages error: {e}")
        return jsonify({
            'error': {
                'type': 'api_error',
                'message': str(e)
            }
        }), 500

@app.route('/api/usage/logs', methods=['GET'])
@require_auth
def get_usage_logs():
    try:
        limit = int(request.args.get('limit', 100))
        logs = load_usage_logs()
        return jsonify({
            'success': True,
            'logs': logs[-limit:]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== Initialize ====================

# Start scheduler
if not scheduler.running:
    scheduler.start()
    setup_scheduler()
    logger.info("🚀 Scheduler started")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
