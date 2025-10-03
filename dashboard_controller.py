import threading
import random
import time
import json
import os
import logging
import queue
import signal
import sys
import atexit
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import instagram_bot as bot_module
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
from collections import defaultdict
import requests

# Airtable integration
try:
    from pyairtable import Api

    AIRTABLE_AVAILABLE = True
except ImportError:
    AIRTABLE_AVAILABLE = False
    logging.warning("pyairtable not installed. Install with: pip install pyairtable")

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
try:
    from api_config import (
        AIRTABLE_PERSONAL_ACCESS_TOKEN,
        AIRTABLE_BASE_ID,
        AIRTABLE_TABLE_NAME,
        AIRTABLE_VIEW_ID,
        AIRTABLE_LINKED_TABLE_ID,
        ADSPOWER_API_URL,
        ADSPOWER_API_KEY
    )
except ImportError:
    logger.error("api_config.py not found! Please copy api_config_template.py to api_config.py and fill in your API keys.")
    sys.exit(1)

PORT = 8080
STATS_FILE = 'profile_stats.json'
STATUS_FILE = 'profile_status.json'
CONFIG_FILE = 'config.json'
MAX_CONCURRENT_PROFILES = 70

# Global state with thread-safe access
profiles = {}
profiles_lock = threading.RLock()  # Use RLock for nested locking

username_queue = queue.Queue()
pending_profiles_queue = []
active_profiles_count = 0
username_update_counter = 0  # Track when to update file

# Cache for statistics to improve performance
stats_cache = {
    'active_count': 0,
    'pending_count': 0,
    'last_update': 0
}
stats_cache_lock = threading.Lock()

# Flag to control automatic profile replacement (only after Start All)
auto_replacement_enabled = False
auto_replacement_lock = threading.Lock()

# Removed dashboard cache - we'll read directly from Airtable and files

# Separate thread pools for different operations
profile_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="profile")
airtable_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="airtable")
io_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="io")
dashboard_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="dashboard")

# Request tracking
request_counter = 0
request_lock = threading.Lock()

# Locks - minimized and separate
concurrent_lock = threading.Lock()
username_queue_lock = threading.Lock()
stats_write_lock = threading.Lock()
status_write_lock = threading.Lock()
config_lock = threading.Lock()


def query_adspower_profile(profile_id):
    """Query AdsPower API for profile information"""
    try:
        # Try querying by serial_number directly
        url = f"{ADSPOWER_API_URL}/api/v1/user/list"
        params = {
            "serial_number": profile_id,
            "page": 1,
            "page_size": 100
        }
        headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
        
        logger.debug(f"Querying AdsPower for profile ID: {profile_id}")
        response = requests.get(url, params=params, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0 and data.get('data', {}).get('list'):
                # Search through results for matching serial_number
                for profile in data['data']['list']:
                    if profile.get('serial_number') == profile_id:
                        name = profile.get('name', None)
                        logger.debug(f"Found profile {profile_id} with name: {name}")
                        return name
        
        logger.debug(f"No profile found for ID: {profile_id}")
        return None
    except Exception as e:
        logger.error(f"Error querying AdsPower profile {profile_id}: {e}")
        return None


def batch_query_adspower_profiles(profile_ids):
    """Query AdsPower API for multiple profiles at once"""
    if not profile_ids:
        return {}
    
    print(f"Querying AdsPower for {len(profile_ids)} profiles...")
    
    all_profiles = {}
    profile_id_set = set(profile_ids)  # Convert to set for faster lookups
    
    try:
        headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
        
        # First get all profiles with larger page size
        params = {
            'page_size': 500  # Increase page size
        }
        
        page = 1
        total_fetched = 0
        
        while True:
            params['page'] = page
            
            try:
                response = requests.get(f"{ADSPOWER_API_URL}/api/v1/user/list", 
                                      params=params, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0 and data.get('data', {}).get('list'):
                        profiles = data['data']['list']
                        
                        # Map profiles by user_id
                        for profile in profiles:
                            user_id = profile.get('user_id', '')
                            if user_id in profile_id_set:
                                name = profile.get('name', '')
                                serial_number = profile.get('serial_number', '')
                                all_profiles[str(user_id)] = {
                                    'name': name,
                                    'serial_number': serial_number
                                }
                        
                        total_fetched += len(profiles)
                        print(f"Fetched page {page}: {len(profiles)} profiles, found {len(all_profiles)} matching so far")
                        
                        # Check if we found all profiles we need
                        if len(all_profiles) >= len(profile_ids):
                            print(f"Found all required profiles!")
                            break
                        
                        # Check if we have more pages
                        total_count = data.get('data', {}).get('count', 0)
                        if total_fetched >= total_count or len(profiles) < 500:
                            break
                        
                        page += 1
                        time.sleep(0.2)  # Small rate limit between pages
                    else:
                        break
                else:
                    print(f"AdsPower API error: {response.status_code}")
                    break
                    
            except Exception as e:
                print(f"Error querying AdsPower page {page}: {e}")
                break
        
        print(f"Retrieved {len(all_profiles)} AdsPower profiles from {total_fetched} total profiles")
        
        # For any missing profiles, query individually with concurrent requests
        missing_profiles = [pid for pid in profile_ids if str(pid) not in all_profiles]
        if missing_profiles:
            print(f"Querying {len(missing_profiles)} missing profiles individually...")
            
            # Use ThreadPoolExecutor for concurrent requests
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def query_single_profile(user_id):
                try:
                    params = {
                        'user_id': str(user_id),
                        'page_size': 1
                    }
                    
                    response = requests.get(f"{ADSPOWER_API_URL}/api/v1/user/list", 
                                          params=params, headers=headers, timeout=5)
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('code') == 0 and data.get('data', {}).get('list'):
                            profiles = data['data']['list']
                            if profiles:
                                profile = profiles[0]
                                name = profile.get('name', '')
                                serial_number = profile.get('serial_number', '')
                                return user_id, {
                                    'name': name,
                                    'serial_number': serial_number
                                }
                    return None
                        
                except Exception as e:
                    print(f"Error querying user_id {user_id}: {e}")
                    return None
            
            # Query up to 10 profiles concurrently
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(query_single_profile, uid): uid for uid in missing_profiles[:20]}  # Limit to 20
                
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        user_id, profile_data = result
                        all_profiles[str(user_id)] = profile_data
                
    except Exception as e:
        print(f"Error querying AdsPower: {e}")
    
    return all_profiles


def get_profile_name(pid):
    """Get AdsPower profile name for a profile ID, fallback to ID if not found"""
    key = str(pid)
    if key in profiles:
        # First try AdsPower profile name
        if profiles[key].get('adspower_name'):
            return profiles[key]['adspower_name']
        # Fallback to username
        elif profiles[key].get('username'):
            return profiles[key]['username']
        # For profiles without AdsPower name, try to create a proper name
        elif profiles[key].get('profile_number'):
            return f"Melina {profiles[key]['profile_number']}"
    return f"Profile {pid}"


def get_active_profiles_count():
    """Get count of active profiles in AdsPower"""
    try:
        url = f"{ADSPOWER_API_URL}/api/v1/user/list"
        params = {'page_size': 500}
        headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0:
                profiles_list = data.get('data', {}).get('list', [])
                # Count profiles with status 'Active' or 'Running'
                active_count = len([p for p in profiles_list if p.get('status') in ['Active', 'Running']])
                return active_count, len(profiles_list)
        return 0, 0
    except Exception as e:
        logger.error(f"Error getting active profiles count: {e}")
        return 0, 0


class DashboardDataManager:
    """Manages dashboard data by reading directly from sources - no caching"""

    @staticmethod
    def get_dashboard_data():
        """Get fresh data for dashboard display - reads directly from sources"""
        try:
            # Get profiles data from memory (already loaded from Airtable)
            profiles_data = {}
            with profiles_lock:
                # Check if profiles are still loading
                if not profiles:
                    return {
                        'profiles': {},
                        'stats': {},
                        'status': {},
                        'loading': True,
                        'message': 'Loading profiles from Airtable...'
                    }
                
                for pid, info in profiles.items():
                    # Only copy serializable data, skip thread and bot objects
                    profiles_data[pid] = {
                        'status': info.get('status', 'Not Running'),
                        'stop_requested': info.get('stop_requested', False),
                        'username': info.get('username', 'Unknown'),
                        'adspower_name': info.get('adspower_name'),
                        'airtable_status': info.get('airtable_status', 'Alive'),
                        'vps_status': info.get('vps_status', 'None'),
                        'phase': info.get('phase', 'None'),
                        'batch': info.get('batch', 'None'),
                        'profile_number': info.get('profile_number', pid),
                        'has_assigned_followers': info.get('assigned_followers_file') is not None,
                        'assigned_followers_count': ProfileSpecificUsernameManager.get_remaining_count_for_profile(pid),
                        'temp_stats': info.get('temp_stats', {
                            'last_run': 0,
                            'today': 0,
                            'total': 0
                        })
                    }

            # Load stats from file
            stats_data = {}
            if os.path.exists(STATS_FILE):
                try:
                    with open(STATS_FILE, 'r') as f:
                        stats_file_data = json.load(f)
                    
                    # Process stats
                    today = datetime.now().strftime('%Y-%m-%d')
                    for pid, stats in stats_file_data.items():
                        today_count = stats.get('today', {}).get(today, 0)
                        stats_data[pid] = {
                            'last_run': stats.get('last_run', 0),
                            'today': today_count,
                            'total_all_time': stats.get('total_all_time', 0)
                        }
                except Exception as e:
                    logger.error(f"Error loading stats: {e}")

            # Load status from file
            status_data = {}
            if os.path.exists(STATUS_FILE):
                try:
                    with open(STATUS_FILE, 'r') as f:
                        status_data = json.load(f)
                except Exception as e:
                    logger.error(f"Error loading status: {e}")

            return {
                'profiles': profiles_data,
                'stats': stats_data,
                'status': status_data
            }

        except Exception as e:
            logger.error(f"Error getting dashboard data: {e}")
            return {'profiles': {}, 'stats': {}, 'status': {}}


class AsyncFileManager:
    """Handles all file I/O operations asynchronously"""

    @staticmethod
    def write_stats_async(stats_update):
        """Write stats update asynchronously"""
        io_executor.submit(AsyncFileManager._write_stats, stats_update)

    @staticmethod
    def _write_stats(stats_update):
        """Internal stats writer"""
        try:
            with stats_write_lock:
                # Load existing
                existing = {}
                if os.path.exists(STATS_FILE):
                    try:
                        with open(STATS_FILE, 'r') as f:
                            existing = json.load(f)
                    except:
                        pass

                # Update
                existing.update(stats_update)

                # Write to temp file
                temp_file = STATS_FILE + '.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(existing, f, indent=2)

                # Atomic rename
                os.replace(temp_file, STATS_FILE)

        except Exception as e:
            logger.error(f"Error writing stats: {e}")

    @staticmethod
    def write_status_async(status_update):
        """Write status update asynchronously"""
        io_executor.submit(AsyncFileManager._write_status, status_update)

    @staticmethod
    def _write_status(status_update):
        """Internal status writer"""
        try:
            with status_write_lock:
                # Load existing
                existing = {}
                if os.path.exists(STATUS_FILE):
                    try:
                        with open(STATUS_FILE, 'r') as f:
                            existing = json.load(f)
                    except:
                        pass

                # Update
                for key, value in status_update.items():
                    if value is None and key in existing:
                        del existing[key]
                    else:
                        existing[key] = value

                # Write to temp file
                temp_file = STATUS_FILE + '.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(existing, f, indent=2)

                # Atomic rename
                os.replace(temp_file, STATUS_FILE)

        except Exception as e:
            logger.error(f"Error writing status: {e}")


class ConfigManager:
    """Manages configuration settings"""

    @staticmethod
    def load_config():
        """Load configuration from file"""
        try:
            with config_lock:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r') as f:
                        return json.load(f)
                else:
                    # Return default configuration
                    default_config = {
                        "delays": {
                            "between_follows": [8, 20],
                            "pre_action_delay": [2, 8],
                            "page_load_wait": [0.5, 2],
                            "follow_check_timeout": 8,
                            "extended_break_interval": [5, 10],
                            "extended_break_duration": [60, 120],
                            "very_long_break_chance": 0.03,
                            "very_long_break_duration": [300, 600],
                            "profile_start_delay": 3,
                            "hourly_reset_break": [600, 1200]
                        },
                        "limits": {
                            "max_follows_per_hour": 35,
                            "max_follows_per_profile": [40, 45]
                        }
                    }
                    ConfigManager.save_config(default_config)
                    return default_config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return None

    @staticmethod
    def save_config(config):
        """Save configuration to file"""
        try:
            with config_lock:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(config, f, indent=2)
                logger.info("Configuration saved successfully")
                return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False


class ProfileSpecificUsernameManager:
    """Manages profile-specific username allocation from assigned followers files"""
    
    # Dictionary to store queues for each profile
    profile_queues = {}
    profile_queues_lock = threading.Lock()
    
    @staticmethod
    def load_profile_usernames(profile_id, followers_file):
        """Load usernames from a profile's assigned followers file"""
        try:
            if not followers_file or not os.path.exists(followers_file):
                logger.warning(f"No followers file for profile {profile_id}")
                return 0
            
            with open(followers_file, 'r', encoding='utf-8') as f:
                usernames = [line.strip() for line in f.readlines() if line.strip()]
            
            # Create a queue for this profile
            with ProfileSpecificUsernameManager.profile_queues_lock:
                if profile_id not in ProfileSpecificUsernameManager.profile_queues:
                    ProfileSpecificUsernameManager.profile_queues[profile_id] = queue.Queue()
                
                profile_queue = ProfileSpecificUsernameManager.profile_queues[profile_id]
                
                # Clear existing queue first
                while not profile_queue.empty():
                    try:
                        profile_queue.get_nowait()
                    except queue.Empty:
                        break
                
                # Add new usernames to queue
                for username in usernames:
                    profile_queue.put(username)
            
            logger.info(f"Loaded {len(usernames)} usernames for profile {profile_id} from {followers_file}")
            return len(usernames)
            
        except Exception as e:
            logger.error(f"Error loading usernames for profile {profile_id}: {e}")
            return 0
    
    @staticmethod
    def get_next_username_for_profile(profile_id):
        """Get next username for a specific profile"""
        try:
            with ProfileSpecificUsernameManager.profile_queues_lock:
                if profile_id not in ProfileSpecificUsernameManager.profile_queues:
                    logger.warning(f"No username queue for profile {profile_id}")
                    return None
                
                profile_queue = ProfileSpecificUsernameManager.profile_queues[profile_id]
                username = profile_queue.get_nowait()
                logger.debug(f"Profile {profile_id}: Allocated username '{username}'")
                return username
                
        except queue.Empty:
            logger.info(f"Profile {profile_id}: No more usernames available")
            return None
    
    @staticmethod
    def get_remaining_count_for_profile(profile_id):
        """Get remaining username count for a specific profile"""
        with ProfileSpecificUsernameManager.profile_queues_lock:
            if profile_id not in ProfileSpecificUsernameManager.profile_queues:
                return 0
            return ProfileSpecificUsernameManager.profile_queues[profile_id].qsize()


class UsernameManager:
    """Manages username allocation"""

    @staticmethod
    def load_usernames_to_queue():
        """Load all usernames from file into memory queue"""
        try:
            if os.path.exists('usernames.txt'):
                with open('usernames.txt', 'r', encoding='utf-8') as f:
                    usernames = [line.strip() for line in f.readlines() if line.strip()]

                # Clear existing queue first
                with username_queue_lock:
                    while not username_queue.empty():
                        try:
                            username_queue.get_nowait()
                        except queue.Empty:
                            break

                    # Add new usernames to queue
                    for username in usernames:
                        username_queue.put(username)

                logger.info(f"Loaded {len(usernames)} usernames into memory queue")
                return len(usernames)
            return 0
        except Exception as e:
            logger.error(f"Error loading usernames: {e}")
            return 0

    @staticmethod
    def get_next_username():
        """Get next username from memory queue and immediately update file"""
        try:
            with username_queue_lock:
                username = username_queue.get_nowait()
                logger.debug(f"Username '{username}' allocated from memory queue")
                
                # Immediately update the file to reflect the removal
                # Submit to IO executor for async processing but don't wait
                io_executor.submit(UsernameManager._update_username_file)
                
                return username
        except queue.Empty:
            logger.info("No more usernames available in memory queue")
            return None
    
    @staticmethod
    def _update_username_file():
        """Update usernames.txt file with current queue contents"""
        try:
            with username_queue_lock:
                # Get all remaining usernames from queue
                remaining_usernames = []
                temp_list = []
                
                # Empty the queue temporarily to get all items
                while not username_queue.empty():
                    try:
                        temp_list.append(username_queue.get_nowait())
                    except queue.Empty:
                        break
                
                # Put them back in the queue
                for username in temp_list:
                    username_queue.put(username)
                    remaining_usernames.append(username)
                
                # Write to file
                with open('usernames.txt', 'w', encoding='utf-8') as f:
                    for username in remaining_usernames:
                        f.write(username + '\n')
                
                logger.debug(f"Updated usernames.txt with {len(remaining_usernames)} usernames")
                
        except Exception as e:
            logger.error(f"Error updating username file: {e}")

    @staticmethod
    def get_remaining_count():
        """Get remaining username count from memory queue"""
        with username_queue_lock:
            return username_queue.qsize()


class StatsManager:
    """Manages profile statistics with async writes"""

    @staticmethod
    def get_today_key():
        """Get today's date as key"""
        return datetime.now().strftime('%Y-%m-%d')

    @staticmethod
    def increment_follow_count(profile_id):
        """Increment follow counts for a profile"""
        try:
            pid_str = str(profile_id)
            today = StatsManager.get_today_key()

            # Update in-memory first
            with profiles_lock:
                if pid_str in profiles:
                    if 'temp_stats' not in profiles[pid_str]:
                        profiles[pid_str]['temp_stats'] = {'last_run': 0, 'today': 0, 'total': 0}
                    profiles[pid_str]['temp_stats']['last_run'] += 1
                    profiles[pid_str]['temp_stats']['today'] += 1
                    profiles[pid_str]['temp_stats']['total'] += 1

            # Load current stats
            stats = {}
            if os.path.exists(STATS_FILE):
                try:
                    with open(STATS_FILE, 'r') as f:
                        stats = json.load(f)
                except:
                    pass

            if pid_str not in stats:
                stats[pid_str] = {
                    'last_run': 0,
                    'today': {},
                    'total_all_time': 0
                }

            stats[pid_str]['last_run'] += 1

            if today not in stats[pid_str]['today']:
                stats[pid_str]['today'][today] = 0
            stats[pid_str]['today'][today] += 1

            stats[pid_str]['total_all_time'] += 1

            # Write asynchronously
            AsyncFileManager.write_stats_async({pid_str: stats[pid_str]})

        except Exception as e:
            logger.error(f"Error incrementing follow count: {e}")

    @staticmethod
    def reset_last_run_count(profile_id):
        """Reset last run count when profile starts"""
        try:
            pid_str = str(profile_id)

            # Update in-memory
            with profiles_lock:
                if pid_str in profiles:
                    if 'temp_stats' not in profiles[pid_str]:
                        profiles[pid_str]['temp_stats'] = {'last_run': 0, 'today': 0, 'total': 0}
                    profiles[pid_str]['temp_stats']['last_run'] = 0

            # Load current stats
            stats = {}
            if os.path.exists(STATS_FILE):
                try:
                    with open(STATS_FILE, 'r') as f:
                        stats = json.load(f)
                except:
                    pass

            if pid_str not in stats:
                stats[pid_str] = {
                    'last_run': 0,
                    'today': {},
                    'total_all_time': 0
                }

            stats[pid_str]['last_run'] = 0

            # Write asynchronously
            AsyncFileManager.write_stats_async({pid_str: stats[pid_str]})

        except Exception as e:
            logger.error(f"Error resetting last run count: {e}")

    @staticmethod
    def get_profile_stats(profile_id):
        """Get current stats for a profile from cache"""
        pid_str = str(profile_id)

        # Check in-memory stats first
        with profiles_lock:
            if pid_str in profiles and 'temp_stats' in profiles[pid_str]:
                temp_stats = profiles[pid_str]['temp_stats']
                return {
                    'last_run': temp_stats.get('last_run', 0),
                    'today': temp_stats.get('today', 0),
                    'total_all_time': temp_stats.get('total', 0)
                }

        # Check file-based stats
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, 'r') as f:
                    stats_data = json.load(f)
                if pid_str in stats_data:
                    today = datetime.now().strftime('%Y-%m-%d')
                    today_count = stats_data[pid_str].get('today', {}).get(today, 0)
                    return {
                        'last_run': stats_data[pid_str].get('last_run', 0),
                        'today': today_count,
                        'total_all_time': stats_data[pid_str].get('total_all_time', 0)
                    }
            except:
                pass

        # Default
        return {
            'last_run': 0,
            'today': 0,
            'total_all_time': 0
        }


