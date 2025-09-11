import requests
import time
import random
import os
import sys
import threading
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging

# Try to import webdriver_manager for automatic ChromeDriver management
try:
    from webdriver_manager.chrome import ChromeDriverManager

    USE_WEBDRIVER_MANAGER = True
except ImportError:
    USE_WEBDRIVER_MANAGER = False

# Airtable integration
try:
    from pyairtable import Api

    AIRTABLE_AVAILABLE = True
except ImportError:
    AIRTABLE_AVAILABLE = False
    logging.warning("pyairtable not installed. Install with: pip install pyairtable")

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Dynamic path - looks for chromedriver in script folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMEDRIVER_PATH = os.path.join(SCRIPT_DIR, "chromedrivers", "chromedriver.exe")

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

# Global lock for file operations to prevent race conditions
file_lock = threading.Lock()


def update_airtable_status(profile_number, status):
    """Update profile status in Airtable"""
    if not AIRTABLE_AVAILABLE:
        logger.warning(f"Profile {profile_number}: Airtable not available, cannot update status to '{status}'")
        return False

    try:
        api = Api(AIRTABLE_PERSONAL_ACCESS_TOKEN)
        table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

        # Find the profile record
        records = table.all(formula=f"{{Profile}} = {profile_number}")

        if records:
            record_id = records[0]['id']
            update_data = {'Status': [status]}
            result = table.update(record_id, update_data)
            logger.info(f"Profile {profile_number}: Successfully updated Airtable status to '{status}'")
            return True
        else:
            logger.warning(f"Profile {profile_number}: Profile not found in Airtable for status update")
            return False

    except Exception as e:
        logger.error(f"Profile {profile_number}: Error updating Airtable status to '{status}': {str(e)}")
        return False