class StatusManager:
    """Manages profile status persistence with async writes"""

    @staticmethod
    def get_persistent_status(profile_id):
        """Get persistent status for a profile from file"""
        try:
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status_data = json.load(f)
                return status_data.get(str(profile_id))
            return None
        except Exception as e:
            logger.error(f"Error getting persistent status for {profile_id}: {e}")
            return None

    @staticmethod
    def mark_profile_blocked(profile_id):
        """Mark profile as permanently blocked"""
        try:
            # profile_id is the AdsPower ID (like 'k144ink8')
            # We need to find the profile_number (like '1091') from the profiles cache
            pid_str = str(profile_id)
            profile_number = None

            # Update in-memory - search by AdsPower ID first, then by other identifiers
            with profiles_lock:
                if pid_str in profiles:
                    info = profiles[pid_str]
                    info['status'] = 'Blocked'
                    info['stop_requested'] = True
                    info['airtable_status'] = 'Follow Block'
                    profile_number = info.get('profile_number')
                    logger.info(f"Profile {profile_id} found by AdsPower ID and marked as BLOCKED")
                else:
                    # Try to find by profile_number or adspower_serial
                    found = False
                    for key, info in profiles.items():
                        if info.get('profile_number') == pid_str or info.get('adspower_serial') == pid_str:
                            info['status'] = 'Blocked'
                            info['stop_requested'] = True
                            info['airtable_status'] = 'Follow Block'
                            profile_number = info.get('profile_number')
                            logger.info(f"Profile {profile_id} found by profile_number/adspower_serial and marked as BLOCKED")
                            found = True
                            break
                    
                    if not found:
                        logger.warning(f"Profile {profile_id} not found in profiles cache")

            # Write status asynchronously
            if pid_str in profiles:
                AsyncFileManager.write_status_async({pid_str: 'blocked'})

            logger.info(f"Profile {profile_id} marked as BLOCKED")

            # Update Airtable asynchronously (only if we have a profile_number)
            if profile_number:
                # Update status to 'Follow Block'
                airtable_executor.submit(
                    AirtableManager.update_profile_status,
                    profile_number,
                    'Follow Block'
                )
                # Update 'Reached Follow Limit' field with current datetime
                airtable_executor.submit(
                    AirtableManager.update_reached_follow_limit,
                    profile_number,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                )
            else:
                logger.warning(f"Profile {profile_id}: No profile_number found, skipping Airtable update")

        except Exception as e:
            logger.error(f"Error marking profile {profile_id} as blocked: {e}")

    @staticmethod
    def mark_profile_suspended(profile_id):
        """Mark profile as permanently suspended"""
        try:
            pid_str = str(profile_id)

            # Update in-memory
            with profiles_lock:
                if pid_str in profiles:
                    profiles[pid_str]['status'] = 'Suspended'
                    profiles[pid_str]['airtable_status'] = 'Suspended'
                    
                    # Get profile_number for Airtable update
                    profile_number = profiles[pid_str].get('profile_number')

            # Write status asynchronously
            AsyncFileManager.write_status_async({pid_str: 'suspended'})

            logger.info(f"Profile {profile_id} marked as SUSPENDED")

            # Update Airtable asynchronously (only if we have a profile_number)
            if profile_number:
                airtable_executor.submit(
                    AirtableManager.update_profile_status,
                    profile_number,
                    'Suspended'
                )
            else:
                logger.warning(f"Profile {profile_id}: No profile_number found, skipping Airtable update")

        except Exception as e:
            logger.error(f"Error marking profile {profile_id} as suspended: {e}")

    @staticmethod
    def mark_profile_something_wrong(profile_id):
        """Mark profile as blocked due to Something went wrong error"""
        try:
            pid_str = str(profile_id)
            profile_number = None

            # Update in-memory - search by AdsPower ID first, then by other identifiers
            with profiles_lock:
                if pid_str in profiles:
                    info = profiles[pid_str]
                    info['status'] = 'Blocked'
                    info['stop_requested'] = True
                    info['airtable_status'] = 'Something went wrong Checkpoint'
                    profile_number = info.get('profile_number')
                    logger.info(f"Profile {profile_id} found by AdsPower ID and marked as BLOCKED with Something went wrong Checkpoint")
                else:
                    # Try to find by profile_number or adspower_serial
                    found = False
                    for key, info in profiles.items():
                        if info.get('profile_number') == pid_str or info.get('adspower_serial') == pid_str:
                            info['status'] = 'Blocked'
                            info['stop_requested'] = True
                            info['airtable_status'] = 'Something went wrong Checkpoint'
                            profile_number = info.get('profile_number')
                            logger.info(f"Profile {profile_id} found by profile_number/adspower_serial and marked as BLOCKED with Something went wrong Checkpoint")
                            found = True
                            break
                    
                    if not found:
                        logger.warning(f"Profile {profile_id} not found in profiles cache for Something went wrong status update")
                        return False

            # Write status to file asynchronously
            AsyncFileManager.write_status_async({pid_str: 'blocked'})

            logger.info(f"Profile {profile_id} BLOCKED due to Something went wrong - status: Blocked, airtable_status: Something went wrong Checkpoint")
            return True

        except Exception as e:
            logger.error(f"Error marking profile {profile_id} as Something went wrong: {e}")
            return False

    @staticmethod
    def revive_profile_status(profile_id):
        """Revive a blocked/suspended profile back to alive"""
        try:
            pid_str = str(profile_id)

            # Update in-memory
            with profiles_lock:
                if pid_str in profiles:
                    profiles[pid_str]['status'] = 'Not Running'
                    profiles[pid_str]['airtable_status'] = ['Alive']  # Keep as list for consistency
                    profiles[pid_str]['stop_requested'] = False
                    
                    # Get profile_number for Airtable update
                    profile_number = profiles[pid_str].get('profile_number')

            # Write status asynchronously (None = delete)
            AsyncFileManager.write_status_async({pid_str: None})

            logger.info(f"Profile {profile_id} REVIVED - status: Not Running, airtable_status: Alive")

            # Update Airtable asynchronously (only if we have a profile_number)
            if profile_number:
                # First remove Follow Block status, then add Alive
                airtable_executor.submit(
                    AirtableManager.remove_profile_status,
                    profile_number,
                    'Follow Block'
                )
                airtable_executor.submit(
                    AirtableManager.update_profile_status,
                    profile_number,
                    'Alive'
                )
            else:
                logger.warning(f"Profile {profile_id}: No profile_number found, skipping Airtable update")

            return True

        except Exception as e:
            logger.error(f"Error reviving profile {profile_id}: {e}")
            return False