class InstagramFollowBot:
    def __init__(self, adspower_api_url=None, profile_id=None):
        """
        Initialize the Instagram Follow Bot

        Args:
            adspower_api_url: AdsPower API base URL
            profile_id: The profile ID to use
        """
        self.adspower_api_url = adspower_api_url or ADSPOWER_API_URL
        self.profile_id = profile_id
        self.driver = None
        self.debug_port = None
        self.adspower_response = None
        self.consecutive_follow_errors = 0
        self.is_suspended = False
        self.consecutive_follow_blocks = 0
        self.is_follow_blocked = False
        self.window_recovery_attempts = 0
        self.max_window_recovery_attempts = 3

    def check_adspower_connection(self):
        """Check if AdsPower API is accessible"""
        try:
            test_url = f"{self.adspower_api_url}/api/v1/user/list"
            headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
            response = requests.get(test_url, headers=headers, timeout=5)
            if response.status_code == 200:
                logger.info(f"AdsPower API is accessible at {self.adspower_api_url}")
                return True
            else:
                logger.error(f"AdsPower API returned status code: {response.status_code}")
                return False
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to AdsPower API at {self.adspower_api_url} - Make sure AdsPower is running!")
            return False
        except Exception as e:
            logger.error(f"Error checking AdsPower connection: {e}")
            return False

    def start_profile(self):
        """Start the AdsPower profile and get browser connection details"""
        try:
            # First check if AdsPower is accessible
            if not self.check_adspower_connection():
                return False
                
            url = f"{self.adspower_api_url}/api/v1/browser/start"
            params = {
                "serial_number": self.profile_id,
                "launch_args": "",
                "headless": 0,
                "disable_password_filling": 0,
                "clear_cache_after_closing": 0,
                "enable_password_saving": 0
            }

            headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
            response = requests.get(url, params=params, headers=headers)
            logger.info(f"AdsPower API request to {url} with params: {params}")
            
            try:
                data = response.json()
                logger.info(f"AdsPower API response: {data}")
            except Exception as e:
                logger.error(f"Failed to parse AdsPower response: {e}")
                logger.error(f"Response status: {response.status_code}")
                logger.error(f"Response text: {response.text}")
                return False

            if data.get('code') == 0:
                # Store the complete AdsPower response
                self.adspower_response = data

                debug_info = data['data']['ws']['selenium']
                # Extract just the port number if it includes host:port format
                if ':' in str(debug_info):
                    self.debug_port = str(debug_info).split(':')[-1]
                else:
                    self.debug_port = str(debug_info)
                logger.info(f"Profile No.{self.profile_id} started successfully. Debug port: {self.debug_port}")
                return True
            else:
                error_msg = data.get('msg', 'Unknown error')
                # Check if profile is already running
                if any(keyword in error_msg.lower() for keyword in [
                    'already', 'running', 'opened', 'started'
                ]):
                    logger.warning(
                        f"Profile No.{self.profile_id} is already running. Attempting to connect to existing session...")
                    # Try to get the running profile's connection info
                    return self.get_running_profile_info()
                else:
                    logger.error(f"Failed to start profile No.{self.profile_id}: {error_msg}")
                    return False

        except Exception as e:
            logger.error(f"Error starting profile No.{self.profile_id}: {str(e)}")
            return False

    def get_running_profile_info(self):
        """Get connection info for an already running profile"""
        try:
            # Try to get profile status/info
            url = f"{self.adspower_api_url}/api/v1/browser/active"
            params = {"serial_number": self.profile_id}

            headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
            response = requests.get(url, params=params, headers=headers)
            data = response.json()

            if data.get('code') == 0 and data.get('data'):
                # Store the complete AdsPower response
                self.adspower_response = data

                debug_info = data['data'].get('ws', {}).get('selenium')
                if debug_info:
                    # Extract just the port number if it includes host:port format
                    if ':' in str(debug_info):
                        self.debug_port = str(debug_info).split(':')[-1]
                    else:
                        self.debug_port = str(debug_info)
                    logger.info(
                        f"Profile No.{self.profile_id} already running. Connected to existing session. Debug port: {self.debug_port}")
                    return True

            # If we can't get connection info, try to restart the profile
            logger.warning(
                f"Profile No.{self.profile_id}: Could not get connection info for running profile. Attempting restart...")
            return self.restart_profile()

        except Exception as e:
            logger.error(f"Error getting running profile info for No.{self.profile_id}: {str(e)}")
            # Try to restart on any error
            logger.info(f"Profile No.{self.profile_id}: Attempting restart due to connection error...")
            return self.restart_profile()

    def restart_profile(self):
        """Close and restart a profile that's already running but not connectable"""
        try:
            logger.info(f"Profile No.{self.profile_id}: Closing existing session...")

            # First, try to stop the profile
            stop_url = f"{self.adspower_api_url}/api/v1/browser/stop"
            stop_params = {"serial_number": self.profile_id}

            headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
            stop_response = requests.get(stop_url, params=stop_params, headers=headers)
            stop_data = stop_response.json()

            if stop_data.get('code') == 0:
                logger.info(f"Profile No.{self.profile_id}: Successfully closed existing session")
            else:
                logger.warning(f"Profile No.{self.profile_id}: Stop response: {stop_data.get('msg', 'Unknown')}")

            # Wait a moment for the profile to fully close
            time.sleep(2)

            # Now try to start it again
            logger.info(f"Profile No.{self.profile_id}: Starting fresh session...")

            start_url = f"{self.adspower_api_url}/api/v1/browser/start"
            start_params = {
                "serial_number": self.profile_id,
                "launch_args": "",
                "headless": 0,
                "disable_password_filling": 0,
                "clear_cache_after_closing": 0,
                "enable_password_saving": 0
            }

            headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
            start_response = requests.get(start_url, params=start_params, headers=headers)
            start_data = start_response.json()

            if start_data.get('code') == 0:
                # Store the complete AdsPower response
                self.adspower_response = start_data

                debug_info = start_data['data']['ws']['selenium']
                # Extract just the port number if it includes host:port format
                if ':' in str(debug_info):
                    self.debug_port = str(debug_info).split(':')[-1]
                else:
                    self.debug_port = str(debug_info)
                logger.info(f"Profile No.{self.profile_id}: Successfully restarted! Debug port: {self.debug_port}")
                return True
            else:
                logger.error(
                    f"Profile No.{self.profile_id}: Failed to restart - {start_data.get('msg', 'Unknown error')}")
                return False

        except Exception as e:
            logger.error(f"Profile No.{self.profile_id}: Error during restart: {str(e)}")
            return False

    def connect_to_browser(self):
        """Connect to the AdsPower browser instance using ChromeDriver path from AdsPower API with retry logic"""
        max_connection_retries = 3
        connection_retry_delay = 5
        
        for conn_attempt in range(max_connection_retries):
            try:
                chrome_options = Options()
                chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.debug_port}")

                # Method 1: Try to get ChromeDriver path from AdsPower API response
                chrome_driver_path = None
                if self.adspower_response and 'data' in self.adspower_response:
                    chrome_driver_path = self.adspower_response['data'].get('webdriver')
                    if chrome_driver_path:
                        logger.info(
                            f"Profile No.{self.profile_id}: Using ChromeDriver from AdsPower API: {chrome_driver_path}")
                        try:
                            service = Service(chrome_driver_path)
                            self.driver = webdriver.Chrome(service=service, options=chrome_options)
                            logger.info(
                                f"Profile No.{self.profile_id}: Connected to browser successfully using AdsPower ChromeDriver")
                            return True
                        except Exception as e:
                            logger.warning(f"Profile No.{self.profile_id}: AdsPower ChromeDriver failed: {str(e)[:100]}...")

                # Method 2: Use webdriver-manager (if available)
                if USE_WEBDRIVER_MANAGER:
                    logger.info(f"Profile No.{self.profile_id}: Using webdriver-manager to handle ChromeDriver")
                    try:
                        # Try Chrome 137 compatible version
                        service = Service(ChromeDriverManager(version="137.0.7106.61").install())
                        self.driver = webdriver.Chrome(service=service, options=chrome_options)
                        logger.info(f"Profile No.{self.profile_id}: Connected using webdriver-manager (Chrome 137)")
                        return True
                    except Exception as e:
                        logger.info(f"Profile No.{self.profile_id}: webdriver-manager Chrome 137 failed: {e}")
                        try:
                            # Fallback to Chrome 138
                            service = Service(ChromeDriverManager(version="138.0.7106.61").install())
                            self.driver = webdriver.Chrome(service=service, options=chrome_options)
                            logger.info(f"Profile No.{self.profile_id}: Connected using webdriver-manager (Chrome 138)")
                            return True
                        except Exception as e2:
                            logger.info(f"Profile No.{self.profile_id}: webdriver-manager Chrome 138 failed: {e2}")
                            try:
                                # Last resort - latest version
                                logger.info(f"Profile No.{self.profile_id}: Trying with latest ChromeDriver version")
                                service = Service(ChromeDriverManager().install())
                                self.driver = webdriver.Chrome(service=service, options=chrome_options)
                                logger.info(f"Profile No.{self.profile_id}: Connected using webdriver-manager (latest)")
                                return True
                            except Exception as e3:
                                logger.warning(f"Profile No.{self.profile_id}: All webdriver-manager attempts failed: {e3}")

                # Method 3: Use ChromeDriver from local chromedrivers folder
                if os.path.exists(CHROMEDRIVER_PATH):
                    logger.info(f"Profile No.{self.profile_id}: Using ChromeDriver from local folder: {CHROMEDRIVER_PATH}")
                    try:
                        service = Service(CHROMEDRIVER_PATH)
                        self.driver = webdriver.Chrome(service=service, options=chrome_options)
                        logger.info(f"Profile No.{self.profile_id}: Connected using local ChromeDriver")
                        return True
                    except Exception as e:
                        logger.warning(f"Profile No.{self.profile_id}: Local ChromeDriver failed: {str(e)[:100]}...")

                # Method 4: Try to use ChromeDriver from PATH
                logger.info(f"Profile No.{self.profile_id}: Trying to use ChromeDriver from system PATH")
                try:
                    self.driver = webdriver.Chrome(options=chrome_options)
                    logger.info(f"Profile No.{self.profile_id}: Connected using system PATH ChromeDriver")
                    return True
                except Exception as e:
                    logger.error(f"Profile No.{self.profile_id}: System PATH ChromeDriver failed: {str(e)[:100]}...")

            except Exception as e:
                if conn_attempt < max_connection_retries - 1:
                    logger.warning(f"Profile No.{self.profile_id}: Browser connection attempt {conn_attempt + 1}/{max_connection_retries} failed: {str(e)}")
                    logger.info(f"Profile No.{self.profile_id}: Retrying in {connection_retry_delay} seconds...")
                    time.sleep(connection_retry_delay)
                else:
                    logger.error(f"Profile No.{self.profile_id}: All connection attempts failed: {str(e)}")
                    return False

        # All methods failed
        logger.error(f"Profile No.{self.profile_id}: All ChromeDriver methods failed!")
        return False
    
    def close_extra_tabs(self):
        """Close all tabs except one Instagram tab to reduce RAM usage"""
        try:
            if not self.driver:
                return False
                
            # Get all window handles
            all_windows = self.driver.window_handles
            logger.info(f"Profile No.{self.profile_id}: Found {len(all_windows)} open tabs")
            
            if len(all_windows) <= 1:
                # Only one tab, nothing to close
                return True
                
            instagram_window = None
            windows_to_close = []
            
            # Check each window to find Instagram tab
            for window in all_windows:
                try:
                    self.driver.switch_to.window(window)
                    current_url = self.driver.current_url
                    
                    # Check if this is an Instagram tab
                    if "instagram.com" in current_url and not instagram_window:
                        instagram_window = window
                        logger.info(f"Profile No.{self.profile_id}: Found Instagram tab: {current_url}")
                    else:
                        windows_to_close.append(window)
                except Exception as e:
                    logger.debug(f"Profile No.{self.profile_id}: Error checking window: {str(e)}")
                    windows_to_close.append(window)
            
            # Close all non-Instagram tabs
            for window in windows_to_close:
                try:
                    self.driver.switch_to.window(window)
                    self.driver.close()
                    logger.info(f"Profile No.{self.profile_id}: Closed extra tab")
                except Exception as e:
                    logger.debug(f"Profile No.{self.profile_id}: Error closing tab: {str(e)}")
            
            # Switch to Instagram tab or create new one if none found
            remaining_windows = self.driver.window_handles
            if instagram_window and instagram_window in remaining_windows:
                self.driver.switch_to.window(instagram_window)
            elif remaining_windows:
                # Use the remaining window
                self.driver.switch_to.window(remaining_windows[0])
            else:
                # All windows closed, open a new one
                logger.warning(f"Profile No.{self.profile_id}: All windows closed, opening new tab")
                self.driver.execute_script("window.open('about:blank', '_blank');")
                self.driver.switch_to.window(self.driver.window_handles[-1])
            
            logger.info(f"Profile No.{self.profile_id}: Tab cleanup complete. {len(self.driver.window_handles)} tab(s) remaining")
            return True
            
        except Exception as e:
            logger.error(f"Profile No.{self.profile_id}: Error during tab cleanup: {str(e)}")
            return False

    def stop_profile(self):
        """Stop the AdsPower profile"""
        try:
            if self.driver:
                self.driver.quit()

            url = f"{self.adspower_api_url}/api/v1/browser/stop"
            params = {"serial_number": self.profile_id}

            headers = {"api_key": ADSPOWER_API_KEY} if ADSPOWER_API_KEY else {}
            response = requests.get(url, params=params, headers=headers)
            data = response.json()

            if data.get('code') == 0:
                logger.info(f"Profile No.{self.profile_id} stopped successfully")
                return True
            else:
                error_msg = data.get('msg', 'Unknown error')
                # Don't log as error if profile was already stopped
                if any(keyword in error_msg.lower() for keyword in [
                    'not running', 'not started', 'already stopped', 'not found'
                ]):
                    logger.info(f"Profile No.{self.profile_id} was already stopped")
                    return True
                else:
                    logger.error(f"Failed to stop profile No.{self.profile_id}: {error_msg}")
                    return False

        except Exception as e:
            logger.error(f"Error stopping profile No.{self.profile_id}: {str(e)}")
            return False

    def check_and_recover_window(self):
        """Check if browser window is available and attempt recovery if needed"""
        try:
            # Try to access current window handle
            if self.driver and self.driver.current_window_handle:
                return True
        except Exception:
            pass

        # Window is not available, attempt recovery
        if self.window_recovery_attempts >= self.max_window_recovery_attempts:
            logger.error(
                f"Profile No.{self.profile_id}: Max window recovery attempts ({self.max_window_recovery_attempts}) reached. Stopping profile.")
            return False

        self.window_recovery_attempts += 1
        logger.warning(
            f"Profile No.{self.profile_id}: Window closed. Attempting recovery #{self.window_recovery_attempts}...")

        try:
            # Wait a few seconds before attempting recovery
            time.sleep(3)

            # Method 1: Try to get available windows and switch to one
            try:
                windows = self.driver.window_handles
                if windows:
                    # Switch to the first available window
                    self.driver.switch_to.window(windows[0])
                    # Navigate to Instagram
                    self.driver.get("https://www.instagram.com")
                    logger.info(
                        f"Profile No.{self.profile_id}: Switched to existing window. Recovery attempt #{self.window_recovery_attempts} successful.")
                    return True
            except Exception as e1:
                logger.debug(f"Profile No.{self.profile_id}: Method 1 failed: {str(e1)[:50]}...")

            # Method 2: Try to open a new window using JavaScript
            try:
                self.driver.execute_script("window.open('https://www.instagram.com', '_blank');")
                windows = self.driver.window_handles
                if len(windows) > 1:
                    self.driver.switch_to.window(windows[-1])  # Switch to the newest window
                    logger.info(
                        f"Profile No.{self.profile_id}: Opened new window. Recovery attempt #{self.window_recovery_attempts} successful.")
                    return True
            except Exception as e2:
                logger.debug(f"Profile No.{self.profile_id}: Method 2 failed: {str(e2)[:50]}...")

            # Method 3: Try to navigate in current context
            try:
                self.driver.get("https://www.instagram.com")
                logger.info(
                    f"Profile No.{self.profile_id}: Navigated in current context. Recovery attempt #{self.window_recovery_attempts} successful.")
                return True
            except Exception as e3:
                logger.debug(f"Profile No.{self.profile_id}: Method 3 failed: {str(e3)[:50]}...")

            # All methods failed
            logger.error(
                f"Profile No.{self.profile_id}: All recovery methods failed for attempt #{self.window_recovery_attempts}")
            return False

        except Exception as e:
            logger.error(
                f"Profile No.{self.profile_id}: Window recovery attempt #{self.window_recovery_attempts} failed: {str(e)[:100]}...")
            return False

    def navigate_to_instagram(self):
        """Navigate to Instagram and check for suspension"""
        try:
            # Close extra tabs first to reduce RAM usage
            self.close_extra_tabs()
            
            self.driver.get("https://www.instagram.com")
            time.sleep(2)

            # Check if account is suspended immediately after navigation
            if self.check_if_suspended():
                self.is_suspended = True
                logger.error(
                    f"Profile No.{self.profile_id}: Account SUSPENDED detected during navigation - updating Airtable")
                update_airtable_status(self.profile_id, 'Suspended')
                return False

            # Check for follow restriction modal after navigation
            if self.check_for_follow_restriction_modal():
                self.is_follow_blocked = True
                logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected after navigation - updating Airtable")
                # Update local profile status for dashboard
                self._update_local_profile_status()
                return False

            logger.info(f"Profile No.{self.profile_id}: Navigated to Instagram")
            return True
        except Exception as e:
            logger.error(f"Profile No.{self.profile_id}: Error navigating to Instagram: {str(e)}")
            return False

    def check_if_suspended(self):
        """Check if the current Instagram account is suspended"""
        try:
            # Check if browser window is available and attempt recovery if needed
            if not self.check_and_recover_window():
                logger.warning(
                    f"Profile No.{self.profile_id}: Cannot check suspension - browser unavailable. Stopping profile.")
                return True

            current_url = self.driver.current_url

            # Method 1: Check URL for suspension redirect
            if "/accounts/suspended/" in current_url:
                logger.error(f"Profile No.{self.profile_id}: Account is SUSPENDED (detected via URL)")
                update_airtable_status(self.profile_id, 'Suspended')
                return True

            # Method 2: Check page source for suspension keywords
            page_source = self.driver.page_source.lower()
            suspension_indicators = [
                "we suspended your account",
                "your account has been suspended",
                "account suspended",
                "suspended on",
                "we've suspended your account"
            ]

            for indicator in suspension_indicators:
                if indicator in page_source:
                    logger.error(f"Profile No.{self.profile_id}: Account is SUSPENDED (detected via page content)")
                    update_airtable_status(self.profile_id, 'Suspended')
                    return True

            # Method 3: Check for specific suspension page elements
            suspension_selectors = [
                "//h1[contains(text(), 'suspended')]",
                "//h2[contains(text(), 'suspended')]",
                "//*[contains(text(), 'We suspended your account')]",
                "//*[contains(text(), 'suspended on')]"
            ]

            for selector in suspension_selectors:
                try:
                    element = self.driver.find_element(By.XPATH, selector)
                    if element:
                        logger.error(f"Profile No.{self.profile_id}: Account is SUSPENDED (detected via page element)")
                        update_airtable_status(self.profile_id, 'Suspended')
                        return True
                except NoSuchElementException:
                    continue

            return False

        except Exception as e:
            logger.warning(f"Profile No.{self.profile_id}: Error checking suspension: {str(e)[:100]}...")
            return False

    def check_if_public_account(self):
        """Check if the current profile page shows a public account"""
        try:
            page_source = self.driver.page_source.lower()

            # Look for private account indicators
            private_indicators = [
                "this account is private",
                "only approved followers can see",
                "follow to see their photos and videos",
                "this account is private.",
                "account is private"
            ]

            for indicator in private_indicators:
                if indicator in page_source:
                    logger.debug(f"Profile No.{self.profile_id}: Account appears to be PRIVATE (found: '{indicator}')")
                    return False

            # Look for additional private account elements
            try:
                private_elements = [
                    "//*[contains(text(), 'This account is private')]",
                    "//*[contains(text(), 'Only approved followers')]",
                    "//*[contains(text(), 'Follow to see')]"
                ]

                for selector in private_elements:
                    try:
                        element = self.driver.find_element(By.XPATH, selector)
                        if element and element.is_displayed():
                            logger.debug(f"Profile No.{self.profile_id}: Account appears to be PRIVATE (found element)")
                            return False
                    except NoSuchElementException:
                        continue
            except Exception:
                pass

            # If no private indicators found, assume it's public
            logger.debug(f"Profile No.{self.profile_id}: Account appears to be PUBLIC (no private indicators found)")
            return True

        except Exception as e:
            logger.warning(f"Profile No.{self.profile_id}: Error checking if account is public: {str(e)[:100]}...")
            return None

    def check_follow_action_success(self, username, max_wait_time=8):
        """Enhanced follow success check with public account follow block detection"""
        try:
            start_time = time.time()
            logger.info(f"Profile No.{self.profile_id}: Checking if follow was successful for {username}...")

            # Wait for explicit success indicators or timeout
            while time.time() - start_time < max_wait_time:
                try:
                    # Check for follow restriction modal only every 3 seconds to avoid performance issues
                    if (time.time() - start_time) % 3 < 0.5:  # Check roughly every 3 seconds
                        if self.check_for_follow_restriction_modal():
                            logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected during follow check for {username}")
                            self.is_follow_blocked = True
                            return False

                    # Method 1: Look for "Following" or "Requested" buttons
                    success_selectors = [
                        "//button[text()='Following']",
                        "//button[text()='Requested']",
                        "//button[contains(text(), 'Following') and not(contains(text(), 'Follow '))]",
                        "//button[contains(text(), 'Requested')]",
                        "//*[contains(@class, 'button') and text()='Following']",
                        "//*[contains(@class, 'button') and text()='Requested']"
                    ]

                    for selector in success_selectors:
                        try:
                            success_button = self.driver.find_element(By.XPATH, selector)
                            if success_button and success_button.is_displayed():
                                button_text = success_button.text.strip()
                                if button_text == 'Following':
                                    logger.info(
                                        f"Profile No.{self.profile_id}: ✅ Follow CONFIRMED for {username} - found 'Following' button")
                                    return True
                                elif button_text == 'Requested':
                                    # NEW: Check if this is a public account showing "Requested" (follow block indicator)
                                    logger.info(
                                        f"Profile No.{self.profile_id}: Found 'Requested' button for {username} - checking if account is public...")
                                    is_public = self.check_if_public_account()

                                    if is_public is True:
                                        # Public account showing "Requested" = Follow block
                                        logger.error(
                                            f"Profile No.{self.profile_id}: ❌ FOLLOW BLOCK detected for {username} - public account showing 'Requested'")
                                        self.is_follow_blocked = True
                                        update_airtable_status(self.profile_id, 'Follow Block')
                                        return False
                                    elif is_public is False:
                                        # Private account showing "Requested" = Normal behavior
                                        logger.info(
                                            f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - private account showing 'Requested' (normal)")
                                        return True
                                    else:
                                        # Cannot determine account type - treat as success but log warning
                                        logger.warning(
                                            f"Profile No.{self.profile_id}: ⚠️ Follow result UNCERTAIN for {username} - cannot determine if account is public/private")
                                        return True
                        except NoSuchElementException:
                            continue

                    # Method 2: Check if Follow button disappeared
                    follow_button_present = False
                    follow_selectors = [
                        "//button[text()='Follow']",
                        "//button[contains(text(), 'Follow') and not(contains(text(), 'Following')) and not(contains(text(), 'Requested'))]"
                    ]

                    for selector in follow_selectors:
                        try:
                            follow_button = self.driver.find_element(By.XPATH, selector)
                            if follow_button and follow_button.is_displayed():
                                button_text = follow_button.text.strip()
                                if button_text == 'Follow':
                                    follow_button_present = True
                                    break
                        except NoSuchElementException:
                            continue

                    # If no Follow button and we've waited at least 3 seconds, likely success
                    if not follow_button_present and (time.time() - start_time) >= 3:
                        # Double-check by looking for any button text
                        all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                        button_texts = []
                        for btn in all_buttons:
                            try:
                                if btn.is_displayed():
                                    text = btn.text.strip()
                                    if text in ['Following', 'Requested', 'Follow', 'Message']:
                                        button_texts.append(text)
                            except:
                                continue

                        logger.info(f"Profile No.{self.profile_id}: Found buttons: {button_texts}")

                        if 'Following' in button_texts:
                            logger.info(
                                f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - found 'Following' in button scan")
                            return True
                        elif 'Requested' in button_texts:
                            # NEW: Check if this is a public account showing "Requested" (follow block indicator)
                            logger.info(
                                f"Profile No.{self.profile_id}: Found 'Requested' in button scan for {username} - checking if account is public...")
                            is_public = self.check_if_public_account()

                            if is_public is True:
                                # Public account showing "Requested" = Follow block
                                logger.error(
                                    f"Profile No.{self.profile_id}: ❌ FOLLOW BLOCK detected for {username} - public account showing 'Requested' (button scan)")
                                self.is_follow_blocked = True
                                update_airtable_status(self.profile_id, 'Follow Block')
                                return False
                            elif is_public is False:
                                # Private account showing "Requested" = Normal behavior
                                logger.info(
                                    f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - private account showing 'Requested' (button scan)")
                                return True
                            else:
                                # Cannot determine account type - treat as success but log warning
                                logger.warning(
                                    f"Profile No.{self.profile_id}: ⚠️ Follow result UNCERTAIN for {username} - cannot determine if account is public/private (button scan)")
                                return True
                        elif 'Follow' not in button_texts:
                            logger.info(
                                f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - Follow button gone, no explicit success button")
                            return True

                    # Wait before next check
                    time.sleep(0.8)

                except Exception as inner_e:
                    logger.debug(f"Profile No.{self.profile_id}: Error during follow check: {str(inner_e)[:50]}...")
                    time.sleep(0.8)
                    continue

            # Timeout reached - do final comprehensive check
            logger.info(f"Profile No.{self.profile_id}: Timeout reached, doing final verification for {username}")
            time.sleep(1)  # Give page time to settle

            # Final comprehensive scan
            try:
                # Get all button texts on the page
                all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                relevant_buttons = []

                for btn in all_buttons:
                    try:
                        if btn.is_displayed():
                            text = btn.text.strip()
                            if text in ['Following', 'Requested', 'Follow', 'Message']:
                                relevant_buttons.append(text)
                    except:
                        continue

                logger.info(f"Profile No.{self.profile_id}: Final scan - relevant buttons: {relevant_buttons}")

                # Check results
                if 'Following' in relevant_buttons:
                    logger.info(
                        f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - final scan found 'Following'")
                    return True
                elif 'Requested' in relevant_buttons:
                    # NEW: Final check for public account with "Requested" button
                    logger.info(
                        f"Profile No.{self.profile_id}: Found 'Requested' in final scan for {username} - checking if account is public...")
                    is_public = self.check_if_public_account()

                    if is_public is True:
                        # Public account showing "Requested" = Follow block
                        logger.error(
                            f"Profile No.{self.profile_id}: ❌ FOLLOW BLOCK detected for {username} - public account showing 'Requested' (final scan)")
                        self.is_follow_blocked = True
                        update_airtable_status(self.profile_id, 'Follow Block')
                        return False
                    elif is_public is False:
                        # Private account showing "Requested" = Normal behavior
                        logger.info(
                            f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - private account showing 'Requested' (final scan)")
                        return True
                    else:
                        # Cannot determine account type - treat as success but log warning
                        logger.warning(
                            f"Profile No.{self.profile_id}: ⚠️ Follow result UNCERTAIN for {username} - cannot determine if account is public/private (final scan)")
                        return True
                elif 'Follow' in relevant_buttons:
                    logger.error(
                        f"Profile No.{self.profile_id}: ❌ Follow FAILED for {username} - Follow button still present")
                    return False
                else:
                    # No Follow, Following, or Requested button found
                    # Check if we can see other profile elements (Message button, follower count, etc.)
                    if 'Message' in relevant_buttons:
                        logger.info(
                            f"Profile No.{self.profile_id}: ✅ Follow SUCCESS for {username} - Follow button disappeared, profile loaded")
                        return True
                    else:
                        logger.warning(
                            f"Profile No.{self.profile_id}: ❓ Follow UNCERTAIN for {username} - page may not have loaded properly")
                        return False

            except Exception as e:
                logger.warning(f"Profile No.{self.profile_id}: Error in final verification: {str(e)[:100]}...")
                return False

        except Exception as e:
            logger.warning(
                f"Profile No.{self.profile_id}: Error checking follow success for {username}: {str(e)[:100]}...")
            return False

    def _update_local_profile_status(self):
        """Update local profile status for dashboard display"""
        try:
            from dashboard_controller import profiles, profiles_lock
            pid_str = str(self.profile_id)
            
            # Update status directly in profiles cache (like "Running" does)
            with profiles_lock:
                if pid_str in profiles:
                    profiles[pid_str]['status'] = 'Blocked'
                    profiles[pid_str]['stop_requested'] = True
                    profiles[pid_str]['airtable_status'] = 'Follow Block'
                    logger.info(f"Profile No.{self.profile_id}: Updated local profile status to Blocked directly")
                else:
                    logger.warning(f"Profile No.{self.profile_id}: Not found in profiles cache")
            
            # Also call StatusManager for Airtable update
            from dashboard_controller import StatusManager
            StatusManager.mark_profile_blocked(self.profile_id)
            logger.info(f"Profile No.{self.profile_id}: Updated local profile status to Blocked using StatusManager")
        except Exception as e:
            logger.debug(f"Profile No.{self.profile_id}: Could not update local profile status: {e}")

    def check_for_follow_restriction_modal(self):
        """Check for the 'Try Again Later' modal that appears when follow limit is reached"""
        try:
            # Check if browser window is available and attempt recovery if needed
            if not self.check_and_recover_window():
                logger.warning(f"Profile No.{self.profile_id}: Cannot check for follow restriction modal - browser unavailable")
                return False

            logger.info(f"Profile No.{self.profile_id}: Checking for follow restriction modal...")

            # First: Quick JavaScript check for modal (fastest method)
            try:
                modal_detected = self.driver.execute_script("""
                    // Look for Instagram modal structure
                    const dialog = document.querySelector('div[role="dialog"]');
                    if (dialog && dialog.innerText.includes('Try Again Later')) {
                        return true;
                    }
                    
                    // Fallback: look for any h1 with "Try Again Later"
                    const h1 = document.querySelector('h1');
                    if (h1 && h1.innerText.includes('Try Again Later')) {
                        return true;
                    }
                    
                    return false;
                """)
                
                if modal_detected:
                    logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected via JavaScript")
                    self.is_follow_blocked = True
                    update_airtable_status(self.profile_id, 'Follow Block')
                    self._update_local_profile_status()
                    return True
            except Exception as e:
                logger.debug(f"Profile No.{self.profile_id}: JavaScript check failed: {str(e)[:50]}")

            # Wait a bit for modal to appear (it might be loaded dynamically)
            time.sleep(0.2)  # Reduced to 0.2 seconds for faster detection

            # First try to find modal elements with WebDriverWait for better reliability
            try:
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC
                
                # Wait up to 3 seconds for modal to appear (increased for better detection)
                wait = WebDriverWait(self.driver, 2)
                
                # Instagram modal selectors based on actual structure (optimized for speed)
                modal_selectors = [
                    # Primary: Look for dialog role with h1 containing "Try Again Later"
                    "//div[@role='dialog']//h1[contains(text(), 'Try Again Later')]",
                    "//div[@role='presentation']//h1[contains(text(), 'Try Again Later')]",
                    
                    # Secondary: Look for any h1 with "Try Again Later" (most common)
                    "//h1[contains(text(), 'Try Again Later')]",
                    
                    # Tertiary: Look for dialog structure with modal text
                    "//div[@role='dialog'][contains(., 'Try Again Later')]",
                    "//div[@role='presentation'][contains(., 'Try Again Later')]",
                    
                    # Fallback: Look for buttons that appear in this modal (only if in dialog)
                    "//div[@role='dialog']//button[contains(text(), 'OK')]",
                    "//div[@role='dialog']//button[contains(text(), 'Report a problem')]",
                    
                    # Generic fallback - only if it contains the main text
                    "//*[contains(text(), 'Try Again Later')]"
                ]
                
                for selector in modal_selectors:
                    try:
                        element = wait.until(EC.presence_of_element_located((By.XPATH, selector)))
                        if element and element.is_displayed():
                            logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected via WebDriverWait with selector: {selector}")
                            logger.error(f"Profile No.{self.profile_id}: Element text: {element.text[:200]}")
                            self.is_follow_blocked = True
                            logger.error(f"Profile No.{self.profile_id}: Setting is_follow_blocked = True")
                            update_airtable_status(self.profile_id, 'Follow Block')
                            # Update local profile status for dashboard
                            self._update_local_profile_status()
                            return True
                    except TimeoutException:
                        continue
                    except Exception as e:
                        logger.debug(f"Profile No.{self.profile_id}: Error with WebDriverWait selector {selector}: {str(e)[:50]}")
                        continue
                        
            except Exception as e:
                logger.debug(f"Profile No.{self.profile_id}: WebDriverWait failed: {str(e)[:50]}")

            # Fallback: Check page source for modal text (optimized for Instagram structure)
            page_source = self.driver.page_source.lower()
            modal_indicators = [
                # Primary indicators from actual Instagram modal
                "try again later",
                "we limit how often you can do certain things on instagram",
                "like following people, to protect our community",
                "report a problem"
            ]

            # Check if we have the main indicator
            found_indicators = 0
            for indicator in modal_indicators:
                if indicator in page_source:
                    found_indicators += 1

            # Only if we find the main "try again later" text OR multiple indicators
            if "try again later" in page_source or found_indicators >= 2:
                logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected via page content (found {found_indicators} indicators)")
                
                # Save page source for debugging
                try:
                    with open(f'modal_debug_{self.profile_id}.html', 'w', encoding='utf-8') as f:
                        f.write(self.driver.page_source)
                    logger.error(f"Profile No.{self.profile_id}: Saved page source to modal_debug_{self.profile_id}.html")
                except Exception as e:
                    logger.error(f"Profile No.{self.profile_id}: Could not save page source: {e}")
                
                self.is_follow_blocked = True
                logger.error(f"Profile No.{self.profile_id}: Setting is_follow_blocked = True")
                update_airtable_status(self.profile_id, 'Follow Block')
                # Update local profile status for dashboard
                self._update_local_profile_status()
                return True

            # Quick final check using Instagram modal structure
            try:
                # Check for h1 with "Try Again Later" (most reliable)
                element = self.driver.find_element(By.XPATH, "//h1[contains(text(), 'Try Again Later')]")
                if element and element.is_displayed():
                    logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected via h1 final check")
                    self.is_follow_blocked = True
                    update_airtable_status(self.profile_id, 'Follow Block')
                    self._update_local_profile_status()
                    return True
            except NoSuchElementException:
                pass
            except Exception as e:
                logger.debug(f"Profile No.{self.profile_id}: Error in h1 final check: {str(e)[:50]}")
            
            # Additional final check for dialog structure
            try:
                element = self.driver.find_element(By.XPATH, "//div[@role='dialog'][contains(., 'Try Again Later')]")
                if element and element.is_displayed():
                    logger.error(f"Profile No.{self.profile_id}: Follow restriction modal detected via dialog final check")
                    self.is_follow_blocked = True
                    update_airtable_status(self.profile_id, 'Follow Block')
                    self._update_local_profile_status()
                    return True
            except NoSuchElementException:
                pass
            except Exception as e:
                logger.debug(f"Profile No.{self.profile_id}: Error in dialog final check: {str(e)[:50]}")

            logger.debug(f"Profile No.{self.profile_id}: No follow restriction modal found")
            return False

        except Exception as e:
            logger.warning(f"Profile No.{self.profile_id}: Error checking for follow restriction modal: {str(e)[:100]}...")
            return False

    def check_for_follow_block(self):
        """Check if the account has received a follow block"""
        try:
            # Check if browser window is available and attempt recovery if needed
            if not self.check_and_recover_window():
                logger.warning(f"Profile No.{self.profile_id}: Cannot check for follow block - browser unavailable")
                return False  # Don't treat window issues as follow blocks

            # First check for the specific follow restriction modal
            if self.check_for_follow_restriction_modal():
                return True

            current_url = self.driver.current_url
            page_source = self.driver.page_source.lower()

            # Check for follow block indicators in page source
            follow_block_indicators = [
                "action blocked",
                "we restrict certain activity",
                "temporarily blocked",
                "slow down",
                "too many requests"
            ]

            for indicator in follow_block_indicators:
                if indicator in page_source:
                    logger.error(f"Profile No.{self.profile_id}: Follow block detected via page content: '{indicator}'")
                    update_airtable_status(self.profile_id, 'Follow Block')
                    return True

            # Check for follow block dialog/popup elements
            block_selectors = [
                "//div[contains(text(), 'Action Blocked')]",
                "//h2[contains(text(), 'Action Blocked')]",
                "//*[contains(text(), 'temporarily blocked')]",
                "//*[contains(text(), 'slow down')]"
            ]

            for selector in block_selectors:
                try:
                    element = self.driver.find_element(By.XPATH, selector)
                    if element:
                        logger.error(f"Profile No.{self.profile_id}: Follow block detected via page element")
                        update_airtable_status(self.profile_id, 'Follow Block')
                        return True
                except NoSuchElementException:
                    continue

            return False

        except Exception as e:
            logger.warning(f"Profile No.{self.profile_id}: Error checking follow block: {str(e)[:100]}...")
            return False

    def follow_user(self, username, fast_mode=True, delay_config=None):
        """Follow a specific Instagram user with configurable delays"""
        try:
            # Use delay config or defaults
            if delay_config is None:
                delay_config = {}

            page_load_wait = delay_config.get('page_load_wait', [0.5, 2])
            follow_check_timeout = delay_config.get('follow_check_timeout', 8)

            # Check if browser window is available and attempt recovery if needed
            if not self.check_and_recover_window():
                return False

            # Navigate to user profile
            profile_url = f"https://www.instagram.com/{username}/"
            self.driver.get(profile_url)

            # Configurable page load wait time
            wait_time = random.uniform(page_load_wait[0], page_load_wait[1]) if not fast_mode else page_load_wait[0]
            time.sleep(wait_time)

            # Check if profile exists
            if "Sorry, this page isn't available" in self.driver.page_source:
                logger.warning(f"Profile No.{self.profile_id}: User {username} not found")
                return False

            # Look for follow button
            follow_selectors = [
                "//button[contains(text(), 'Follow') and not(contains(text(), 'Following'))]",
                "//button[contains(@class, '_acan') and contains(text(), 'Follow') and not(contains(text(), 'Following'))]",
                "//div[contains(text(), 'Follow') and not(contains(text(), 'Following'))]//parent::button",
                "//button[@type='button' and contains(., 'Follow') and not(contains(., 'Following'))]",
                "//button[text()='Follow']",
                "//*[contains(@class, 'x1i10hfl') and contains(text(), 'Follow')]",
                "//button[contains(@class, 'x9f619') and contains(text(), 'Follow')]"
            ]

            # Much faster timeout
            timeout = 1 if fast_mode else 2
            follow_button = None

            # Try to find the button quickly
            for selector in follow_selectors:
                try:
                    follow_button = WebDriverWait(self.driver, timeout).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    break
                except TimeoutException:
                    continue

            # If WebDriverWait fails, try direct element finding
            if not follow_button:
                try:
                    follow_button = self.driver.find_element(By.XPATH,
                                                             "//button[contains(text(), 'Follow') and not(contains(text(), 'Following'))]")
                except NoSuchElementException:
                    pass

            if follow_button:
                # Quick check if already following
                button_text = follow_button.text.lower()
                if "following" in button_text or "requested" in button_text:
                    logger.info(f"Profile No.{self.profile_id}: Already following or requested {username}")
                    self.consecutive_follow_errors = 0  # Reset error counter on success
                    return True

                # Click follow button
                follow_button.click()
                logger.info(f"Profile No.{self.profile_id}: Clicked follow button for {username}")

                # Check for follow restriction modal after clicking (optimized)
                time.sleep(0.5)  # Reduced wait time
                if self.check_for_follow_restriction_modal():
                    logger.error(f"Profile No.{self.profile_id}: Follow restriction modal appeared after clicking follow for {username}")
                    self.is_follow_blocked = True
                    return False
                
                # Additional check after 2 seconds
                time.sleep(1.0)
                if self.check_for_follow_restriction_modal():
                    logger.error(f"Profile No.{self.profile_id}: Follow restriction modal appeared 2 seconds after clicking follow for {username}")
                    self.is_follow_blocked = True
                    return False

                # ENHANCED: Check if follow action was successful with new public account follow block detection
                follow_success = self.check_follow_action_success(username, max_wait_time=follow_check_timeout)

                if follow_success:
                    logger.info(f"Profile No.{self.profile_id}: ✅ Successfully followed {username}")
                    self.consecutive_follow_errors = 0  # Reset error counter on success
                    self.consecutive_follow_blocks = 0  # Reset block counter on success

                    # Minimal delay after successful follow
                    if fast_mode:
                        time.sleep(0.2)
                    else:
                        time.sleep(random.uniform(0.5, 1))
                    return True
                else:
                    logger.error(
                        f"Profile No.{self.profile_id}: ❌ Follow action FAILED for {username} - checking for blocks")
                    self.consecutive_follow_blocks += 1

                    # Check for follow block after failed follow attempt
                    if self.check_for_follow_block():
                        self.is_follow_blocked = True
                        logger.error(f"Profile No.{self.profile_id}: FOLLOW BLOCK detected! Stopping this profile.")
                        return False

                    # If 3 consecutive follow blocks without explicit block detection, assume blocked
                    if self.consecutive_follow_blocks >= 3:
                        logger.error(
                            f"Profile No.{self.profile_id}: 3 consecutive follow failures - likely FOLLOW BLOCKED! Stopping this profile.")
                        self.is_follow_blocked = True
                        update_airtable_status(self.profile_id, 'Follow Block')
                        return False

                    return False
            else:
                logger.warning(f"Profile No.{self.profile_id}: Follow button not found for {username}")
                self.consecutive_follow_errors += 1

                # Check for suspension after 3 consecutive errors
                if self.consecutive_follow_errors >= 3:
                    logger.warning(
                        f"Profile No.{self.profile_id}: 3 consecutive follow button errors. Checking for suspension...")
                    if self.check_if_suspended():
                        self.is_suspended = True
                        logger.error(f"Profile No.{self.profile_id}: ACCOUNT SUSPENDED! Stopping this profile.")
                        return False
                    else:
                        logger.info(f"Profile No.{self.profile_id}: Not suspended, resetting error counter")
                        self.consecutive_follow_errors = 0  # Reset if not suspended

                return False

        except Exception as e:
            logger.error(f"Profile No.{self.profile_id}: Error following {username}: {str(e)[:100]}...")
            return False

    def get_next_username(self, filename="usernames.txt"):
        """Get the next username from file and remove it atomically"""
        with file_lock:  # Ensure thread-safe file operations
            try:
                # Get the directory where the script is located
                script_dir = os.path.dirname(os.path.abspath(__file__))
                file_path = os.path.join(script_dir, filename)

                if not os.path.exists(file_path):
                    return None

                # Read all usernames from file
                with open(file_path, 'r', encoding='utf-8') as file:
                    lines = file.readlines()

                # Filter out empty lines and get non-empty usernames
                usernames = [line.strip() for line in lines if line.strip()]

                if not usernames:
                    return None

                # Get the first username
                next_username = usernames[0]

                # Write back the remaining usernames (excluding the first one)
                remaining_usernames = usernames[1:]
                with open(file_path, 'w', encoding='utf-8') as file:
                    for username in remaining_usernames:
                        file.write(username + '\n')

                logger.info(
                    f"Profile No.{self.profile_id}: Got username '{next_username}' from file. {len(remaining_usernames)} remaining.")
                return next_username

            except Exception as e:
                logger.error(f"Profile No.{self.profile_id}: Error getting next username: {str(e)}")
                return None

    def follow_users_continuously(self, delay_range=(5, 10), fast_mode=True, max_follows=None, delay_config=None):
        """Continuously follow users from the file until no more usernames available"""
        results = {
            'successful': [],
            'failed': [],
            'total_processed': 0,
            'suspension_stopped': False,
            'follow_block_stopped': False
        }

        logger.info(
            f"Profile No.{self.profile_id}: Starting continuous following (Fast Mode: {fast_mode}, Max Follows: {max_follows})")

        follow_count = 0
        while True:
            # Check if account is suspended
            if self.is_suspended:
                logger.error(f"Profile No.{self.profile_id}: Account is suspended. Stopping bot for this profile.")
                results['suspension_stopped'] = True
                break

            # Check if account is follow blocked
            if self.is_follow_blocked:
                logger.error(f"Profile No.{self.profile_id}: Account is follow blocked. Stopping bot for this profile.")
                logger.error(f"Profile No.{self.profile_id}: is_follow_blocked = {self.is_follow_blocked}")
                results['follow_block_stopped'] = True
                break

            # Removed: Additional check for follow restriction modal at the start of each loop
            # This was causing performance issues - modal check is already done in follow_user()

            # Check if we've reached the maximum number of follows
            if max_follows and follow_count >= max_follows:
                logger.info(f"Profile No.{self.profile_id}: Reached maximum follow limit ({max_follows})")
                break

            # Get next username from file
            username = self.get_next_username()
            if not username:
                logger.info(f"Profile No.{self.profile_id}: No more usernames available in file")
                break

            logger.info(
                f"Profile No.{self.profile_id}: Processing username: {username} ({follow_count + 1}/{max_follows if max_follows else '∞'})")

            success = self.follow_user(username, fast_mode=fast_mode, delay_config=delay_config)
            results['total_processed'] += 1
            follow_count += 1

            if success:
                results['successful'].append(username)
            else:
                results['failed'].append(username)

                # If account is suspended or follow blocked, break the loop
                if self.is_suspended or self.is_follow_blocked:
                    break

            # Add delay between follows (skip if suspended or blocked)
            if not self.is_suspended and not self.is_follow_blocked:
                if fast_mode:
                    delay = random.uniform(delay_range[0], delay_range[1])
                else:
                    delay = random.uniform(30, 60)

                logger.info(f"Profile No.{self.profile_id}: Waiting {delay:.1f} seconds before next follow...")

                # During the delay, periodically check for follow blocks
                delay_start = time.time()
                while time.time() - delay_start < delay:
                    # Only check for follow blocks if we can access the browser
                    try:
                        if self.driver and self.driver.current_window_handle:
                            # Check for follow block every 2 seconds during delay
                            if time.time() - delay_start >= 2:
                                if self.check_for_follow_block():
                                    self.is_follow_blocked = True
                                    logger.error(
                                        f"Profile No.{self.profile_id}: Follow block detected during delay period!")
                                    break
                    except Exception:
                        # Browser window not available - don't treat as follow block
                        logger.debug(f"Profile No.{self.profile_id}: Browser window not available during delay check")
                        pass
                    time.sleep(0.5)  # Small sleep to prevent excessive checking

        return results

    def run(self, delay_range=(5, 10), fast_mode=True, max_follows=None, delay_config=None):
        """Main method to run the bot"""
        try:
            # Start profile
            if not self.start_profile():
                return None

            # Connect to browser
            if not self.connect_to_browser():
                self.stop_profile()
                return None

            # Navigate to Instagram
            if not self.navigate_to_instagram():
                self.stop_profile()
                return None

            # Follow users continuously
            results = self.follow_users_continuously(delay_range, fast_mode, max_follows, delay_config)

            # Log results
            logger.info(f"Profile No.{self.profile_id}: Bot completed. Results: {results}")

            return results

        except Exception as e:
            logger.error(f"Profile No.{self.profile_id}: Error in main bot execution: {str(e)}")
            return None
        finally:
            # Always stop the profile
            self.stop_profile()


def load_profiles_from_file(filename="adspowerprofiles.txt"):
    """Load profile Numbers from a text file"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)

        if not os.path.exists(file_path):
            logger.error(f"File '{filename}' not found in script directory: {script_dir}")
            logger.info(f"Please create '{filename}' in the same folder as this script")
            return []

        with open(file_path, 'r', encoding='utf-8') as file:
            profile_numbers = [line.strip() for line in file.readlines() if line.strip()]

        logger.info(f"Loaded {len(profile_numbers)} profile Numbers from '{filename}'")

        if profile_numbers:
            logger.info(f"Profile Numbers: {profile_numbers}")

        return profile_numbers

    except Exception as e:
        logger.error(f"Error loading profile Numbers from file: {str(e)}")
        return []


def check_usernames_file(filename="usernames.txt"):
    """Check if usernames file exists and has content"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)

        if not os.path.exists(file_path):
            return 0

        with open(file_path, 'r', encoding='utf-8') as file:
            usernames = [line.strip() for line in file.readlines() if line.strip()]

        return len(usernames)

    except Exception as e:
        logger.error(f"Error checking usernames file: {str(e)}")
        return 0


def run_single_profile(profile_number, delay_range, fast_mode, max_follows_per_profile, delay_config=None):
    """Run bot for a single profile (to be used in threading)"""
    logger.info(f"Starting bot for profile No.{profile_number} (Max follows: {max_follows_per_profile})")

    # Initialize bot for this profile
    bot = InstagramFollowBot(profile_id=profile_number)

    # Run the bot
    results = bot.run(
        delay_range=delay_range,
        fast_mode=fast_mode,
        max_follows=max_follows_per_profile,
        delay_config=delay_config
    )

    if results:
        logger.info(
            f"Profile No.{profile_number} completed: {len(results['successful'])} successful, {len(results['failed'])} failed")
    else:
        logger.error(f"Profile No.{profile_number} failed to run")

    return {
        'profile_id': profile_number,
        'results': results
    }


if __name__ == "__main__":
    print("=" * 60)
    print("MULTI-PROFILE INSTAGRAM FOLLOW BOT WITH ENHANCED FOLLOW BLOCK DETECTION")
    print("INCLUDING 'TRY AGAIN LATER' MODAL DETECTION")
    print("=" * 60)

    # Load profiles from file
    profile_numbers = load_profiles_from_file("adspowerprofiles.txt")

    if not profile_numbers:
        print("\n" + "=" * 50)
        print("ERROR: No profile Numbers loaded!")
        print("=" * 50)
        print("Please create an 'adspowerprofiles.txt' file in the same folder as this script.")
        print("Put one AdsPower profile Number per line in the file.")
        print("\nExample adspowerprofiles.txt content:")
        print("1")
        print("2")
        print("5")
        print("10")
        print("=" * 50)
        input("\nPress Enter to exit...")
        sys.exit(1)

    # Check usernames file
    username_count = check_usernames_file("usernames.txt")
    if username_count == 0:
        print("\n" + "=" * 50)
        print("ERROR: No usernames found!")
        print("=" * 50)
        print("Please create a 'usernames.txt' file in the same folder as this script.")
        print("Put one Instagram username per line in the file.")
        print("=" * 50)
        input("\nPress Enter to exit...")
        sys.exit(1)

    print(f"\nLoaded {len(profile_numbers)} profiles and {username_count} usernames")
    print("Profile Numbers:", profile_numbers)

    # Check if running on Windows
    if sys.platform != "win32":
        logger.warning("This script is optimized for Windows")

    # Load delay configuration
    import json

    try:
        with open('config.json', 'r') as f:
            delay_config = json.load(f).get('delays', {})
        print(f"\nLoaded custom delay configuration from config.json")
    except:
        delay_config = {
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
        }
        print(f"\nUsing default delay configuration")

    # Configuration
    DELAY_RANGE = delay_config.get('between_follows', [8, 20])
    FAST_MODE = True
    MIN_FOLLOWS_PER_PROFILE = 40
    MAX_FOLLOWS_PER_PROFILE = 60
    PROFILE_START_DELAY = delay_config.get('profile_start_delay', 3)

    print(f"\nConfiguration:")
    print(f"- Delay between follows: {DELAY_RANGE[0]}-{DELAY_RANGE[1]} seconds")
    print(f"- Fast mode: {FAST_MODE}")
    print(f"- Follows per profile: {MIN_FOLLOWS_PER_PROFILE}-{MAX_FOLLOWS_PER_PROFILE} (random)")
    print(f"- Profile start delay: {PROFILE_START_DELAY} seconds")
    print(f"- Page load wait: {delay_config.get('page_load_wait', [0.5, 2])} seconds")
    print(f"- Follow check timeout: {delay_config.get('follow_check_timeout', 8)} seconds")
    print(f"- NEW: Enhanced follow block detection for public accounts showing 'Requested'")
    print(f"- NEW: 'Try Again Later' modal detection for follow restrictions")

    # Ask for confirmation
    print("\n" + "=" * 50)
    confirm = input("Start the multi-profile bot? (y/n): ").lower().strip()
    if confirm != 'y':
        print("Bot cancelled.")
        sys.exit(0)

    print("\n" + "=" * 50)
    print("STARTING MULTI-PROFILE BOT WITH ENHANCED FOLLOW BLOCK DETECTION")
    print("=" * 50)

    # Start profiles with delay and run them in parallel
    threads = []
    all_results = []

    for i, profile_number in enumerate(profile_numbers):
        if i > 0:  # Don't delay before the first profile
            logger.info(f"Waiting {PROFILE_START_DELAY} seconds before starting next profile...")
            time.sleep(PROFILE_START_DELAY)

        # Generate random number of follows for this profile (between 40-60)
        follows_for_this_profile = random.randint(MIN_FOLLOWS_PER_PROFILE, MAX_FOLLOWS_PER_PROFILE)
        logger.info(f"Profile No.{profile_number} will follow {follows_for_this_profile} accounts")

        # Start each profile in a separate thread
        thread = threading.Thread(
            target=lambda pnum=profile_number, follows=follows_for_this_profile: all_results.append(
                run_single_profile(pnum, DELAY_RANGE, FAST_MODE, follows, delay_config)
            )
        )
        thread.start()
        threads.append(thread)

        logger.info(f"Started thread for profile No.{profile_number}")

    # Wait for all threads to complete
    logger.info("Waiting for all profiles to complete...")
    for thread in threads:
        thread.join()

    # Print final results
    print("\n" + "=" * 60)
    print("ALL PROFILES COMPLETED")
    print("=" * 60)

    total_successful = 0
    total_failed = 0
    total_processed = 0

    for result in all_results:
        profile_number = result['profile_id']
        profile_results = result['results']

        if profile_results:
            successful = len(profile_results['successful'])
            failed = len(profile_results['failed'])
            processed = profile_results['total_processed']
            suspended = profile_results.get('suspension_stopped', False)
            follow_blocked = profile_results.get('follow_block_stopped', False)

            print(f"\nProfile No.{profile_number}:")
            print(f"  ✓ Successfully followed: {successful}")
            print(f"  ✗ Failed: {failed}")
            print(f"  📊 Total processed: {processed}")
            if suspended:
                print(f"  ⚠️  STOPPED: Account was suspended")
            if follow_blocked:
                print(f"  🚫 STOPPED: Account was follow blocked")

            total_successful += successful
            total_failed += failed
            total_processed += processed
        else:
            print(f"\nProfile No.{profile_number}: FAILED TO RUN")

    print(f"\n" + "=" * 40)
    print("OVERALL SUMMARY")
    print("=" * 40)
    print(f"Total profiles run: {len(profile_numbers)}")
    print(f"Total successfully followed: {total_successful}")
    print(f"Total failed: {total_failed}")
    print(f"Total processed: {total_processed}")
    print(f"Average follows per profile: {total_successful / len(profile_numbers):.1f}")

    # Check remaining usernames
    remaining_usernames = check_usernames_file("usernames.txt")
    print(f"Remaining usernames in file: {remaining_usernames}")

    print("=" * 60)
    print("ENHANCED FOLLOW BLOCK DETECTION FEATURES:")
    print("✅ Traditional follow block detection (Action Blocked, etc.)")
    print("✅ NEW: 'Try Again Later' modal detection")
    print("✅ NEW: Public account 'Requested' button detection")
    print("✅ Private account 'Requested' button (normal behavior)")
    print("✅ Automatic Airtable status updates")
    print("✅ Multiple detection points for maximum accuracy")
    print("✅ Smart public/private account differentiation")
    print("✅ Modal detection at multiple checkpoints")
    print("=" * 60)
    input("\nPress Enter to exit...")