class AirtableManager:
    """Manages Airtable operations"""
    
    @staticmethod
    def sync_profiles_from_airtable():
        """Sync profile statuses from Airtable to local cache"""
        if not AIRTABLE_AVAILABLE:
            logger.warning("Airtable not available for sync")
            return False
            
        try:
            api = AirtableManager._get_api()
            table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)
            
            # Get all profiles from Airtable
            records = table.all()
            logger.info(f"Syncing {len(records)} profiles from Airtable...")
            
            updated_count = 0
            with profiles_lock:
                for record in records:
                    # Find profile by profile_number
                    profile_number = None
                    for field_name in ['Profile', 'Profile Number', 'Profile ID', 'ID A']:
                        if field_name in record['fields']:
                            profile_number = str(record['fields'][field_name])
                            break
                    
                    if not profile_number:
                        continue
                    
                    # Find matching profile in cache
                    found_profile = None
                    for pid, info in profiles.items():
                        if info.get('profile_number') == profile_number:
                            found_profile = pid
                            break
                    
                    if found_profile:
                        # Update airtable_status from Airtable (multi-select field)
                        airtable_status = record['fields'].get('Status', ['Alive'])
                        if not isinstance(airtable_status, list):
                            airtable_status = [airtable_status] if airtable_status else ['Alive']
                        
                        old_status = profiles[found_profile].get('airtable_status', ['Alive'])
                        profiles[found_profile]['airtable_status'] = airtable_status
                        
                        if old_status != airtable_status:
                            logger.info(f"Profile {found_profile} ({profile_number}): Updated airtable_status from '{old_status}' to '{airtable_status}'")
                            updated_count += 1
            
            logger.info(f"Sync completed: {updated_count} profiles updated from Airtable")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing profiles from Airtable: {e}")
            return False

    # Connection pooling
    _api_instance = None
    _last_request_time = 0
    _request_cooldown = 1.0  # 1 second between requests
    _request_lock = threading.Lock()

    @staticmethod
    def _get_api():
        """Get or create API instance"""
        if AirtableManager._api_instance is None and AIRTABLE_AVAILABLE:
            AirtableManager._api_instance = Api(AIRTABLE_PERSONAL_ACCESS_TOKEN)
        return AirtableManager._api_instance

    @staticmethod
    def _rate_limit():
        """Simple rate limiting"""
        with AirtableManager._request_lock:
            current_time = time.time()
            time_since_last = current_time - AirtableManager._last_request_time
            if time_since_last < AirtableManager._request_cooldown:
                time.sleep(AirtableManager._request_cooldown - time_since_last)
            AirtableManager._last_request_time = time.time()

    @staticmethod
    def update_profile_status(profile_number, status):
        """Update profile status in Airtable with retry logic - adds new status to existing ones (multi-select field)"""
        if not AIRTABLE_AVAILABLE:
            return False

        max_retries = 2  # Reduced retries
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                AirtableManager._rate_limit()

                api = AirtableManager._get_api()
                table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

                records = table.all(formula=f"{{Profile}} = {profile_number}")

                if records:
                    record_id = records[0]['id']
                    current_record = records[0]
                    
                    # Get current statuses (multi-select field)
                    current_statuses = current_record.get('fields', {}).get('Status', [])
                    
                    # Convert to list if it's not already
                    if not isinstance(current_statuses, list):
                        current_statuses = [current_statuses] if current_statuses else []
                    
                    # Add new status if it's not already present
                    if status not in current_statuses:
                        current_statuses.append(status)
                        logger.info(f"Profile {profile_number}: Adding new status '{status}' to existing statuses: {current_statuses}")
                    else:
                        logger.info(f"Profile {profile_number}: Status '{status}' already exists, keeping current statuses: {current_statuses}")
                    
                    update_data = {'Status': current_statuses}
                    result = table.update(record_id, update_data)
                    logger.info(f"✅ Updated profile {profile_number} statuses to: {current_statuses}")
                    return True
                else:
                    logger.warning(f"❌ Profile {profile_number} not found in Airtable")
                    return False

            except Exception as e:
                logger.error(f"❌ Error updating profile {profile_number} status: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return False

    @staticmethod
    def remove_profile_status(profile_number, status):
        """Remove a specific status from Airtable multi-select field"""
        if not AIRTABLE_AVAILABLE:
            return False

        max_retries = 2
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                AirtableManager._rate_limit()

                api = AirtableManager._get_api()
                table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

                records = table.all(formula=f"{{Profile}} = {profile_number}")

                if records:
                    record_id = records[0]['id']
                    current_record = records[0]
                    
                    # Get current statuses (multi-select field)
                    current_statuses = current_record.get('fields', {}).get('Status', [])
                    
                    # Convert to list if it's not already
                    if not isinstance(current_statuses, list):
                        current_statuses = [current_statuses] if current_statuses else []
                    
                    # Remove status if it exists
                    if status in current_statuses:
                        current_statuses.remove(status)
                        logger.info(f"Profile {profile_number}: Removed status '{status}', remaining statuses: {current_statuses}")
                    else:
                        logger.info(f"Profile {profile_number}: Status '{status}' not found in current statuses: {current_statuses}")
                        return True  # Status not present, consider it successful
                    
                    update_data = {'Status': current_statuses}
                    result = table.update(record_id, update_data)
                    logger.info(f"✅ Removed status '{status}' from profile {profile_number}, remaining statuses: {current_statuses}")
                    return True
                else:
                    logger.warning(f"❌ Profile {profile_number} not found in Airtable")
                    return False

            except Exception as e:
                logger.error(f"❌ Error removing status '{status}' from profile {profile_number}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return False

    @staticmethod
    def update_reached_follow_limit(profile_number, date_blocked=None):
        """Update 'Reached Follow Limit' field in Airtable with the date when bot was blocked"""
        if not AIRTABLE_AVAILABLE:
            return False

        if date_blocked is None:
            date_blocked = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        max_retries = 2
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                AirtableManager._rate_limit()

                api = AirtableManager._get_api()
                table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

                records = table.all(formula=f"{{Profile}} = {profile_number}")

                if records:
                    record_id = records[0]['id']
                    update_data = {'Reached Follow Limit': date_blocked}
                    result = table.update(record_id, update_data)
                    logger.info(f"✅ Updated profile {profile_number} 'Reached Follow Limit' to '{date_blocked}' in Airtable")
                    return True
                else:
                    logger.warning(f"❌ Profile {profile_number} not found in Airtable for 'Reached Follow Limit' update")
                    return False

            except Exception as e:
                logger.error(f"❌ Error updating profile {profile_number} 'Reached Follow Limit': {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return False

    @staticmethod
    def mark_profile_logged_in(profile_number):
        """Add 'Logged In ⭐️' status and clean up old problematic statuses when account is working normally"""
        if not AIRTABLE_AVAILABLE:
            return False

        max_retries = 2
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                AirtableManager._rate_limit()

                api = AirtableManager._get_api()
                table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

                records = table.all(formula=f"{{Profile}} = {profile_number}")

                if records:
                    record_id = records[0]['id']
                    current_record = records[0]
                    
                    # Get current statuses (multi-select field)
                    current_statuses = current_record.get('fields', {}).get('Status', [])
                    
                    # Convert to list if it's not already
                    if not isinstance(current_statuses, list):
                        current_statuses = [current_statuses] if current_statuses else []
                    
                    # Remove old problematic statuses when account is working normally
                    statuses_to_remove = [
                        'Follow Block',
                        'Waiting for Appeal',
                        'Suspended',
                        'Banned 🔴',
                        "Can't Access",
                        'Something went wrong Checkpoint',
                        'Wrong Password',
                        'Bad Proxy'
                    ]
                    
                    removed_statuses = []
                    for status in statuses_to_remove:
                        if status in current_statuses:
                            current_statuses.remove(status)
                            removed_statuses.append(status)
                            logger.info(f"Profile {profile_number}: Removed old status '{status}' - account is working normally")
                    
                    if removed_statuses:
                        logger.info(f"Profile {profile_number}: Cleaned up old statuses: {removed_statuses}")
                    
                    # Add 'Logged In ⭐️' and 'Alive' if not already present (without removing other statuses)
                    statuses_to_add = ['Logged In ⭐️', 'Alive']
                    added_statuses = []
                    
                    for status in statuses_to_add:
                        if status not in current_statuses:
                            current_statuses.append(status)
                            added_statuses.append(status)
                    
                    if added_statuses:
                        logger.info(f"Profile {profile_number}: Added statuses {added_statuses} to existing statuses: {current_statuses}")
                    else:
                        logger.info(f"Profile {profile_number}: All required statuses already exist, keeping current statuses: {current_statuses}")
                    
                    # Update Airtable
                    update_data = {'Status': current_statuses}
                    result = table.update(record_id, update_data)
                    logger.info(f"✅ Profile {profile_number} updated with statuses: {current_statuses}")
                    return True
                else:
                    logger.warning(f"❌ Profile {profile_number} not found in Airtable")
                    return False

            except Exception as e:
                logger.error(f"❌ Error marking profile {profile_number} as logged in: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return False

    @staticmethod
    def clean_up_old_statuses(profile_number):
        """Remove old problematic statuses when account is working normally"""
        if not AIRTABLE_AVAILABLE:
            return False

        max_retries = 2
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                AirtableManager._rate_limit()

                api = AirtableManager._get_api()
                table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

                records = table.all(formula=f"{{Profile}} = {profile_number}")

                if records:
                    record_id = records[0]['id']
                    current_record = records[0]
                    
                    # Get current statuses (multi-select field)
                    current_statuses = current_record.get('fields', {}).get('Status', [])
                    
                    # Convert to list if it's not already
                    if not isinstance(current_statuses, list):
                        current_statuses = [current_statuses] if current_statuses else []
                    
                    # Statuses to remove when account is working normally
                    statuses_to_remove = [
                        'Waiting for Appeal',
                        'Suspended',
                        'Banned 🔴',
                        "Can't Access",
                        'Something went wrong Checkpoint',
                        'Wrong Password',
                        'Bad Proxy'
                    ]
                    
                    # Remove old problematic statuses
                    removed_statuses = []
                    for status in statuses_to_remove:
                        if status in current_statuses:
                            current_statuses.remove(status)
                            removed_statuses.append(status)
                            logger.info(f"Profile {profile_number}: Removed old status '{status}' - account is working normally")
                    
                    if removed_statuses:
                        # Update Airtable
                        update_data = {'Status': current_statuses}
                        result = table.update(record_id, update_data)
                        logger.info(f"✅ Profile {profile_number} cleaned up old statuses. Removed: {removed_statuses}, remaining: {current_statuses}")
                        return True
                    else:
                        logger.info(f"Profile {profile_number}: No old statuses to remove")
                        return True
                else:
                    logger.warning(f"❌ Profile {profile_number} not found in Airtable")
                    return False

            except Exception as e:
                logger.error(f"❌ Error cleaning up old statuses for profile {profile_number}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return False

    @staticmethod
    def update_total_follows(profile_number, additional_follows):
        """Update total follows count in Airtable"""
        if not AIRTABLE_AVAILABLE:
            return False

        max_retries = 2
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                AirtableManager._rate_limit()

                api = AirtableManager._get_api()
                table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

                records = table.all(formula=f"{{Profile}} = {profile_number}")

                if records:
                    record_id = records[0]['id']
                    current_record = records[0]
                    
                    # Get current total follows count
                    current_total = current_record.get('fields', {}).get('Total Follows', 0)
                    if not isinstance(current_total, (int, float)):
                        current_total = 0
                    
                    # Add new follows to current total
                    new_total = current_total + additional_follows
                    
                    # Update Airtable
                    update_data = {'Total Follows': new_total}
                    result = table.update(record_id, update_data)
                    logger.info(f"✅ Profile {profile_number}: Updated total follows from {current_total} to {new_total} (+{additional_follows})")
                    return True
                else:
                    logger.warning(f"❌ Profile {profile_number} not found in Airtable")
                    return False

            except Exception as e:
                logger.error(f"❌ Error updating total follows for profile {profile_number}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return False

    @staticmethod
    def update_profile_statistics(profile_number, last_run=None, follows_today=None, total_follows=None):
        """Update profile statistics in Airtable"""
        if not AIRTABLE_AVAILABLE:
            return False

        # Note: Statistics fields (Last run, Follows today, Total Follows) don't exist in Airtable schema
        # This function is kept for compatibility but doesn't perform any updates
        logger.info(f"Profile {profile_number}: Statistics update skipped (fields don't exist in Airtable schema)")
        return True

    @staticmethod
    def update_profile_statistics_on_completion(profile_id):
        """Update statistics for a profile when it completes work"""
        try:
            stats = StatsManager.get_profile_stats(profile_id)
            
            # Get profile_number for Airtable update
            pid_str = str(profile_id)
            profile_number = None
            with profiles_lock:
                if pid_str in profiles:
                    profile_number = profiles[pid_str].get('profile_number')
            
            if not profile_number:
                logger.warning(f"Profile {profile_id}: No profile_number found, skipping Airtable statistics update")
                return False
                
            success = AirtableManager.update_profile_statistics(
                profile_number,
                last_run=stats['last_run'],
                follows_today=stats['today'],
                total_follows=stats['total_all_time']
            )
            if success:
                logger.info(f"✅ Profile {profile_id} statistics updated in Airtable")
            return success
        except Exception as e:
            logger.error(f"❌ Error updating profile {profile_id} statistics: {e}")
            return False

    @staticmethod
    def load_profiles():
        """Load profile numbers and usernames from Airtable - OPTIMIZED VERSION"""
        if not AIRTABLE_AVAILABLE:
            logger.error("pyairtable library not available")
            return []

        try:
            api = AirtableManager._get_api()
            table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)
            linked_table = api.table(AIRTABLE_BASE_ID, AIRTABLE_LINKED_TABLE_ID)

            logger.info(f"Fetching profiles from Airtable view {AIRTABLE_VIEW_ID}...")
            
            # Use Airtable's built-in pagination - it handles this automatically
            logger.info("Using Airtable's built-in pagination...")
            AirtableManager._rate_limit()
            all_records = table.all(view=AIRTABLE_VIEW_ID)
            logger.info(f"Total records fetched: {len(all_records)}")

            # First pass: collect all AdsPower IDs and Assigned IG records for batch querying
            adspower_ids_to_query = []
            assigned_ig_ids_to_query = []
            records_with_data = []
            
            profile_field_names = ['Profile', 'Profile Number', 'AdsPower Profile', 'Profile ID', 'ID A']
            
            for record in all_records:
                record_data = {'record': record}
                
                # Find profile number
                for field_name in profile_field_names:
                    if field_name in record['fields']:
                        record_data['profile_number'] = record['fields'][field_name]
                        break
                
                # Get Status field from Airtable (multi-select field)
                airtable_status = record['fields'].get('Status', 'Alive')
                if isinstance(airtable_status, list):
                    # Keep the full list of statuses for proper handling
                    record_data['airtable_status'] = airtable_status
                else:
                    # Convert single status to list for consistency
                    record_data['airtable_status'] = [airtable_status] if airtable_status else ['Alive']
                
                # Get AdsPower ID if present
                if 'AdsPower ID' in record['fields']:
                    adspower_id = record['fields']['AdsPower ID']
                    record_data['adspower_id'] = adspower_id
                    if adspower_id and record_data.get('profile_number'):
                        adspower_ids_to_query.append(adspower_id)
                
                # Get Assigned IG record IDs if present
                if 'Assigned IG' in record['fields']:
                    assigned_ig_records = record['fields']['Assigned IG']
                    if isinstance(assigned_ig_records, list) and len(assigned_ig_records) > 0:
                        # Usually just one record, but could be multiple
                        record_data['assigned_ig_ids'] = assigned_ig_records
                        assigned_ig_ids_to_query.extend(assigned_ig_records)
                
                if record_data.get('profile_number'):
                    records_with_data.append(record_data)
            
            # Query AdsPower names for all profiles
            if adspower_ids_to_query:
                logger.info(f"Querying {len(adspower_ids_to_query)} AdsPower profile names...")
                logger.info(f"AdsPower IDs to query: {adspower_ids_to_query}")
                adspower_names = batch_query_adspower_profiles(adspower_ids_to_query)
                logger.info(f"Got {len(adspower_names)} AdsPower profile names: {adspower_names}")
            else:
                adspower_names = {}
            
            # Fetch Assigned IG records if any
            assigned_ig_data = {}
            if assigned_ig_ids_to_query:
                logger.info(f"Fetching {len(assigned_ig_ids_to_query)} Assigned IG records...")
                
                # Create directory for followers files
                import os
                followers_dir = os.path.join(os.getcwd(), 'assigned_followers')
                if not os.path.exists(followers_dir):
                    os.makedirs(followers_dir)
                
                # Function to fetch and download a single record
                def fetch_and_download_record(record_id):
                    try:
                        linked_record = linked_table.get(record_id)
                        if linked_record:
                            # Download Filtered Followers file if present
                            if 'Filtered Followers' in linked_record['fields']:
                                attachments = linked_record['fields']['Filtered Followers']
                                if isinstance(attachments, list) and len(attachments) > 0:
                                    # Usually just one attachment
                                    attachment = attachments[0]
                                    file_url = attachment.get('url')
                                    filename = attachment.get('filename', f'followers_{record_id}.txt')
                                    
                                    if file_url:
                                        filepath = os.path.join(followers_dir, f'{record_id}_{filename}')
                                        
                                        # Check if file already exists
                                        if os.path.exists(filepath):
                                            logger.info(f"Followers file already exists for {record_id}: {filename}")
                                            linked_record['followers_file'] = filepath
                                        else:
                                            # Download file
                                            response = requests.get(file_url, timeout=30)
                                            if response.status_code == 200:
                                                with open(filepath, 'w', encoding='utf-8') as f:
                                                    f.write(response.text)
                                                logger.info(f"Downloaded followers file for {record_id}: {filename}")
                                                
                                                # Store the filepath in the data
                                                linked_record['followers_file'] = filepath
                                            else:
                                                logger.error(f"Failed to download followers file for {record_id}")
                            
                            return (record_id, linked_record)
                    except Exception as e:
                        logger.error(f"Error fetching linked record {record_id}: {e}")
                        return None
                
                # Use ThreadPoolExecutor to fetch records in parallel
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=10) as executor:
                    # Submit all download tasks
                    future_to_record_id = {executor.submit(fetch_and_download_record, record_id): record_id 
                                         for record_id in assigned_ig_ids_to_query}
                    
                    # Collect results as they complete
                    for future in as_completed(future_to_record_id):
                        result = future.result()
                        if result:
                            record_id, linked_record = result
                            assigned_ig_data[record_id] = linked_record
            
            # Second pass: build profile data with AdsPower names and assigned IG data
            profile_data_list = []
            
            for record_data in records_with_data:
                record = record_data['record']
                profile_number = record_data['profile_number']
                username = None
                adspower_name = None
                airtable_status = None
                vps_status = None
                phase = None
                batch = None

                if 'Username' in record['fields']:
                    username = record['fields']['Username']
                
                # Get AdsPower name and serial number from batch results
                adspower_id = record_data.get('adspower_id')
                adspower_serial = None
                if adspower_id and str(adspower_id) in adspower_names:
                    profile_info = adspower_names[str(adspower_id)]
                    adspower_name = profile_info.get('name')
                    adspower_serial = profile_info.get('serial_number')
                elif not adspower_id and profile_number:
                    # For profiles without AdsPower ID, use profile_number as serial
                    adspower_serial = str(profile_number)
                    adspower_name = f"Melina {profile_number}"

                if 'Status' in record['fields']:
                    airtable_status = record['fields']['Status']

                if 'VPS' in record['fields']:
                    vps_status = record['fields']['VPS']

                if 'Phase' in record['fields']:
                    phase = record['fields']['Phase']

                if 'Batch' in record['fields']:
                    batch = record['fields']['Batch']

                if profile_number:  # Include all profiles with profile numbers
                    # Get assigned IG data if available
                    assigned_followers_file = None
                    assigned_ig_ids = record_data.get('assigned_ig_ids', [])
                    if assigned_ig_ids:
                        # Usually just use the first one
                        first_assigned_ig = assigned_ig_ids[0]
                        if first_assigned_ig in assigned_ig_data:
                            linked_data = assigned_ig_data[first_assigned_ig]
                            assigned_followers_file = linked_data.get('followers_file')
                    
                    # Use AdsPower ID as the primary ID
                    # Use profile_number as primary key if no AdsPower ID
                    primary_id = str(adspower_id) if adspower_id else f"profile_{profile_number}"
                    
                    profile_data = {
                        'id': primary_id,  # Use AdsPower ID or profile_number as primary key
                        'profile_number': str(profile_number),
                        'username': username or 'Unknown',
                        'adspower_name': adspower_name or f"Melina {profile_number}",
                        'adspower_id': adspower_id,
                        'adspower_serial': adspower_serial or str(profile_number),
                        'airtable_status': airtable_status or 'Alive',
                        'vps_status': vps_status or 'None',
                        'phase': phase or 'None',
                        'batch': batch or 'None',
                        'assigned_followers_file': assigned_followers_file
                    }
                    profile_data_list.append(profile_data)

            logger.info(f"Loaded {len(profile_data_list)} profiles from Airtable")
            return profile_data_list

        except Exception as e:
            logger.error(f"Error loading profiles from Airtable: {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    @staticmethod
    def get_vps_options():
        """Get all unique VPS options from profiles"""
        vps_options = set()
        with profiles_lock:
            for pid, info in profiles.items():
                vps = info.get('vps_status', 'None')
                if vps and vps != 'None':
                    vps_options.add(vps)
        return sorted(list(vps_options))

    @staticmethod
    def get_phase_options():
        """Get all unique Phase options from profiles"""
        phase_options = set()
        with profiles_lock:
            for pid, info in profiles.items():
                phase = info.get('phase', 'None')
                if phase and phase != 'None':
                    phase_options.add(phase)
        return sorted(list(phase_options))

    @staticmethod
    def get_batch_options():
        """Get all unique Batch options from profiles"""
        batch_options = set()
        with profiles_lock:
            for pid, info in profiles.items():
                batch = info.get('batch', 'None')
                if batch and batch != 'None':
                    batch_options.add(batch)
        return sorted(list(batch_options))


class ConcurrencyManager:
    """Manages concurrent profile execution"""

    @staticmethod
    def enable_auto_replacement():
        """Enable automatic profile replacement (called after Start All)"""
        global auto_replacement_enabled
        with auto_replacement_lock:
            auto_replacement_enabled = True
            logger.info("Auto replacement enabled - system will maintain 70 active profiles")

    @staticmethod
    def disable_auto_replacement():
        """Disable automatic profile replacement"""
        global auto_replacement_enabled
        with auto_replacement_lock:
            auto_replacement_enabled = False
            logger.info("Auto replacement disabled")

    @staticmethod
    def is_auto_replacement_enabled():
        """Check if automatic replacement is enabled"""
        global auto_replacement_enabled
        with auto_replacement_lock:
            return auto_replacement_enabled

    @staticmethod
    def get_active_profiles_count():
        """Get number of currently Running profiles (should never exceed 50) - with caching"""
        global active_profiles_count, stats_cache
        import time
        
        current_time = time.time()
        
        # Check cache first (valid for 2 seconds)
        with stats_cache_lock:
            if current_time - stats_cache['last_update'] < 2:
                return stats_cache['active_count']
        
        # Use a single lock for better performance
        with profiles_lock:
            count = 0
            for pid, info in profiles.items():
                # Count only profiles that are Running
                if info['status'] == 'Running':
                    count += 1
            active_profiles_count = count
            
            # Update cache
            with stats_cache_lock:
                stats_cache['active_count'] = count
                stats_cache['last_update'] = current_time
            
            return count

    @staticmethod
    def get_pending_profiles_count():
        """Get number of currently Queueing profiles (since Running is limited to 50) - with caching"""
        global stats_cache
        import time
        
        current_time = time.time()
        
        # Check cache first (valid for 2 seconds)
        with stats_cache_lock:
            if current_time - stats_cache['last_update'] < 2:
                return stats_cache['pending_count']
        
        # Use a single lock for better performance
        with profiles_lock:
            count = 0
            for pid, info in profiles.items():
                # Count only profiles that are Queueing
                if info['status'] == 'Queueing':
                    count += 1
            
            # Update cache
            with stats_cache_lock:
                stats_cache['pending_count'] = count
                stats_cache['last_update'] = current_time
            
            return count

    @staticmethod
    def get_total_working_profiles_count():
        """Get total number of profiles that are Running or Queueing (for concurrent limit)"""
        # Use only profiles_lock for better performance
        with profiles_lock:
            count = 0
            for pid, info in profiles.items():
                # Count profiles that are Running or Queueing (for concurrent limit)
                if info['status'] in ['Running', 'Queueing']:
                    count += 1
            return count

    @staticmethod
    def can_start_new_profile():
        """Check if we can start a new profile (only Running count matters for the limit)"""
        # Count only Running profiles for the 70 limit - use single lock
        with profiles_lock:
            running_count = 0
            for pid, info in profiles.items():
                if info['status'] == 'Running':
                    running_count += 1
            return running_count < MAX_CONCURRENT_PROFILES

    @staticmethod
    def add_to_pending_queue(profile_id):
        """Add a profile to the pending queue"""
        global pending_profiles_queue
        with concurrent_lock:
            if profile_id not in pending_profiles_queue:
                pending_profiles_queue.append(profile_id)
                logger.info(f"Profile {profile_id} added to pending queue. Queue length: {len(pending_profiles_queue)}")

    @staticmethod
    def start_next_pending_profile():
        """Start the next profile from the pending queue if possible"""
        global pending_profiles_queue
        with concurrent_lock:
            if not pending_profiles_queue:
                return False

        # Get counts outside of concurrent_lock to avoid nested locking
        active_count = ConcurrencyManager.get_active_profiles_count()
        pending_count = ConcurrencyManager.get_pending_profiles_count()
        total_working = ConcurrencyManager.get_total_working_profiles_count()
        logger.info(
            f"Checking pending queue. Active: {active_count}, Pending: {pending_count}, Total working: {total_working}/{MAX_CONCURRENT_PROFILES}, Pending queue: {len(pending_profiles_queue)}")

        if ConcurrencyManager.can_start_new_profile():
            with concurrent_lock:
                if pending_profiles_queue:  # Double-check queue is not empty
                    next_profile = pending_profiles_queue.pop(0)
                    logger.info(f"Starting pending profile: {next_profile}")

                    # Submit to executor instead of direct call
                    profile_executor.submit(ProfileRunner.start_profile_internal, next_profile)
                    return True
        else:
            logger.info(f"Cannot start pending profile - max concurrent limit reached")
        return False

    @staticmethod
    def maintain_active_profile_limit():
        """Maintain the active profile limit by starting new profiles when others finish/block"""
        try:
            # Check if auto replacement is enabled
            if not ConcurrencyManager.is_auto_replacement_enabled():
                return
            
            # Get current counts
            running_count = 0
            queueing_count = 0
            with profiles_lock:
                for pid, info in profiles.items():
                    if info['status'] == 'Running':
                        running_count += 1
                    elif info['status'] == 'Queueing':
                        queueing_count += 1
            
            total_working = running_count + queueing_count
            
            # If we have less than 70 working profiles, try to start more
            if total_working < MAX_CONCURRENT_PROFILES:
                available_slots = MAX_CONCURRENT_PROFILES - total_working
                logger.info(f"Maintaining limit: Running={running_count}, Queueing={queueing_count}, Available slots={available_slots}")
                
                # Find profiles that can be started
                startable_profiles = []
                with profiles_lock:
                    for pid, info in profiles.items():
                        # Only start profiles that are not running, queueing, or blocked/suspended
                        if info['status'] in ['Not Running', 'Finished', 'Stopped']:
                            # Check Airtable status
                            airtable_status = info.get('airtable_status', 'Alive')
                            if isinstance(airtable_status, list):
                                airtable_status = airtable_status[0] if airtable_status else 'Alive'
                            
                            # Only start if Airtable status is Alive or Logged In
                            if airtable_status in ['Alive', 'Logged In ⭐️']:
                                startable_profiles.append(pid)
                
                # Start up to available_slots profiles
                started_count = 0
                for pid in startable_profiles[:available_slots]:
                    if ConcurrencyManager.can_start_new_profile():
                        logger.info(f"Auto-starting profile {pid} to maintain limit")
                        ProfileController.start_profile(pid)
                        started_count += 1
                        time.sleep(0.5)  # Small delay between starts
                
                if started_count > 0:
                    logger.info(f"Auto-started {started_count} profiles to maintain limit")
                    
        except Exception as e:
            logger.error(f"Error in maintain_active_profile_limit: {e}")

    @staticmethod
    def monitor_and_start_pending():
        """Background thread to monitor and start pending profiles"""
        while True:
            try:
                # Check every second
                time.sleep(1)

                # No cache updates needed - we read directly from sources

                # Check for pending profiles
                if pending_profiles_queue:
                    ConcurrencyManager.start_next_pending_profile()

                # Check if we need to start more profiles to maintain the limit
                ConcurrencyManager.maintain_active_profile_limit()

                # Clean up stuck profiles
                cleanup_finished_profiles()

            except Exception as e:
                logger.error(f"Error in monitor thread: {e}")
                time.sleep(2)


class ProfileRunner:
    """Handles profile execution"""

    @staticmethod
    def profile_runner(pid, max_follows):
        """Main profile runner logic"""
        from instagram_bot import InstagramFollowBot

        key = str(pid)
        
        # Check if this profile was blocked before starting
        # Check both persistent status and Airtable status
        persistent_status = StatusManager.get_persistent_status(pid)
        airtable_status = None
        with profiles_lock:
            if key in profiles:
                airtable_status = profiles[key].get('airtable_status', ['Alive'])
        
        # Ensure airtable_status is a list for proper multi-select handling
        if not isinstance(airtable_status, list):
            airtable_status = [airtable_status] if airtable_status else ['Alive']
        
        # Check if profile is blocked based on Airtable statuses
        is_blocked_by_airtable = 'Follow Block' in airtable_status or 'Suspended' in airtable_status
        was_blocked = persistent_status == 'blocked' or is_blocked_by_airtable
        is_test_mode = max_follows == 1
        
        logger.info(f"Profile {pid} starting: was_blocked={was_blocked}, is_test_mode={is_test_mode}, persistent_status={persistent_status}, airtable_status={airtable_status}")
        
        # Log username queue status for debugging
        remaining_usernames = UsernameManager.get_remaining_count()
        logger.info(f"Profile {pid}: Username queue has {remaining_usernames} usernames available")

        # Update status
        with profiles_lock:
            if key not in profiles:
                return
            profiles[key]['status'] = 'Running'
            profiles[key]['stop_requested'] = False

        # Update active count when status changes to Running
        ConcurrencyManager.get_active_profiles_count()
        
        StatsManager.reset_last_run_count(pid)

        # Get configuration
        config = ConfigManager.load_config() or {}
        delay_config = config.get('delays', {})
        limits_config = config.get('limits', {})

        # Extract settings
        between_follows = delay_config.get('between_follows', [8, 20])
        pre_action_delay = delay_config.get('pre_action_delay', [2, 8])
        extended_break_interval = delay_config.get('extended_break_interval', [5, 10])
        extended_break_duration = delay_config.get('extended_break_duration', [60, 120])
        very_long_break_chance = delay_config.get('very_long_break_chance', 0.03)
        very_long_break_duration = delay_config.get('very_long_break_duration', [300, 600])
        hourly_reset_break = delay_config.get('hourly_reset_break', [600, 1200])
        max_follows_per_hour = limits_config.get('max_follows_per_hour', 35)

        # Get the AdsPower serial number for this profile
        adspower_serial = None
        with profiles_lock:
            if key in profiles:
                adspower_serial = profiles[key].get('adspower_serial')
        
        # Use AdsPower serial number if available, otherwise fall back to pid
        bot_profile_id = adspower_serial if adspower_serial else pid
        bot = InstagramFollowBot(profile_id=bot_profile_id)
        
        # Set test mode flag for the bot
        if max_follows == 1:
            bot.is_test_mode = True
            logger.info(f"Profile {pid}: Set bot test mode flag to True")

        with profiles_lock:
            profiles[key]['bot'] = bot

        try:
            # Initialize bot
            if not bot.start_profile():
                with profiles_lock:
                    profiles[key]['status'] = 'Error'
                return

            if not bot.connect_to_browser():
                with profiles_lock:
                    profiles[key]['status'] = 'Error'
                bot.stop_profile()
                return

            # Close extra tabs immediately after connecting to reduce RAM usage
            bot.close_extra_tabs()

            if not bot.navigate_to_instagram():
                if hasattr(bot, 'is_suspended') and bot.is_suspended:
                    logger.error(f"Profile {pid}: SUSPENDED")
                    with profiles_lock:
                        profiles[key]['status'] = 'Suspended'
                    StatusManager.mark_profile_suspended(pid)
                else:
                    with profiles_lock:
                        profiles[key]['status'] = 'Error'
                bot.stop_profile()
                return

            # Check suspension
            if bot.check_if_suspended():
                logger.error(f"Profile {pid}: SUSPENDED")
                with profiles_lock:
                    profiles[key]['status'] = 'Suspended'
                StatusManager.mark_profile_suspended(pid)
                bot.stop_profile()
                return

            # Profile successfully started and navigated to Instagram - add logged in status
            profile_number = None
            with profiles_lock:
                if key in profiles:
                    profile_number = profiles[key].get('profile_number')
            
            if profile_number:
                # Add 'Logged In ⭐️' status for normal runs or after successful tests
                if max_follows > 1:  # Normal run
                    airtable_executor.submit(
                        AirtableManager.mark_profile_logged_in,
                        profile_number
                    )
                    logger.info(f"Profile {pid}: Added 'Logged In ⭐️' status to Airtable (normal startup)")
                elif max_follows == 1:
                    logger.info(f"Profile {pid}: Test mode - will add 'Logged In ⭐️' status after successful test completion")

            # Follow loop
            follows_this_hour = 0
            hour_start_time = time.time()

            logger.info(f"Profile {pid}: Starting follow loop with max_follows={max_follows}")
            if max_follows == 1:
                logger.info(f"Profile {pid}: TEST MODE - Starting follow loop to test for blocks")
            
            for i in range(max_follows):
                logger.info(f"Profile {pid}: Follow loop iteration {i + 1}/{max_follows}")
                
                # For test mode, log that we're about to attempt follow
                if max_follows == 1:
                    logger.info(f"Profile {pid}: TEST MODE - About to attempt follow to check for blocks")
                # Check stop request
                stop_requested = False
                with profiles_lock:
                    stop_requested = profiles[key].get('stop_requested', False)

                if stop_requested:
                    with profiles_lock:
                        profiles[key]['status'] = 'Stopped'
                    break

                # Check hourly limits
                current_time = time.time()
                if current_time - hour_start_time >= 3600:
                    follows_this_hour = 0
                    hour_start_time = current_time

                if follows_this_hour >= max_follows_per_hour:
                    break_duration = random.uniform(hourly_reset_break[0], hourly_reset_break[1])
                    time.sleep(break_duration)
                    follows_this_hour = 0
                    hour_start_time = time.time()

                # Get username - prefer profile-specific, fallback to shared
                username = ProfileSpecificUsernameManager.get_next_username_for_profile(key)
                if not username:
                    # Fallback to shared username pool
                    username = UsernameManager.get_next_username()
                    if not username:
                        # For test mode, try to get a real username from the file
                        if max_follows == 1:  # Test mode
                            try:
                                # Try to get a real username from the file for testing
                                with open('usernames.txt', 'r', encoding='utf-8') as f:
                                    lines = f.readlines()
                                    if lines:
                                        username = lines[0].strip()
                                        logger.info(f"Profile {pid}: Using real username from file for test: {username}")
                                    else:
                                        username = "instagram"  # Fallback to a known existing account
                                        logger.info(f"Profile {pid}: Using fallback username for test: {username}")
                            except:
                                username = "instagram"  # Fallback to a known existing account
                                logger.info(f"Profile {pid}: Using fallback username for test: {username}")
                        else:
                            logger.warning(f"Profile {pid}: No usernames available, finishing profile")
                            with profiles_lock:
                                profiles[key]['status'] = 'Finished'
                            break
                    else:
                        logger.info(f"Profile {pid}: Got username from shared pool: {username}")
                else:
                    logger.info(f"Profile {pid}: Got username from profile-specific pool: {username}")

                # Pre-action pause
                pause = random.uniform(pre_action_delay[0], pre_action_delay[1])
                time.sleep(pause)

                # Follow user
                if max_follows == 1:  # Test mode
                    logger.info(f"Profile {pid}: TEST MODE - Attempting to follow {username} to check for blocks")
                    logger.info(f"Profile {pid}: TEST MODE - Will navigate to https://www.instagram.com/{username}/")
                else:
                    logger.info(f"Profile {pid}: Following {username} ({i + 1}/{max_follows})")
                
                logger.info(f"Profile {pid}: Calling bot.follow_user() with username: {username}")
                success = bot.follow_user(username, fast_mode=False, delay_config=delay_config)
                logger.info(f"Profile {pid}: bot.follow_user() returned: {success}")
                
                # For test mode, log additional info
                if max_follows == 1:
                    logger.info(f"Profile {pid}: TEST MODE - Follow attempt completed, success: {success}")

                if success:
                    StatsManager.increment_follow_count(pid)
                    follows_this_hour += 1

                # Check for error pages (Something went wrong, Aw Snap, Take a quick pause)
                if not success and hasattr(bot, 'something_went_wrong') and bot.something_went_wrong:
                    logger.error(f"Profile {pid}: Error page detected - 'Something went wrong', 'Aw, Snap!', or 'Take a quick pause'")
                    
                    # Update local profile status to Blocked
                    with profiles_lock:
                        if key in profiles:
                            profiles[key]['status'] = 'Blocked'
                            profiles[key]['stop_requested'] = True
                            # The airtable_status will be set by the specific error handler
                            logger.info(f"Profile {pid}: Updated local status to Blocked due to error page")
                    
                    # Stop the profile
                    bot.stop_profile()
                    logger.info(f"Profile {pid}: Stopped profile due to error page")
                    return

                # Check blocks
                if bot.is_follow_blocked:
                    with profiles_lock:
                        profiles[key]['status'] = 'Blocked'
                    StatusManager.mark_profile_blocked(pid)
                    break

                if bot.is_suspended:
                    with profiles_lock:
                        profiles[key]['status'] = 'Suspended'
                    StatusManager.mark_profile_suspended(pid)
                    break

                # Delays
                delay = random.uniform(between_follows[0], between_follows[1])
                time.sleep(delay)

                # Extended breaks
                if i > 0 and i % random.randint(extended_break_interval[0], extended_break_interval[1]) == 0:
                    long_break = random.uniform(extended_break_duration[0], extended_break_duration[1])
                    time.sleep(long_break)

                # Very long breaks
                if random.random() < very_long_break_chance:
                    very_long = random.uniform(very_long_break_duration[0], very_long_break_duration[1])
                    time.sleep(very_long)

        except Exception as e:
            logger.error(f"Profile {pid} error: {e}")
            with profiles_lock:
                # Only set to Error if not already Blocked or Suspended
                current_status = profiles[key].get('status', '')
                if current_status not in ['Blocked', 'Suspended']:
                    profiles[key]['status'] = 'Error'
                    logger.info(f"Profile {pid}: Set status to Error due to exception")
                else:
                    logger.info(f"Profile {pid}: Keeping status as {current_status} despite exception")
        finally:
            # Cleanup
            bot.stop_profile()

            # Check if this was a successful test of a previously blocked profile
            final_status = None
            bot_was_blocked = bot.is_follow_blocked if bot else False
            
            with profiles_lock:
                final_status = profiles[key]['status']
                
                # If bot was blocked but status is not Blocked, update it
                if bot_was_blocked and final_status != 'Blocked':
                    profiles[key]['status'] = 'Blocked'
                    profiles[key]['airtable_status'] = 'Follow Block'
                    final_status = 'Blocked'
                    logger.info(f"Profile {pid}: Updated status to Blocked because bot was blocked")
                    
                    # Also update Airtable with 'Reached Follow Limit' field
                    profile_number = profiles[key].get('profile_number')
                    if profile_number:
                        airtable_executor.submit(
                            AirtableManager.update_profile_status,
                            profile_number,
                            'Follow Block'
                        )
                        airtable_executor.submit(
                            AirtableManager.update_reached_follow_limit,
                            profile_number,
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        )
                        logger.info(f"Profile {pid}: Updated Airtable with Follow Block and Reached Follow Limit")
                elif profiles[key]['status'] == 'Running':
                    profiles[key]['status'] = 'Finished'
                    
                profiles[key]['bot'] = None

            logger.info(f"Profile {pid} finished: is_test_mode={is_test_mode}, was_blocked={was_blocked}, bot_was_blocked={bot_was_blocked}, final_status={final_status}")

            # If this was a test mode and the profile was previously blocked but completed successfully
            # Check both the final status AND the bot's internal follow block flag
            if is_test_mode and was_blocked:
                logger.info(f"🔍 TEST MODE for previously blocked profile {pid}: bot_was_blocked={bot_was_blocked}, final_status={final_status}")
                if not bot_was_blocked and final_status in ['Running', 'Finished', 'Testing', 'Not Running']:
                    logger.info(f"✅ TEST SUCCESSFUL for previously blocked profile {pid} - reviving profile")
                    StatusManager.revive_profile_status(pid)
                    
                    # Add 'Logged In ⭐️' status after successful test of previously blocked profile
                    profile_number = None
                    with profiles_lock:
                        if key in profiles:
                            profile_number = profiles[key].get('profile_number')
                    
                    if profile_number:
                        airtable_executor.submit(
                            AirtableManager.mark_profile_logged_in,
                            profile_number
                        )
                        logger.info(f"Profile {pid}: Added 'Logged In ⭐️' status after successful test of previously blocked profile")
                else:
                    logger.info(f"❌ TEST CONFIRMED profile {pid} is still blocked - keeping blocked status")
            elif is_test_mode:
                logger.info(f"🔍 TEST MODE for non-blocked profile {pid} - checking if profile is working correctly")
                if bot_was_blocked:
                    logger.info(f"⚠️ TEST DETECTED new block for profile {pid} - marking as blocked")
                else:
                    logger.info(f"✅ TEST SUCCESSFUL for profile {pid} - profile is working correctly")
                    # Add 'Logged In ⭐️' status after successful test
                    profile_number = None
                    with profiles_lock:
                        if key in profiles:
                            profile_number = profiles[key].get('profile_number')
                    
                    if profile_number:
                        airtable_executor.submit(
                            AirtableManager.mark_profile_logged_in,
                            profile_number
                        )
                        logger.info(f"Profile {pid}: Added 'Logged In ⭐️' status after successful test")

            # Update total follows count in Airtable
            profile_number = None
            with profiles_lock:
                if key in profiles:
                    profile_number = profiles[key].get('profile_number')
            
            if profile_number and bot:
                # Get successful follows count from bot
                successful_follows = 0
                if hasattr(bot, 'successful_follows_count'):
                    successful_follows = bot.successful_follows_count
                elif hasattr(bot, 'follow_results') and bot.follow_results:
                    successful_follows = len(bot.follow_results.get('successful', []))
                elif hasattr(bot, 'consecutive_follow_errors'):
                    # Estimate based on max_follows and errors
                    successful_follows = max(0, max_follows - bot.consecutive_follow_errors)
                
                if successful_follows > 0:
                    airtable_executor.submit(
                        AirtableManager.update_total_follows,
                        profile_number,
                        successful_follows
                    )
                    logger.info(f"Profile {pid}: Will update total follows with +{successful_follows} follows")

            # Update Airtable
            if profiles[key]['status'] in ['Finished', 'Stopped', 'Blocked', 'Suspended']:
                airtable_executor.submit(
                    AirtableManager.update_profile_statistics_on_completion,
                    pid
                )

    @staticmethod
    def profile_runner_wrapper(pid, max_follows):
        """Wrapper that handles cleanup"""
        try:
            ProfileRunner.profile_runner(pid, max_follows)
        finally:
            key = str(pid)
            with profiles_lock:
                if key in profiles:
                    if profiles[key]['status'] == 'Running':
                        profiles[key]['status'] = 'Finished'
                    profiles[key]['thread'] = None
                    profiles[key]['stop_requested'] = False

            # Decrement active count
            with concurrent_lock:
                global active_profiles_count
                active_profiles_count = max(0, active_profiles_count - 1)

            # Try to start next pending
            time.sleep(0.5)  # Small delay
            ConcurrencyManager.start_next_pending_profile()
            
            # Also try to maintain the active profile limit
            ConcurrencyManager.maintain_active_profile_limit()

    @staticmethod
    def start_profile_internal(pid):
        """Internal function to start a profile"""
        # First, try to find the profile by profile_number, adspower_serial, or adspower_id
        actual_profile_id = None
        with profiles_lock:
            # Try to find by profile_number first
            for key, info in profiles.items():
                if info.get('profile_number') == str(pid) or info.get('adspower_serial') == str(pid):
                    actual_profile_id = key
                    logger.info(f"Profile {pid} found in cache with key: {actual_profile_id}")
                    break
            
            # If not found by profile_number, try by adspower_id
            if not actual_profile_id and str(pid) in profiles:
                actual_profile_id = str(pid)
                logger.info(f"Profile {pid} found directly in cache")
            
            # If still not found, try to find by adspower_id in the data
            if not actual_profile_id:
                for key, info in profiles.items():
                    if info.get('adspower_id') == str(pid):
                        actual_profile_id = key
                        logger.info(f"Profile {pid} found by adspower_id with key: {actual_profile_id}")
                        break

        if not actual_profile_id:
            logger.error(f"Profile {pid} not found in profiles cache")
            logger.error(f"Available profiles: {list(profiles.keys())[:5]}...")  # Show first 5 for debugging
            return False

        key = actual_profile_id

        # Check if already running
        with profiles_lock:
            if key in profiles:
                if profiles[key].get('thread') and profiles[key]['thread'].is_alive():
                    logger.info(f"Profile {pid} (key: {key}) is already running")
                    return False

        # Check Airtable status has priority
        with profiles_lock:
            if key in profiles:
                airtable_status = profiles[key].get('airtable_status', 'Alive')
                # Convert array to string if needed (Airtable multi-select fields)
                if isinstance(airtable_status, list):
                    airtable_status = airtable_status[0] if airtable_status else 'Alive'
                
                # Only block if status is Follow Block or Suspended
                if airtable_status == 'Follow Block' or airtable_status == 'Suspended':
                    logger.info(f"Profile {pid} (key: {key}) is {airtable_status} in Airtable")
                    return False

        # Get follow limits
        config = ConfigManager.load_config() or {}
        limits_config = config.get('limits', {})
        follow_range = limits_config.get('max_follows_per_profile', [40, 45])
        max_follows = random.randint(follow_range[0], follow_range[1])

        # Update profile info
        with profiles_lock:
            if key not in profiles:
                profiles[key] = {}

            profiles[key].update({
                'thread': None,
                'bot': None,
                'status': 'Queueing',
                'stop_requested': False
            })

        # Start thread
        t = threading.Thread(
            target=ProfileRunner.profile_runner_wrapper,
            args=(key, max_follows),  # Use the actual key
            daemon=True,
            name=f"Profile-{key}"
        )

        with profiles_lock:
            profiles[key]['thread'] = t

        t.start()
        logger.info(f"Profile {pid} (key: {key}) started (max {max_follows} follows)")
        
        # Update active count immediately after starting
        ConcurrencyManager.get_active_profiles_count()
        
        return True


class ProfileController:
    """Controls profile operations"""

    @staticmethod
    def start_profile(pid):
        """Start a profile with concurrent limit management"""
        if ConcurrencyManager.can_start_new_profile():
            return ProfileRunner.start_profile_internal(pid)
        else:
            ConcurrencyManager.add_to_pending_queue(pid)
            with profiles_lock:
                if str(pid) in profiles:
                    profiles[str(pid)]['status'] = 'Pending'
            logger.info(f"Profile {pid} queued")
            return True

    @staticmethod
    def stop_profile(pid):
        """Stop a running profile"""
        # First, try to find the profile by profile_number, adspower_serial, or adspower_id
        actual_profile_id = None
        with profiles_lock:
            # Try to find by profile_number first
            for key, info in profiles.items():
                if info.get('profile_number') == str(pid) or info.get('adspower_serial') == str(pid):
                    actual_profile_id = key
                    logger.info(f"Profile {pid} found in cache with key: {actual_profile_id}")
                    break
            
            # If not found by profile_number, try by adspower_id
            if not actual_profile_id and str(pid) in profiles:
                actual_profile_id = str(pid)
                logger.info(f"Profile {pid} found directly in cache")
            
            # If still not found, try to find by adspower_id in the data
            if not actual_profile_id:
                for key, info in profiles.items():
                    if info.get('adspower_id') == str(pid):
                        actual_profile_id = key
                        logger.info(f"Profile {pid} found by adspower_id with key: {actual_profile_id}")
                        break

        if not actual_profile_id:
            logger.error(f"Profile {pid} not found in profiles cache")
            return False

        key = actual_profile_id

        with profiles_lock:
            if key not in profiles:
                return False

            profiles[key]['stop_requested'] = True
            bot = profiles[key].get('bot')

        if bot:
            try:
                bot.stop_profile()
            except:
                pass

        # Wait for thread
        thread = None
        with profiles_lock:
            thread = profiles[key].get('thread')

        if thread and thread.is_alive():
            thread.join(timeout=2)

        with profiles_lock:
            profiles[key]['thread'] = None
            profiles[key]['status'] = 'Stopped'

        # Update statistics
        airtable_executor.submit(
            AirtableManager.update_profile_statistics_on_completion,
            key  # Use the actual key
        )

        return True
    
    @staticmethod
    def test_profile(pid):
        """Test a profile by running it with just 1 follow"""
        # Start the profile with max_follows=1 for testing
        return ProfileController.test_profile_internal(pid)
    
    @staticmethod
    def test_profile_internal(pid):
        """Internal function to test a profile with just 1 follow"""
        # First, try to find the profile by profile_number, adspower_serial, or adspower_id
        actual_profile_id = None
        with profiles_lock:
            # Try to find by profile_number first
            for key, info in profiles.items():
                if info.get('profile_number') == str(pid) or info.get('adspower_serial') == str(pid):
                    actual_profile_id = key
                    logger.info(f"Profile {pid} found in cache with key: {actual_profile_id}")
                    break
            
            # If not found by profile_number, try by adspower_id
            if not actual_profile_id and str(pid) in profiles:
                actual_profile_id = str(pid)
                logger.info(f"Profile {pid} found directly in cache")
            
            # If still not found, try to find by adspower_id in the data
            if not actual_profile_id:
                for key, info in profiles.items():
                    if info.get('adspower_id') == str(pid):
                        actual_profile_id = key
                        logger.info(f"Profile {pid} found by adspower_id with key: {actual_profile_id}")
                        break
        
        if not actual_profile_id:
            logger.error(f"Profile {pid} not found in profiles cache")
            return False
        
        key = actual_profile_id
        
        # Check if already running
        with profiles_lock:
            if key in profiles:
                if profiles[key].get('thread') and profiles[key]['thread'].is_alive():
                    logger.info(f"Profile {pid} (key: {key}) is already running")
                    return False
        
        # For test mode, we allow testing of blocked/suspended profiles
        # to check if they're still blocked or have been unblocked
        persistent_status = StatusManager.get_persistent_status(key)
        if persistent_status in ['blocked', 'suspended']:
            logger.info(f"Testing {persistent_status} profile {pid} (key: {key})")
        
        # Update profile info
        with profiles_lock:
            if key not in profiles:
                profiles[key] = {}
            
            profiles[key].update({
                'thread': None,
                'bot': None,
                'status': 'Testing',
                'stop_requested': False
            })
        
        # Start thread with max_follows=1 for testing
        # Use the actual profile_id (key) for the thread
        t = threading.Thread(
            target=ProfileRunner.profile_runner_wrapper,
            args=(key, 1),  # Only 1 follow for testing, use the actual key
            daemon=True,
            name=f"Profile-{key}-Test"
        )
        
        with profiles_lock:
            profiles[key]['thread'] = t
        
        t.start()
        logger.info(f"Profile {pid} (key: {key}) started in TEST mode (1 follow only)")
        return True


def start_all_profiles_backend(vps_filter='all', phase_filter='all', batch_filter='all'):
    """Start all profiles that meet the filter criteria - ULTIMATE FIX"""

    def _start_all_async():
        """Run the actual start process async"""
        try:
            # Get current Running profiles count (only Running matters for the 70 limit)
            current_running = 0
            with profiles_lock:
                for pid, info in profiles.items():
                    if info['status'] == 'Running':
                        current_running += 1
            
            current_queueing = ConcurrencyManager.get_pending_profiles_count()
            logger.info(f"Current Running profiles: {current_running}/{MAX_CONCURRENT_PROFILES}, Queueing: {current_queueing}")
            
            # Calculate how many profiles we can start (only Running count matters)
            available_slots = MAX_CONCURRENT_PROFILES - current_running
            if available_slots <= 0:
                logger.info(f"Cannot start any profiles - max concurrent limit reached ({current_running}/{MAX_CONCURRENT_PROFILES})")
                return

            # Get all alive profiles
            alive_profiles = []

            with profiles_lock:
                for pid, info in profiles.items():
                    # Check filters
                    if vps_filter != 'all' and info.get('vps_status', 'None') != vps_filter:
                        continue
                    if phase_filter != 'all' and info.get('phase', 'None') != phase_filter:
                        continue
                    if batch_filter != 'all' and info.get('batch', 'None') != batch_filter:
                        continue

                    # Check Airtable status has priority
                    airtable_status = info.get('airtable_status', 'Alive')
                    # Convert array to string if needed (Airtable multi-select fields)
                    if isinstance(airtable_status, list):
                        airtable_status = airtable_status[0] if airtable_status else 'Alive'
                    
                    # Include profiles that are Alive or Logged In (not blocked/suspended)
                    if airtable_status in ['Alive', 'Logged In ⭐️']:
                        # Check if not already running
                        thread = info.get('thread')
                        if thread is None or not thread.is_alive():
                            alive_profiles.append(pid)

            if not alive_profiles:
                logger.info("No profiles to start")
                return

            # SORT PROFILES BY ID (NUMERICALLY) TO START FROM LOWEST TO HIGHEST
            # This matches the dashboard display order
            def get_sort_key(pid):
                try:
                    # Try to convert to int for numeric sorting
                    return int(pid)
                except (ValueError, TypeError):
                    # If can't convert to int, use string sorting
                    return pid
            
            alive_profiles.sort(key=get_sort_key)

            # Limit to available slots (max 70 active profiles)
            profiles_to_start = alive_profiles[:available_slots]
            
            logger.info(f"Found {len(alive_profiles)} eligible profiles, will start {len(profiles_to_start)} (limited by {MAX_CONCURRENT_PROFILES} max concurrent)")

            if not profiles_to_start:
                logger.info("No profiles to start after applying concurrent limit")
                return

            # Get delay config
            config = ConfigManager.load_config() or {}
            delay_config = config.get('delays', {})
            profile_delay = delay_config.get('profile_start_delay', 3)

            # Start in small batches
            batch_size = 2
            for i in range(0, len(profiles_to_start), batch_size):
                batch = profiles_to_start[i:i + batch_size]

                logger.info(f"Starting batch: {batch}")

                for pid in batch:
                    ProfileController.start_profile(pid)
                    # Update active count after each profile start
                    ConcurrencyManager.get_active_profiles_count()
                    time.sleep(5)  # 5 second delay between each profile

                # Delay between batches (optional, you can remove this if you want consistent 5s between all)
                if i + batch_size < len(profiles_to_start):
                    time.sleep(0)  # No additional delay between batches since we have 5s between profiles

            logger.info(f"Start All completed - started {len(profiles_to_start)} profiles")
            
            # Enable auto replacement after Start All
            ConcurrencyManager.enable_auto_replacement()

        except Exception as e:
            logger.error(f"Error in start all: {e}")

    # Run completely async - submit and forget
    profile_executor.submit(_start_all_async)

    # Return immediately without any counting that requires locks
    return True, -1  # -1 indicates count is being calculated


def cleanup_finished_profiles():
    """Clean up stuck profiles"""
    try:
        cleanup_count = 0
        with concurrent_lock:
            with profiles_lock:
                for pid, info in list(profiles.items()):
                    if info['status'] in ['Running', 'Queueing']:
                        thread = info.get('thread')
                        if thread is None or not thread.is_alive():
                            info['status'] = 'Finished'
                            info['thread'] = None
                            info['stop_requested'] = False
                            cleanup_count += 1

                            # Update stats
                            airtable_executor.submit(
                                AirtableManager.update_profile_statistics_on_completion,
                                pid
                            )

            # Recalculate active count
            global active_profiles_count
            active_profiles_count = 0
            with profiles_lock:
                for pid, info in profiles.items():
                    if info['status'] in ['Running', 'Queueing']:
                        active_profiles_count += 1

        if cleanup_count > 0:
            logger.info(f"Cleaned up {cleanup_count} stuck profiles")
            # Try to start pending
            ConcurrencyManager.start_next_pending_profile()
            # Also try to maintain the active profile limit
            ConcurrencyManager.maintain_active_profile_limit()

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler - ULTRA FAST VERSION"""

    def log_message(self, fmt, *args):
        pass

    def _set_headers(self, code=200, ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-type', ctype)
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()

    def do_GET(self):
        global request_counter

        # Track request
        with request_lock:
            request_counter += 1
            req_id = request_counter

        u = urlparse(self.path)

        if u.path == '/':
            self._set_headers(200, 'text/html')
            with open('dashboard.html', 'rb') as f:
                self.wfile.write(f.read())
            return

        if u.path == '/api/status':
            # Get parameters
            qs = parse_qs(u.query)
            page = int(qs.get('page', [1])[0])
            per_page = int(qs.get('per_page', [100])[0])
            filter_status = qs.get('filter', ['all'])[0]
            vps_filter = qs.get('vps', ['all'])[0]
            phase_filter = qs.get('phase', ['all'])[0]
            batch_filter = qs.get('batch', ['all'])[0]

            # Get fresh data directly from sources - no caching
            cached_data = DashboardDataManager.get_dashboard_data()

            # Check if profiles are still loading
            if cached_data.get('loading'):
                response = {
                    'profiles': {},
                    'pagination': {
                        'current_page': 1,
                        'total_pages': 1,
                        'total_profiles': 0,
                        'per_page': 0,
                        'start_index': 0,
                        'end_index': 0
                    },
                    'remaining_usernames': 0,
                    'concurrent_info': {
                        'active_profiles': 0,
                        'max_concurrent': MAX_CONCURRENT_PROFILES,
                        'pending_profiles': 0
                    },
                    'filter': filter_status,
                    'vps_filter': vps_filter,
                    'phase_filter': phase_filter,
                    'batch_filter': batch_filter,
                    'vps_options': [],
                    'phase_options': [],
                    'batch_options': [],
                    'loading': True,
                    'message': cached_data.get('message', 'Loading...')
                }
                self._set_headers()
                self.wfile.write(json.dumps(response).encode())
                return

            # Filter profiles
            filtered_profiles = {}

            for pid, info in cached_data['profiles'].items():
                # Get persistent status
                persistent_status = cached_data['status'].get(pid)
                vps_status = info.get('vps_status', 'None')
                phase = info.get('phase', 'None')
                batch = info.get('batch', 'None')

                # Apply filters
                if vps_filter != 'all' and vps_status != vps_filter:
                    continue
                if phase_filter != 'all' and phase != phase_filter:
                    continue
                if batch_filter != 'all' and batch != batch_filter:
                    continue

                # Display status - prioritize Airtable status
                airtable_status = info.get('airtable_status', ['Alive'])
                
                # Ensure airtable_status is a list for proper multi-select handling
                if not isinstance(airtable_status, list):
                    airtable_status = [airtable_status] if airtable_status else ['Alive']
                
                # Determine display status based on Airtable statuses
                # Priority order: Follow Block > Suspended > Logged In > Alive > others
                if 'Follow Block' in airtable_status:
                    display_status = 'Blocked'
                elif 'Suspended' in airtable_status:
                    display_status = 'Suspended'
                elif 'Logged In ⭐️' in airtable_status:
                    # Even if locally marked as blocked/suspended, show current running status
                    display_status = info['status']
                elif 'Alive' in airtable_status:
                    # Even if locally marked as blocked/suspended, show current running status
                    display_status = info['status']
                else:
                    # For any other Airtable status, check persistent status as fallback
                    if persistent_status == 'blocked':
                        display_status = 'Blocked'
                    elif persistent_status == 'suspended':
                        display_status = 'Suspended'
                    else:
                        display_status = info['status']

                # Status filter
                if filter_status == 'all':
                    include = True
                elif filter_status == 'alive':
                    include = display_status not in ['Blocked', 'Suspended']
                elif filter_status == 'blocked':
                    include = display_status == 'Blocked'
                elif filter_status == 'suspended':
                    include = display_status == 'Suspended'
                else:
                    include = True

                if include:
                    # Get stats
                    stats = cached_data['stats'].get(pid, {
                        'last_run': 0,
                        'today': 0,
                        'total_all_time': 0
                    })

                    # Get airtable_status - keep as list for proper multi-select display
                    display_airtable_status = info.get('airtable_status', ['Alive'])
                    if not isinstance(display_airtable_status, list):
                        display_airtable_status = [display_airtable_status] if display_airtable_status else ['Alive']
                    
                    filtered_profiles[pid] = {
                        'status': display_status,
                        'stats': stats,
                        'username': info.get('username', 'Unknown'),
                        'adspower_name': info.get('adspower_name'),
                        'airtable_status': display_airtable_status,
                        'persistent_status': persistent_status,
                        'vps_status': vps_status,
                        'phase': phase,
                        'batch': batch,
                        'profile_number': info.get('profile_number', pid),
                        'has_assigned_followers': info.get('has_assigned_followers', False),
                        'assigned_followers_count': info.get('assigned_followers_count', 0)
                    }

            # Sort profiles by profile number (no pagination - show all)
            # Sort by profile number, converting to int for proper numeric sorting
            def get_sort_key(pid):
                try:
                    profile_num = filtered_profiles[pid].get('profile_number', '999999')
                    return int(profile_num)
                except (ValueError, TypeError):
                    # If can't convert to int, put at end
                    return 999999
            
            sorted_ids = sorted(filtered_profiles.keys(), key=get_sort_key)
            total = len(sorted_ids)
            
            # Return all profiles without pagination
            page_profiles = filtered_profiles

            # Get counts
            active_count = ConcurrencyManager.get_active_profiles_count()  # Only Running
            pending_count = ConcurrencyManager.get_pending_profiles_count()  # Only Queueing

            # Build response
            response = {
                'profiles': page_profiles,
                'pagination': {
                    'current_page': 1,
                    'total_pages': 1,
                    'total_profiles': total,
                    'per_page': total,
                    'start_index': 1 if total > 0 else 0,
                    'end_index': total
                },
                'remaining_usernames': UsernameManager.get_remaining_count(),
                'concurrent_info': {
                    'active_profiles': active_count,
                    'max_concurrent': MAX_CONCURRENT_PROFILES,
                    'pending_profiles': pending_count
                },
                'filter': filter_status,
                'vps_filter': vps_filter,
                'phase_filter': phase_filter,
                'batch_filter': batch_filter,
                'vps_options': AirtableManager.get_vps_options(),
                'phase_options': AirtableManager.get_phase_options(),
                'batch_options': AirtableManager.get_batch_options()
            }

            self._set_headers()
            self.wfile.write(json.dumps(response).encode())
            return

        if u.path == '/api/active-count':
            self._set_headers(200, 'application/json')
            try:
                active_count, total_count = get_active_profiles_count()
                response = {
                    'active_profiles': active_count,
                    'total_profiles': total_count,
                    'chrome_processes': len([p for p in os.popen('ps aux | grep -i chrome | grep -v grep').readlines()])
                }
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                error_response = {'error': str(e)}
                self.wfile.write(json.dumps(error_response).encode())
            return

        if u.path == '/api/control':
            qs = parse_qs(u.query)
            act = qs.get('action', [''])[0]
            pid = qs.get('profile', [''])[0]

            if act == 'start':
                ok = ProfileController.start_profile(pid)
                self._set_headers()
                self.wfile.write(json.dumps({'success': ok}).encode())
                return
            elif act == 'stop':
                ok = ProfileController.stop_profile(pid)
                self._set_headers()
                self.wfile.write(json.dumps({'success': ok}).encode())
                return
            elif act == 'test':
                ok = ProfileController.test_profile(pid)
                self._set_headers()
                self.wfile.write(json.dumps({'success': ok}).encode())
                return
            elif act == 'start_all':
                vps = qs.get('vps', ['all'])[0]
                phase = qs.get('phase', ['all'])[0]
                batch = qs.get('batch', ['all'])[0]

                # Just trigger the start and return immediately
                success, count = start_all_profiles_backend(vps, phase, batch)

                # Return success immediately without waiting
                self._set_headers()
                self.wfile.write(json.dumps({'success': success, 'count': count}).encode())
                return
            elif act == 'enable_auto_replacement':
                ConcurrencyManager.enable_auto_replacement()
                self._set_headers()
                self.wfile.write(json.dumps({'success': True, 'message': 'Auto replacement enabled'}).encode())
                return
            elif act == 'disable_auto_replacement':
                ConcurrencyManager.disable_auto_replacement()
                self._set_headers()
                self.wfile.write(json.dumps({'success': True, 'message': 'Auto replacement disabled'}).encode())
                return
            elif act == 'stop_all':
                ConcurrencyManager.disable_auto_replacement()
                # Stop all running profiles
                with profiles_lock:
                    for pid, info in profiles.items():
                        if info['status'] in ['Running', 'Queueing']:
                            info['stop_requested'] = True
                self._set_headers()
                self.wfile.write(json.dumps({'success': True, 'message': 'All profiles stopped and auto replacement disabled'}).encode())
                return
            else:
                self._set_headers()
                self.wfile.write(json.dumps({'success': False}).encode())
                return

        self._set_headers(404)
        self.wfile.write(json.dumps({'error': 'Not found'}).encode())


def initialize_profiles():
    """Initialize profiles from Airtable - ASYNC VERSION"""
    logger.info("Starting async profile loading from Airtable...")
    
    def load_profiles_async():
        """Load profiles in background"""
        try:
            profiles_list = AirtableManager.load_profiles()

            if not profiles_list:
                logger.error("No profiles loaded!")
                return False

            with profiles_lock:
                for profile_data in profiles_list:
                    pid = profile_data['id']  # This is the AdsPower ID (like 'kwyc4ml')
                    profile_number = profile_data.get('profile_number')
                    # Handle airtable_status as array - keep full list for proper status handling
                    airtable_status = profile_data.get('airtable_status', ['Alive'])
                    if not isinstance(airtable_status, list):
                        airtable_status = [airtable_status] if airtable_status else ['Alive']
                    
                    profiles[str(pid)] = {
                        'thread': None,
                        'bot': None,
                        'status': 'Not Running',
                        'stop_requested': False,
                        'username': profile_data['username'],
                        'adspower_name': profile_data.get('adspower_name'),
                        'adspower_id': profile_data.get('adspower_id'),
                        'adspower_serial': profile_data.get('adspower_serial'),
                        'profile_number': profile_number,
                        'airtable_status': airtable_status,
                        'vps_status': profile_data.get('vps_status', 'None'),
                        'phase': profile_data.get('phase', 'None'),
                        'batch': profile_data.get('batch', 'None'),
                        'assigned_followers_file': profile_data.get('assigned_followers_file')
                    }

            logger.info(f"✅ Loaded {len(profiles_list)} profiles from Airtable")
            
            # Load profile-specific usernames
            for profile_data in profiles_list:
                pid = profile_data['id']
                followers_file = profile_data.get('assigned_followers_file')
                if followers_file:
                    count = ProfileSpecificUsernameManager.load_profile_usernames(pid, followers_file)
                    logger.info(f"Profile {pid}: Loaded {count} assigned followers")
            
            return True
            
        except Exception as e:
            logger.error(f"Error loading profiles: {e}")
            return False

    # Start loading in background
    airtable_executor.submit(load_profiles_async)
    
    # Return immediately - profiles will be loaded in background
    logger.info("Profile loading started in background...")
    return True


def run():
    """Main function"""
    # Initialize
    if not initialize_profiles():
        return

    # Load usernames
    username_count = UsernameManager.load_usernames_to_queue()
    logger.info(f"Loaded {username_count} usernames")

    # No cache pre-population needed - we read directly from sources

    # Start monitor thread
    monitor_thread = threading.Thread(
        target=ConcurrencyManager.monitor_and_start_pending,
        daemon=True,
        name="Monitor"
    )
    monitor_thread.start()

    # Log info
    logger.info(f"Dashboard at http://localhost:{PORT}")
    logger.info("✨ SIMPLIFIED ARCHITECTURE:")
    logger.info("  - No caching - reads directly from Airtable and files")
    logger.info("  - Multiple thread pools for different tasks")
    logger.info("  - Async file I/O - no blocking")
    logger.info("  - Fast request handling without cache complexity")
    logger.info("  - Smart batching for Start All")
    logger.info("  - Profiles start in order from lowest to highest ID")
    logger.info("  - Scales to 10k+ profiles without memory issues")
    logger.info("🚀 Simple, fast, and reliable!")

    # Start server
    try:
        httpd = ThreadingHTTPServer(('0.0.0.0', PORT), DashboardHandler)
        logger.info(f"Server started successfully on port {PORT}")
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except OSError as e:
        if e.errno == 48:  # Address already in use
            logger.error(f"Port {PORT} is already in use. Please stop the existing server or use a different port.")
            logger.info("To kill the existing server, run: lsof -ti:8080 | xargs kill -9")
        else:
            logger.error(f"Server error: {e}")
    except Exception as e:
        logger.error(f"Server error: {e}")


if __name__ == '__main__':
    run()