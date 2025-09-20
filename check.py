import requests
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import json
import os
import urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import concurrent.futures
import pyotp
import random
import re

# Try to import webdriver_manager for automatic ChromeDriver management
try:
    from webdriver_manager.chrome import ChromeDriverManager

    USE_WEBDRIVER_MANAGER = True
except ImportError:
    USE_WEBDRIVER_MANAGER = False

# Dynamic path - looks for chromedriver in script folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMEDRIVER_PATH = os.path.join(SCRIPT_DIR, "chromedrivers", "chromedriver.exe")


class InstagramStatusChecker:
    def __init__(self):
        self.adspower_api = "http://local.adspower.net:50325"
        self.airtable_token = "pat8jWSIvK699kYmQ.3629ac44cda2a5252993a5e370e4105d45e9b19b98b4fd7ca9f133349fdb1125"
        self.airtable_base_id = "appvCCZMuHb5V1o57"
        self.airtable_table_name = "AdsPowers2"
        self.profile_start_delay = 5  # 5 second delay between starting profiles
        self.batch_size = 50  # Batch size for concurrent processing
        self.thread_lock = threading.Lock()  # For thread-safe operations
        self.browser_lock = threading.Lock()  # For browser instance synchronization
        self.detection_lock = threading.Lock()  # For detection method synchronization
        self.daisysms_api_key = "rvBQZ3OgmbMp0O2lA7TcffH2dKQmsz"  # DaisySMS API key
        self.daisysms_base_url = "https://daisysms.com/stubs/handler_api.php"

    def get_profile_selection_method(self):
        """Ask user whether to use range input or Airtable view"""
        print("\n📋 Select profile processing method:")
        print("1. Enter profile range (e.g., 2090-2100)")
        print("2. Process all profiles from 'Logged Out' view")

        while True:
            try:
                choice = input("\nEnter your choice (1 or 2): ").strip()
                if choice == '1':
                    return 'range'
                elif choice == '2':
                    return 'view'
                else:
                    print("❌ Please enter 1 or 2")
            except KeyboardInterrupt:
                print("\n👋 Exiting...")
                exit()

    def get_concurrent_limit(self, total_profiles):
        """Get the maximum number of concurrent profiles from user"""
        print(f"\n🔢 Total profiles to process: {total_profiles}")
        print(f"Default concurrent limit: {self.batch_size}")

        while True:
            try:
                limit_input = input(f"\nEnter maximum concurrent profiles (1-{min(total_profiles, 100)}): ").strip()
                if not limit_input:
                    return self.batch_size

                limit = int(limit_input)
                if 1 <= limit <= min(total_profiles, 100):
                    return limit
                else:
                    print(f"❌ Please enter a number between 1 and {min(total_profiles, 100)}")
            except ValueError:
                print("❌ Please enter a valid number")
            except KeyboardInterrupt:
                print("\n👋 Exiting...")
                exit()

    def get_profile_range_input(self):
        """Get profile range from user input"""
        while True:
            try:
                range_input = input("\nEnter profile range (e.g., 2090-2100): ").strip()

                if '-' in range_input:
                    start, end = range_input.split('-')
                    start_id = int(start.strip())
                    end_id = int(end.strip())

                    if start_id > end_id:
                        print("❌ Start profile ID must be less than or equal to end profile ID")
                        continue

                    return start_id, end_id
                else:
                    # Single profile
                    profile_id = int(range_input)
                    return profile_id, profile_id

            except ValueError:
                print("❌ Invalid format. Use format like: 2090-2100 or 2090")
            except KeyboardInterrupt:
                print("\n👋 Exiting...")
                exit()

    def get_profiles_from_airtable(self, start_id, end_id):
        """Get profiles from Airtable that are in the specified range"""
        try:
            print(f"\n🔍 Searching Airtable for profiles in range {start_id}-{end_id}...")

            headers = {
                'Authorization': f'Bearer {self.airtable_token}',
                'Content-Type': 'application/json'
            }

            encoded_table_name = urllib.parse.quote(self.airtable_table_name)
            base_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{encoded_table_name}"

            all_profiles = []
            offset = None

            # Fetch all records with pagination
            while True:
                params = {}
                if offset:
                    params['offset'] = offset

                response = requests.get(base_url, headers=headers, params=params)

                if response.status_code != 200:
                    print(f"❌ Failed to fetch Airtable records: HTTP {response.status_code}")
                    print(f"Response: {response.text}")
                    return []

                data = response.json()
                records = data.get('records', [])

                if not records:
                    break

                all_profiles.extend(records)

                # Check for more pages
                offset = data.get('offset')
                if not offset:
                    break

                print(f"📄 Fetched {len(records)} records, continuing...")
                time.sleep(0.2)  # Small delay to be nice to Airtable API

            print(f"📊 Total records fetched from Airtable: {len(all_profiles)}")

            # Filter profiles in the specified range
            profiles_in_range = []

            for record in all_profiles:
                try:
                    fields = record.get('fields', {})

                    # Try different possible field names for profile number
                    profile_number = None
                    username = None
                    password = None
                    twofa_secret = None
                    login_credentials = None

                    possible_profile_fields = ['Profile', 'Profile Number', 'AdsPower Profile', 'Profile ID', 'ID']
                    possible_username_fields = ['Username', 'User', 'Email', 'Account']
                    possible_password_fields = ['Password', 'Pass', 'Pwd']
                    possible_2fa_fields = ['2FA', '2fa', 'TOTP', 'Authenticator', 'Secret']
                    possible_login_fields = ['Login', 'Credentials', 'LoginInfo']

                    for field_name in possible_profile_fields:
                        if field_name in fields:
                            profile_number = fields[field_name]
                            break

                    # Check for Login column first (username:password format)
                    for field_name in possible_login_fields:
                        if field_name in fields:
                            login_credentials = fields[field_name]
                            break

                    # Parse login credentials if found
                    if login_credentials and ':' in login_credentials:
                        try:
                            username, password = login_credentials.split(':', 1)
                            username = username.strip()
                            password = password.strip()
                        except ValueError:
                            pass

                    # Fallback to individual username/password fields if Login column not found
                    if not username:
                        for field_name in possible_username_fields:
                            if field_name in fields:
                                username = fields[field_name]
                                break

                    if not password:
                        for field_name in possible_password_fields:
                            if field_name in fields:
                                password = fields[field_name]
                                break

                    for field_name in possible_2fa_fields:
                        if field_name in fields:
                            twofa_secret = fields[field_name]
                            break

                    if profile_number is not None:
                        # Convert to int if it's a string number
                        if isinstance(profile_number, str) and profile_number.isdigit():
                            profile_num = int(profile_number)
                        elif isinstance(profile_number, int):
                            profile_num = profile_number
                        else:
                            continue

                        if start_id <= profile_num <= end_id:
                            current_status = fields.get('Status', 'Unknown')
                            profiles_in_range.append({
                                'id': str(profile_num),
                                'record_id': record['id'],
                                'current_status': current_status,
                                'username': username or 'Unknown',
                                'password': password or '',
                                '2fa_secret': twofa_secret or ''
                            })
                            print(
                                f"✅ Found profile {profile_num} (Status: {current_status}, User: {username or 'N/A'})")

                except (ValueError, TypeError) as e:
                    continue

            # Sort profiles numerically
            profiles_in_range.sort(key=lambda x: int(x['id']))

            print(f"\n✅ Found {len(profiles_in_range)} profiles in Airtable for range {start_id}-{end_id}")

            if profiles_in_range:
                profile_ids = [p['id'] for p in profiles_in_range]
                if len(profile_ids) <= 10:
                    print(f"📋 Profiles: {', '.join(profile_ids)}")
                else:
                    first_5 = profile_ids[:5]
                    last_5 = profile_ids[-5:]
                    print(f"📋 Profiles: {', '.join(first_5)} ... {', '.join(last_5)}")

            return profiles_in_range

        except Exception as e:
            print(f"❌ Error getting profiles from Airtable: {e}")
            return []

    def get_profiles_from_view(self, view_id="viwWM7u85sJJ6LU1U"):
        """Get profiles from a specific Airtable view"""
        try:
            print(f"\n🔍 Fetching profiles from 'Logged Out' view...")

            headers = {
                'Authorization': f'Bearer {self.airtable_token}',
                'Content-Type': 'application/json'
            }

            encoded_table_name = urllib.parse.quote(self.airtable_table_name)
            base_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{encoded_table_name}"

            all_profiles = []
            offset = None

            # Fetch all records from the specific view
            while True:
                params = {'view': view_id}
                if offset:
                    params['offset'] = offset

                response = requests.get(base_url, headers=headers, params=params)

                if response.status_code != 200:
                    print(f"❌ Failed to fetch Airtable records: HTTP {response.status_code}")
                    print(f"Response: {response.text}")
                    return []

                data = response.json()
                records = data.get('records', [])

                if not records:
                    break

                all_profiles.extend(records)

                # Check for more pages
                offset = data.get('offset')
                if not offset:
                    break

                print(f"📄 Fetched {len(records)} records, continuing...")
                time.sleep(0.2)  # Small delay to be nice to Airtable API

            print(f"📊 Total records fetched from 'Logged Out' view: {len(all_profiles)}")

            # Process the profiles
            profiles_to_process = []

            for record in all_profiles:
                try:
                    fields = record.get('fields', {})

                    # Try different possible field names for profile number
                    profile_number = None
                    username = None
                    password = None
                    twofa_secret = None
                    login_credentials = None

                    possible_profile_fields = ['Profile', 'Profile Number', 'AdsPower Profile', 'Profile ID', 'ID']
                    possible_username_fields = ['Username', 'User', 'Email', 'Account']
                    possible_password_fields = ['Password', 'Pass', 'Pwd']
                    possible_2fa_fields = ['2FA', '2fa', 'TOTP', 'Authenticator', 'Secret']
                    possible_login_fields = ['Login', 'Credentials', 'LoginInfo']

                    for field_name in possible_profile_fields:
                        if field_name in fields:
                            profile_number = fields[field_name]
                            break

                    # Check for Login column first (username:password format)
                    for field_name in possible_login_fields:
                        if field_name in fields:
                            login_credentials = fields[field_name]
                            break

                    # Parse login credentials if found
                    if login_credentials and ':' in login_credentials:
                        try:
                            username, password = login_credentials.split(':', 1)
                            username = username.strip()
                            password = password.strip()
                        except ValueError:
                            pass

                    # Fallback to individual username/password fields if Login column not found
                    if not username:
                        for field_name in possible_username_fields:
                            if field_name in fields:
                                username = fields[field_name]
                                break

                    if not password:
                        for field_name in possible_password_fields:
                            if field_name in fields:
                                password = fields[field_name]
                                break

                    for field_name in possible_2fa_fields:
                        if field_name in fields:
                            twofa_secret = fields[field_name]
                            break

                    if profile_number is not None:
                        # Convert to int if it's a string number
                        if isinstance(profile_number, str) and profile_number.isdigit():
                            profile_num = int(profile_number)
                        elif isinstance(profile_number, int):
                            profile_num = profile_number
                        else:
                            continue

                        current_status = fields.get('Status', 'Unknown')
                        profiles_to_process.append({
                            'id': str(profile_num),
                            'record_id': record['id'],
                            'current_status': current_status,
                            'username': username or 'Unknown',
                            'password': password or '',
                            '2fa_secret': twofa_secret or ''
                        })
                        print(f"✅ Found profile {profile_num} (User: {username or 'N/A'})")

                except (ValueError, TypeError) as e:
                    continue

            # Sort profiles numerically
            profiles_to_process.sort(key=lambda x: int(x['id']))

            print(f"\n✅ Found {len(profiles_to_process)} 'Logged Out' profiles to process")

            if profiles_to_process:
                profile_ids = [p['id'] for p in profiles_to_process]
                if len(profile_ids) <= 10:
                    print(f"📋 Profiles: {', '.join(profile_ids)}")
                else:
                    first_5 = profile_ids[:5]
                    last_5 = profile_ids[-5:]
                    print(f"📋 Profiles: {', '.join(first_5)} ... {', '.join(last_5)}")

            return profiles_to_process

        except Exception as e:
            print(f"❌ Error getting profiles from view: {e}")
            return []

    def start_adspower_profile(self, profile_id):
        """Start AdsPower profile"""
        try:
            url = f"{self.adspower_api}/api/v1/browser/start"
            params = {
                "serial_number": profile_id,
                "launch_args": "",
                "headless": 0,
                "disable_password_filling": 0,
                "clear_cache_after_closing": 0,
                "enable_password_saving": 0
            }

            response = requests.get(url, params=params)
            data = response.json()

            if data.get('code') == 0:
                print(f"✅ Profile {profile_id} started successfully")
                return data
            else:
                error_msg = data.get('msg', 'Unknown error')
                if any(keyword in error_msg.lower() for keyword in [
                    'already', 'running', 'opened', 'started'
                ]):
                    print(f"⚠️  Profile {profile_id} is already running. Attempting to connect...")
                    return self.get_running_profile_info(profile_id)
                else:
                    print(f"❌ Failed to start profile {profile_id}: {error_msg}")
                    return None

        except Exception as e:
            print(f"❌ Error starting profile {profile_id}: {e}")
            return None

    def get_running_profile_info(self, profile_id):
        """Get connection info for an already running profile"""
        try:
            url = f"{self.adspower_api}/api/v1/browser/active"
            params = {"serial_number": profile_id}

            response = requests.get(url, params=params)
            data = response.json()

            if data.get('code') == 0 and data.get('data'):
                print(f"✅ Connected to existing session for profile {profile_id}")
                return data
            else:
                print(f"⚠️  Could not get connection info for profile {profile_id}. Attempting restart...")
                return self.restart_profile(profile_id)

        except Exception as e:
            print(f"❌ Error getting running profile info for {profile_id}: {e}")
            return self.restart_profile(profile_id)

    def restart_profile(self, profile_id):
        """Close and restart a profile"""
        try:
            print(f"🔄 Restarting profile {profile_id}...")

            # Stop the profile
            stop_url = f"{self.adspower_api}/api/v1/browser/stop"
            stop_params = {"serial_number": profile_id}
            stop_response = requests.get(stop_url, params=stop_params)

            # Wait for profile to close
            time.sleep(2)

            # Start it again
            start_url = f"{self.adspower_api}/api/v1/browser/start"
            start_params = {
                "serial_number": profile_id,
                "launch_args": "",
                "headless": 0,
                "disable_password_filling": 0,
                "clear_cache_after_closing": 0,
                "enable_password_saving": 0
            }

            start_response = requests.get(start_url, params=start_params)
            start_data = start_response.json()

            if start_data.get('code') == 0:
                print(f"✅ Profile {profile_id} restarted successfully")
                return start_data
            else:
                print(f"❌ Failed to restart profile {profile_id}")
                return None

        except Exception as e:
            print(f"❌ Error restarting profile {profile_id}: {e}")
            return None

    def stop_adspower_profile(self, profile_id):
        """Stop AdsPower profile"""
        try:
            url = f"{self.adspower_api}/api/v1/browser/stop"
            params = {"serial_number": profile_id}

            response = requests.get(url, params=params)
            data = response.json()

            if data.get('code') == 0:
                print(f"⏹️  Profile {profile_id} stopped successfully")
                return True
            else:
                error_msg = data.get('msg', 'Unknown error')
                if any(keyword in error_msg.lower() for keyword in [
                    'not running', 'not started', 'already stopped', 'not found'
                ]):
                    print(f"ℹ️  Profile {profile_id} was already stopped")
                    return True
                else:
                    print(f"❌ Failed to stop profile {profile_id}: {error_msg}")
                    return False

        except Exception as e:
            print(f"❌ Error stopping profile {profile_id}: {e}")
            return False

    def generate_2fa_code(self, secret_key):
        """Generate 2FA code from secret key using TOTP"""
        try:
            if not secret_key or secret_key.strip() == '':
                return None

            # Clean the secret key (remove spaces and convert to uppercase)
            clean_secret = secret_key.replace(' ', '').upper()

            totp = pyotp.TOTP(clean_secret)
            current_code = totp.now()

            print(f"🔐 Generated 2FA code: {current_code}")
            return current_code

        except Exception as e:
            print(f"❌ Error generating 2FA code: {e}")
            return None

    def connect_to_browser(self, adspower_response):
        """Connect to the AdsPower browser"""
        try:
            # Extract debug port from AdsPower response
            debug_info = adspower_response['data']['ws']['selenium']
            if ':' in str(debug_info):
                debug_port = str(debug_info).split(':')[-1]
            else:
                debug_port = str(debug_info)

            chrome_options = Options()
            chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
            
            # Enable 3rd-party cookies for reCAPTCHA to work properly
            chrome_options.add_argument("--disable-features=BlockThirdPartyCookies")
            chrome_options.add_experimental_option("prefs", {
                "profile.default_content_setting_values.cookies": 1,
                "profile.block_third_party_cookies": False
            })

            # Method 1: Try to get ChromeDriver path from AdsPower API response
            chrome_driver_path = adspower_response['data'].get('webdriver')
            if chrome_driver_path:
                print(f"🔧 Using ChromeDriver from AdsPower API: {chrome_driver_path}")
                try:
                    service = Service(chrome_driver_path)
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    print(f"✅ Connected to browser using AdsPower ChromeDriver")
                    return driver
                except Exception as e:
                    print(f"⚠️  AdsPower ChromeDriver failed: {str(e)[:100]}...")

            # Method 2: Use webdriver-manager (if available)
            if USE_WEBDRIVER_MANAGER:
                print(f"🔧 Using webdriver-manager to handle ChromeDriver")
                try:
                    service = Service(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    print(f"✅ Connected using webdriver-manager")
                    return driver
                except Exception as e:
                    print(f"⚠️  webdriver-manager failed: {e}")

            # Method 3: Use ChromeDriver from local chromedrivers folder
            if os.path.exists(CHROMEDRIVER_PATH):
                print(f"🔧 Using ChromeDriver from local folder: {CHROMEDRIVER_PATH}")
                try:
                    service = Service(CHROMEDRIVER_PATH)
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    print(f"✅ Connected using local ChromeDriver")
                    return driver
                except Exception as e:
                    print(f"⚠️  Local ChromeDriver failed: {str(e)[:100]}...")

            # Method 4: Try to use ChromeDriver from PATH
            print(f"🔧 Trying to use ChromeDriver from system PATH")
            try:
                driver = webdriver.Chrome(options=chrome_options)
                print(f"✅ Connected using system PATH ChromeDriver")
                return driver
            except Exception as e:
                print(f"❌ System PATH ChromeDriver failed: {str(e)[:100]}...")

            print(f"❌ All ChromeDriver methods failed!")
            return None

        except Exception as e:
            print(f"❌ Error connecting to browser: {e}")
            return None

    def handle_initial_popups(self, driver, profile_id):
        """Handle initial popups when opening Instagram - SINGLE PASS ONLY"""
        try:
            print(f"🔄 [Profile {profile_id}] Checking for initial popups...")

            current_url = driver.current_url

            # Handle cookie consent page first
            if "/consent/" in current_url and "flow=user_cookie_choice" in current_url:
                print(f"🍪 [Profile {profile_id}] Detected cookie consent page, handling...")
                time.sleep(3)

                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.TAG_NAME, "button"))
                    )
                except TimeoutException:
                    print(f"⚠️ [Profile {profile_id}] Timeout waiting for buttons to load")

                allow_button_found = False

                try:
                    allow_all_selectors = [
                        "//button[normalize-space(text())='Allow all cookies']",
                        "//button[contains(normalize-space(text()), 'Allow all cookies')]",
                        "//button[contains(normalize-space(text()), 'Allow cookies')]",
                        "//button[contains(normalize-space(text()), 'Accept all')]",
                        "//div[@role='button' and contains(normalize-space(text()), 'Allow all cookies')]",
                        "//button[contains(@class, '_acan') and contains(@class, '_acap')]"
                    ]

                    for selector in allow_all_selectors:
                        try:
                            elements = driver.find_elements(By.XPATH, selector)
                            for element in elements:
                                if element.is_displayed() and element.is_enabled():
                                    element_text = element.text.strip()
                                    print(
                                        f"✅ [Profile {profile_id}] Found 'Allow all cookies' button: '{element_text}'")

                                    try:
                                        element.click()
                                        print(f"✅ [Profile {profile_id}] Clicked 'Allow all cookies' button")
                                        allow_button_found = True
                                        break
                                    except Exception as click_error:
                                        try:
                                            driver.execute_script("arguments[0].click();", element)
                                            print(
                                                f"✅ [Profile {profile_id}] Clicked 'Allow all cookies' button (JavaScript)")
                                            allow_button_found = True
                                            break
                                        except Exception as js_error:
                                            continue

                            if allow_button_found:
                                break

                        except Exception as selector_error:
                            continue

                    if not allow_button_found:
                        print(f"🔍 [Profile {profile_id}] Specific button not found, trying general approach...")

                        all_buttons = driver.find_elements(By.TAG_NAME, "button")
                        for element in all_buttons:
                            try:
                                if element.is_displayed() and element.is_enabled():
                                    element_text = element.text.strip()
                                    skip_texts = ['select all', 'settings', 'feedback', 'customize', 'manage']
                                    if any(skip_text in element_text.lower() for skip_text in skip_texts):
                                        continue

                                    consent_keywords = ['allow', 'accept', 'continue', 'agree', 'confirm']
                                    if any(keyword in element_text.lower() for keyword in consent_keywords):
                                        try:
                                            driver.execute_script("arguments[0].click();", element)
                                            print(
                                                f"✅ [Profile {profile_id}] Clicked potential consent button: '{element_text}'")
                                            allow_button_found = True
                                            break
                                        except:
                                            continue

                            except:
                                continue

                    if allow_button_found:
                        time.sleep(5)  # Wait longer for the next page to load
                        print(f"✅ [Profile {profile_id}] Cookie consent handled successfully")
                    else:
                        print(f"❌ [Profile {profile_id}] Could not find suitable consent button")

                except Exception as e:
                    print(f"❌ [Profile {profile_id}] Error handling cookie consent: {e}")

            # Now handle the data processing consent sequence
            time.sleep(2)  # Small delay before checking for next page
            current_url = driver.current_url
            page_source = driver.page_source.lower()

            # Check if we're now on the data processing consent page
            if "/consent/" in current_url and (
                    "ad_free_subscription" in current_url or "choose if we process your data for ads" in page_source):
                print(f"📊 [Profile {profile_id}] Data processing consent page detected, handling...")

                # Step 1: Look for "Get started" button
                if self.click_button_by_text(driver, profile_id, "Get started", ["Get started", "Get Started"]):
                    time.sleep(4)

                # Step 2: Handle the choice page with radio buttons
                page_source = driver.page_source.lower()
                if "you need to make a choice" in page_source or "use free of charge with ads" in page_source:
                    print(f"🔄 [Profile {profile_id}] Found choice page, selecting 'Use free of charge with ads'...")

                    free_ads_selected = False

                    # Try to find and click the third radio button
                    try:
                        all_radios = driver.find_elements(By.XPATH, "//input[@type='radio']")
                        visible_radios = [radio for radio in all_radios if radio.is_displayed() and radio.is_enabled()]

                        if len(visible_radios) >= 3:
                            third_radio = visible_radios[2]
                            driver.execute_script("arguments[0].click();", third_radio)
                            print(f"✅ [Profile {profile_id}] Clicked third radio button")
                            free_ads_selected = True
                        elif len(visible_radios) >= 1:
                            last_radio = visible_radios[-1]
                            driver.execute_script("arguments[0].click();", last_radio)
                            print(f"✅ [Profile {profile_id}] Clicked last available radio button")
                            free_ads_selected = True

                    except Exception as e:
                        print(f"⚠️ [Profile {profile_id}] Failed to find radio buttons: {e}")

                    if free_ads_selected:
                        time.sleep(2)
                        if self.click_button_by_text(driver, profile_id, "Continue", ["Continue"]):
                            time.sleep(4)

                # Step 3: Look for "Agree" button
                current_page = driver.page_source.lower()
                if "agree" in current_page and (
                        "meta processing" in current_page or "terms" in current_page or "data for ads" in current_page):
                    if self.click_button_by_text(driver, profile_id, "Agree", ["Agree"]):
                        time.sleep(4)

                # Step 4: Look for "Not interested" button
                current_page = driver.page_source.lower()
                if "not interested" in current_page or "manage your ad experience" in current_page:
                    if self.click_button_by_text(driver, profile_id, "Not interested",
                                                 ["Not interested", "Not Interested"]):
                        time.sleep(4)

            # Handle other common popups
            popup_handlers = [
                ("Dismiss", "//button[contains(text(), 'Dismiss')]"),
                ("Not now", "//button[contains(text(), 'Not now')]"),
                ("Not Now", "//button[contains(text(), 'Not Now')]"),
                ("OK", "//button[contains(text(), 'OK')]")
            ]

            for popup_name, xpath in popup_handlers:
                try:
                    popup_button = driver.find_element(By.XPATH, xpath)
                    if popup_button.is_displayed():
                        popup_button.click()
                        print(f"✅ [Profile {profile_id}] Clicked '{popup_name}' popup")
                        time.sleep(2)
                except NoSuchElementException:
                    continue
                except Exception as e:
                    continue

            return True

        except Exception as e:
            print(f"❌ [Profile {profile_id}] Error handling initial popups: {e}")
            return False

    def human_type(self, element, text, typing_delay=0.1):
        """Type text character by character with human-like delays"""
        try:
            element.clear()
            time.sleep(0.5)

            for char in text:
                element.send_keys(char)
                time.sleep(random.uniform(typing_delay, typing_delay * 2))

            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"❌ Error during human typing: {e}")
            return False

    def detect_instagram_status_quiet(self, driver, profile_id):
        """Detect Instagram account status with NO RECURSION to prevent infinite loops"""
        try:
            # Only navigate to Instagram if we're not already on a test page
            current_url = driver.current_url.lower()
            if not current_url.startswith("data:text/html") and "instagram.com" not in current_url:
                driver.get("https://www.instagram.com")
                time.sleep(random.uniform(3, 6))
            else:
                time.sleep(random.uniform(1, 2))

            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                return "Error"

            time.sleep(random.uniform(1, 2))

            current_url = driver.current_url.lower()
            page_source = driver.page_source.lower()

            with self.thread_lock:
                print(f"🔍 [Profile {profile_id}] Current URL: {current_url}")

            # PRIORITY 1: Check for HTTP errors and network issues
            if "http error 429" in page_source or "too many requests" in page_source:
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] HTTP 429 error detected")
                return "HTTP 429 Error"

            # PRIORITY 2: Check for Bad Proxy indicators
            bad_proxy_indicators = [
                "this site can't be reached" in page_source,
                "connection timed out" in page_source,
                "network error" in page_source,
                "proxy error" in page_source,
                "dns_probe_finished_nxdomain" in page_source,
                len(driver.find_elements(By.TAG_NAME, "body")) == 0  # Empty page
            ]

            if any(bad_proxy_indicators):
                with self.thread_lock:
                    print(f"🌐 [Profile {profile_id}] Bad Proxy detected")
                return "Bad Proxy"

            # PRIORITY 3: Check for challenge/verification pages FIRST
            if "/challenge/" in current_url:
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Instagram challenge page detected")

                # Check for phone verification specifically
                phone_verification_indicators = [
                    "add phone number to get back into" in page_source,
                    "we will send a confirmation code via sms" in page_source,
                    "phone number" in page_source and "confirmation" in page_source,
                    "send confirmation" in page_source
                ]

                if any(phone_verification_indicators):
                    with self.thread_lock:
                        print(f"📱 [Profile {profile_id}] Phone verification required")
                    return "Phone verification required"

                # Check for other challenge types
                if "suspicious activity" in page_source or "automated behavior" in page_source:
                    with self.thread_lock:
                        print(f"🤖 [Profile {profile_id}] Suspicious activity challenge")
                    return "Challenge required"

                # Generic challenge
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Generic Instagram challenge")
                return "Challenge required"

            # PRIORITY 4: Check for suspension/disabled in URL and content
            suspension_url_indicators = [
                "suspended" in current_url,
                "accounts/suspended" in current_url,
                "disabled" in current_url,
                "accounts/disabled" in current_url
            ]

            suspension_content_indicators = [
                "we disabled your account" in page_source,
                "your account has been suspended" in page_source,
                "account suspended" in page_source,
                "temporarily suspended" in page_source,
                "this account has been disabled" in page_source,
                "account disabled" in page_source,
                "we're sorry, but your account has been disabled" in page_source
            ]

            if any(suspension_url_indicators) or any(suspension_content_indicators):
                with self.thread_lock:
                    print(f"🚫 [Profile {profile_id}] Account banned/suspended detected")
                return "Banned 🔴"

            # PRIORITY 5: Check for Account Compromised
            compromised_indicators = [
                "your account was compromised" in page_source,
                "account compromised" in page_source,
                "we detected suspicious activity" in page_source,
                "suspicious login" in page_source,
                "unusual activity" in page_source and "password" in page_source
            ]

            if any(compromised_indicators):
                with self.thread_lock:
                    print(f"🔒 [Profile {profile_id}] Account compromised detected")
                return "Change Password Checkpoint"

            # PRIORITY 3: Handle consent/cookie pages FIRST (before checking logged-in status)
            if "/consent/" in current_url and (
                    "flow=user_cookie_choice" in current_url or "user_cookie_choice" in current_url):
                with self.thread_lock:
                    print(f"🍪 [Profile {profile_id}] Cookie consent page detected, handling...")
                if self.handle_initial_popups(driver, profile_id):
                    time.sleep(3)
                    # Check final URL after handling - NO RECURSION
                    final_url = driver.current_url.lower()
                    final_page = driver.page_source.lower()

                    # Quick check if we're now logged in
                    nav_elements = driver.find_elements(By.CSS_SELECTOR, "nav[role='navigation']")
                    if nav_elements:
                        with self.thread_lock:
                            print(f"✅ [Profile {profile_id}] Now logged in after consent handling")
                        return "Alive"

                    # Check if still on consent or now on login
                    if "/consent/" not in final_url and ("login" in final_url or "password" in final_page):
                        with self.thread_lock:
                            print(f"🔐 [Profile {profile_id}] Now on login page after consent")
                        return "Logged out"

                    # If still on consent page after handling, return logged out
                    if "/consent/" in final_url:
                        with self.thread_lock:
                            print(f"🔐 [Profile {profile_id}] Still on consent page - needs login")
                        return "Logged out"

            # PRIORITY 4: Check for logged-in indicators (AFTER consent handling)
            try:
                logged_in_selectors = [
                    "nav[role='navigation']",
                    "[data-testid='user-avatar']",
                    "input[placeholder*='Search' i]",
                    "img[alt*='profile picture' i]",
                    "svg[aria-label*='Home' i]",
                    "a[href='/']",
                    "div[role='menubar']"
                ]

                logged_in_elements_found = 0
                found_elements = []

                for selector in logged_in_selectors:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    visible_elements = [el for el in elements if el.is_displayed()]
                    if visible_elements:
                        logged_in_elements_found += 1
                        found_elements.append(selector)

                # Check for Instagram feed content (strong indicator of being logged in)
                # BUT only if we're NOT on a consent page
                if "/consent/" not in current_url:
                    feed_indicators = [
                        len(driver.find_elements(By.CSS_SELECTOR, "article")) > 0,
                        "for you" in page_source and "following" in page_source,
                        len(driver.find_elements(By.CSS_SELECTOR, "[data-testid='like-button']")) > 0,
                        len(driver.find_elements(By.CSS_SELECTOR, "img[alt*='profile picture']")) > 0
                    ]

                    feed_elements_found = sum(1 for indicator in feed_indicators if indicator)

                    # If we find multiple logged-in indicators OR feed content, account is alive
                    if logged_in_elements_found >= 2 or feed_elements_found >= 1:
                        with self.thread_lock:
                            print(
                                f"✅ [Profile {profile_id}] Account is LOGGED IN - found {logged_in_elements_found} nav elements, {feed_elements_found} feed elements")
                            print(f"✅ [Profile {profile_id}] Found elements: {found_elements}")
                        return "Alive"

            except Exception as e:
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Error checking logged-in indicators: {e}")

            # PRIORITY 6: Check for automated behavior warning (means logged in but flagged)
            automated_behavior_indicators = [
                "we suspect automated behavior" in page_source,
                "automated behavior on your account" in page_source,
                "suspicious activity" in page_source and "dismiss" in page_source
            ]

            if any(automated_behavior_indicators):
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Automated behavior warning - can dismiss and continue")
                return "Automated Behavior Warning"

            # PRIORITY 6: Check for login page indicators
            login_indicators = [
                "accounts/login" in current_url,
                "login/?source" in current_url,
                len(driver.find_elements(By.CSS_SELECTOR, "input[name='username']")) > 0,
                len(driver.find_elements(By.CSS_SELECTOR, "input[name='password']")) > 0,
                "log in" in page_source and "password" in page_source
            ]

            # Only consider it a login page if we haven't already determined it's logged in
            if any(login_indicators):
                # Double-check we're not on a logged-in page with embedded login forms
                nav_check = len(driver.find_elements(By.CSS_SELECTOR, "nav[role='navigation']")) > 0
                search_check = len(driver.find_elements(By.CSS_SELECTOR, "input[placeholder*='Search']")) > 0

                if nav_check or search_check:
                    with self.thread_lock:
                        print(f"✅ [Profile {profile_id}] Has login elements but also nav/search - account is LOGGED IN")
                    return "Alive"
                else:
                    with self.thread_lock:
                        print(f"🔐 [Profile {profile_id}] Login page detected")
                    return "Logged out"

            # PRIORITY 7: Check for 2FA page
            two_fa_indicators = [
                "two_factor" in current_url,
                "challenge" in current_url,
                "security code" in page_source and "6-digit" in page_source,
                len(driver.find_elements(By.CSS_SELECTOR, "input[name='verificationCode']")) > 0
            ]

            if any(two_fa_indicators):
                with self.thread_lock:
                    print(f"🔐 [Profile {profile_id}] 2FA page detected")
                return "Logged out"

            # PRIORITY 8: Check for page loading errors that might indicate "Something went wrong"
            page_error_indicators = [
                "something went wrong" in page_source,
                "error occurred" in page_source,
                "page not loading" in page_source,
                "try again later" in page_source,
                len(driver.find_elements(By.TAG_NAME, "body")) > 0 and 
                len(driver.find_elements(By.CSS_SELECTOR, "nav, [data-testid='user-avatar'], input[placeholder*='Search']")) == 0
            ]

            if any(page_error_indicators):
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Page loading error detected")
                return "Something went wrong Checkpoint"

            # PRIORITY 10: Final fallback - if we're on main Instagram page, assume logged in
            if current_url == "https://www.instagram.com/" or "instagram.com/" in current_url:
                # We're on the main Instagram page - this should be logged in
                with self.thread_lock:
                    print(f"✅ [Profile {profile_id}] On main Instagram page - assuming logged in")
                return "Alive"

            with self.thread_lock:
                print(f"❓ [Profile {profile_id}] Could not determine status - marking as Error")
            return "Error"

        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Exception in status detection: {e}")
            return "Error"

    def handle_automated_behavior_warning(self, driver, profile_id):
        """Handle automated behavior warning by clicking dismiss"""
        try:
            with self.thread_lock:
                print(f"🔄 [Profile {profile_id}] Handling automated behavior warning...")
            
            dismiss_selectors = [
                "//button[contains(text(), 'Dismiss')]",
                "//button[contains(text(), 'dismiss')]",
                "//button[contains(text(), 'OK')]",
                "//button[contains(text(), 'Continue')]",
                "//div[@role='button' and contains(text(), 'Dismiss')]"
            ]
            
            for selector in dismiss_selectors:
                try:
                    dismiss_button = driver.find_element(By.XPATH, selector)
                    if dismiss_button.is_displayed() and dismiss_button.is_enabled():
                        dismiss_button.click()
                        with self.thread_lock:
                            print(f"✅ [Profile {profile_id}] Clicked dismiss button")
                        time.sleep(3)
                        return True
                except:
                    continue
            
            with self.thread_lock:
                print(f"⚠️ [Profile {profile_id}] Could not find dismiss button")
            return False
            
        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error handling automated behavior warning: {e}")
            return False

    def handle_http_429_error(self, driver, profile_id):
        """Handle HTTP 429 error by reloading the page"""
        try:
            with self.thread_lock:
                print(f"🔄 [Profile {profile_id}] Handling HTTP 429 error - reloading page...")
            
            driver.refresh()
            time.sleep(random.uniform(5, 8))
            
            # Check if error is resolved
            current_url = driver.current_url.lower()
            page_source = driver.page_source.lower()
            
            if "http error 429" not in page_source and "too many requests" not in page_source:
                with self.thread_lock:
                    print(f"✅ [Profile {profile_id}] HTTP 429 error resolved after reload")
                return True
            else:
                with self.thread_lock:
                    print(f"❌ [Profile {profile_id}] HTTP 429 error persists after reload")
                return False
                
        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error handling HTTP 429: {e}")
            return False

    def handle_bad_proxy(self, driver, profile_id):
        """Handle bad proxy by closing the profile"""
        try:
            with self.thread_lock:
                print(f"🔄 [Profile {profile_id}] Bad proxy detected - closing profile...")
            
            # Close the browser
            driver.quit()
            
            with self.thread_lock:
                print(f"✅ [Profile {profile_id}] Profile closed due to bad proxy")
            return True
            
        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error handling bad proxy: {e}")
            return False

    def handle_something_wrong_checkpoint(self, driver, profile_id):
        """Handle 'Something went wrong' checkpoint"""
        try:
            with self.thread_lock:
                print(f"🔄 [Profile {profile_id}] Handling 'Something went wrong' checkpoint...")
            
            # Try reloading the page first
            driver.refresh()
            time.sleep(random.uniform(5, 8))
            
            # Check if issue is resolved
            current_url = driver.current_url.lower()
            page_source = driver.page_source.lower()
            
            error_indicators = [
                "something went wrong" in page_source,
                "error occurred" in page_source,
                "page not loading" in page_source,
                "try again later" in page_source
            ]
            
            if not any(error_indicators):
                with self.thread_lock:
                    print(f"✅ [Profile {profile_id}] Issue resolved after reload")
                return True
            else:
                # Close profile if reload didn't help
                with self.thread_lock:
                    print(f"❌ [Profile {profile_id}] Issue persists - closing profile")
                driver.quit()
                return False
                
        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error handling something wrong checkpoint: {e}")
            return False

    def handle_change_password_checkpoint(self, driver, profile_id):
        """Handle 'Change Password' checkpoint"""
        try:
            with self.thread_lock:
                print(f"🔄 [Profile {profile_id}] Handling 'Change Password' checkpoint...")
            
            # Close the profile as we can't automatically change password
            driver.quit()
            
            with self.thread_lock:
                print(f"✅ [Profile {profile_id}] Profile closed - requires manual password change")
            return True
            
        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error handling change password checkpoint: {e}")
            return False

    def attempt_auto_login(self, driver, profile_info):
        """Attempt to automatically log in the profile"""
        profile_id = profile_info['id']
        username = profile_info.get('username', '')
        password = profile_info.get('password', '')
        twofa_secret = profile_info.get('2fa_secret', '')

        try:
            with self.thread_lock:
                print(f"🔐 [Profile {profile_id}] Starting auto-login process...")
                print(f"🔐 [Profile {profile_id}] Username: {username}")

            current_url = driver.current_url.lower()

            # Check if we're on a phone verification challenge page
            if "/challenge/" in current_url:
                page_source = driver.page_source.lower()
                phone_verification_indicators = [
                    "add phone number to get back into" in page_source,
                    "we will send a confirmation code via sms" in page_source,
                    "phone number" in page_source and "confirmation" in page_source,
                    "send confirmation" in page_source
                ]

                if any(phone_verification_indicators):
                    with self.thread_lock:
                        print(f"📱 [Profile {profile_id}] Currently on phone verification page - cannot auto-login")
                    return "Phone verification required - cannot bypass"

            # Navigate to login page if not already there
            if "login" not in current_url and "/challenge/" not in current_url:
                driver.get("https://www.instagram.com")
                time.sleep(random.uniform(3, 5))
                self.handle_initial_popups(driver, profile_id)

            current_url = driver.current_url.lower()
            if "login" not in current_url:
                try:
                    nav_elements = driver.find_elements(By.TAG_NAME, "nav")
                    if nav_elements:
                        with self.thread_lock:
                            print(f"✅ [Profile {profile_id}] Already logged in!")
                        self.handle_post_login_popups(driver, profile_id)
                        return "Already logged in"
                except:
                    pass

            if "login" not in current_url:
                driver.get("https://www.instagram.com/accounts/login/")
                time.sleep(random.uniform(2, 4))

            if not username or not password:
                with self.thread_lock:
                    print(f"❌ [Profile {profile_id}] Missing username or password in Airtable")
                return "Missing credentials"

            # Find and fill username field
            username_selectors = [
                "input[name='username']",
                "input[placeholder='Phone number, username, or email']"
            ]

            username_field = None
            for selector in username_selectors:
                try:
                    username_field = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    break
                except TimeoutException:
                    continue

            if not username_field:
                with self.thread_lock:
                    print(f"❌ [Profile {profile_id}] Could not find username field")
                return "Username field not found"

            # Find password field
            password_field = None
            try:
                password_field = driver.find_element(By.CSS_SELECTOR, "input[name='password']")
            except:
                with self.thread_lock:
                    print(f"❌ [Profile {profile_id}] Could not find password field")
                return "Password field not found"

            # Fill credentials
            with self.thread_lock:
                print(f"📝 [Profile {profile_id}] Filling credentials...")

            username_field.click()
            time.sleep(0.5)
            if not self.human_type(username_field, username, 0.08):
                return "Error typing username"

            time.sleep(1)
            password_field.click()
            time.sleep(0.5)
            if not self.human_type(password_field, password, 0.08):
                return "Error typing password"

            time.sleep(1)

            # Find and click login button
            login_button = None
            try:
                login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            except:
                try:
                    login_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Log in')]")
                except:
                    return "Login button not found"

            with self.thread_lock:
                print(f"🚀 [Profile {profile_id}] Clicking login button...")

            login_button.click()

            # Wait and check for different outcomes
            time.sleep(5)
            current_url = driver.current_url.lower()
            page_source = driver.page_source.lower()

            # Check for phone verification challenge after login
            if "/challenge/" in current_url:
                phone_verification_indicators = [
                    "add phone number to get back into" in page_source,
                    "we will send a confirmation code via sms" in page_source,
                    "phone number" in page_source and "confirmation" in page_source,
                    "send confirmation" in page_source
                ]

                if any(phone_verification_indicators):
                    with self.thread_lock:
                        print(f"📱 [Profile {profile_id}] Login triggered phone verification challenge")
                    return "Login successful but phone verification required"

            # Check for 2FA
            two_fa_indicators = [
                "two_factor" in current_url,
                "challenge" in current_url and "security code" in page_source,
                "security code" in page_source and "6-digit" in page_source,
                len(driver.find_elements(By.CSS_SELECTOR, "input[name='verificationCode']")) > 0
            ]

            if any(two_fa_indicators):
                with self.thread_lock:
                    print(f"🔐 [Profile {profile_id}] 2FA required")

                if not twofa_secret:
                    with self.thread_lock:
                        print(f"❌ [Profile {profile_id}] 2FA required but no secret in Airtable")
                    return "2FA required but no secret"

                twofa_code = self.generate_2fa_code(twofa_secret)
                if not twofa_code:
                    return "Failed to generate 2FA code"

                # Find and fill 2FA field
                code_field = None
                try:
                    code_field = WebDriverWait(driver, 8).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='verificationCode']"))
                    )
                except:
                    try:
                        code_field = driver.find_element(By.CSS_SELECTOR, "input[maxlength='6']")
                    except:
                        return "2FA input field not found"

                code_field.click()
                time.sleep(0.5)
                if not self.human_type(code_field, twofa_code, 0.15):
                    return "Error typing 2FA code"

                time.sleep(1)

                # Submit 2FA
                try:
                    confirm_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                    confirm_button.click()
                except:
                    code_field.send_keys("\n")

                time.sleep(5)

            # Handle post-login popups
            self.handle_post_login_popups(driver, profile_id)

            # Verify login success
            time.sleep(2)
            final_url = driver.current_url.lower()

            if "login" not in final_url and "challenge" not in final_url:
                try:
                    logged_in_elements = driver.find_elements(By.CSS_SELECTOR, "nav, [data-testid='user-avatar']")
                    if logged_in_elements:
                        with self.thread_lock:
                            print(f"✅ [Profile {profile_id}] Login successful!")
                        return "Login successful"
                except:
                    pass

            return "Login verification failed"

        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error in auto-login: {e}")
            return f"Auto-login error: {str(e)}"

    def handle_post_login_popups(self, driver, profile_id):
        """Handle popups that appear after login"""
        try:
            print(f"🔄 [Profile {profile_id}] Checking for post-login popups...")
            time.sleep(3)

            popup_handlers = [
                ("Get started", "//button[contains(text(), 'Get started')]"),
                ("Not now", "//button[contains(text(), 'Not now')]"),
                ("Not Now", "//button[contains(text(), 'Not Now')]"),
                ("Dismiss", "//button[contains(text(), 'Dismiss')]"),
                ("Continue", "//button[contains(text(), 'Continue')]")
            ]

            for popup_name, xpath in popup_handlers:
                try:
                    popup_button = driver.find_element(By.XPATH, xpath)
                    if popup_button.is_displayed():
                        popup_button.click()
                        print(f"✅ [Profile {profile_id}] Clicked '{popup_name}' popup")
                        time.sleep(2)
                except:
                    continue

        except Exception as e:
            print(f"❌ [Profile {profile_id}] Error handling post-login popups: {e}")

    def should_update_status(self, current_status, detected_status, is_logged_in):
        """Determine if we should update the status"""
        # Handle new checkpoint statuses
        if detected_status == "Banned 🔴":
            return True, "Banned 🔴"

        if detected_status == "Automated Behavior Warning":
            # Don't update status, just handle the warning
            return False, current_status

        if detected_status == "HTTP 429 Error":
            # Don't update status, just handle the error
            return False, current_status

        if detected_status == "Bad Proxy":
            return True, "Bad Proxy"

        if detected_status == "Something went wrong Checkpoint":
            return True, "Something went wrong Checkpoint"

        if detected_status == "Change Password Checkpoint":
            return True, "Change Password Checkpoint"

        # Handle existing statuses
        if detected_status == "Suspended":
            return True, "Suspended"

        if detected_status == "Disabled":
            return True, "Disabled"

        # Map new statuses to existing Airtable options
        if detected_status == "Phone verification required":
            return True, "Logged out"  # Use existing status since phone verification means logged out

        if detected_status == "Challenge required":
            return True, "Logged out"  # Use existing status since challenge means logged out

        if detected_status in ["Logged out", "Error"]:
            return True, detected_status

        if detected_status == "Alive" and is_logged_in:
            if current_status in ["Suspended", "Logged out", "Disabled", "Error", None, "", "Banned 🔴", "Bad Proxy", "Something went wrong Checkpoint", "Change Password Checkpoint"]:
                return True, "Alive"
            else:
                print(f"ℹ️  Not updating status - current is '{current_status}' and account is logged in")
                return False, current_status

        return True, detected_status

    def update_airtable_status(self, record_id, new_status):
        """Update the status in Airtable using record ID"""
        try:
            headers = {
                'Authorization': f'Bearer {self.airtable_token}',
                'Content-Type': 'application/json'
            }

            encoded_table_name = urllib.parse.quote(self.airtable_table_name)
            update_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{encoded_table_name}/{record_id}"

            update_data = {
                "fields": {
                    "Status": new_status
                }
            }

            response = requests.patch(update_url, headers=headers, json=update_data)

            if response.status_code in [200, 201]:
                print(f"✅ Updated Airtable status to: {new_status}")
                return True
            else:
                print(f"❌ Failed to update Airtable: {response.status_code}")
                print(f"Response: {response.text}")
                return False

        except Exception as e:
            print(f"❌ Error updating Airtable: {e}")
            return False

    def process_single_profile_thread_safe(self, profile_info):
        """Thread-safe version of process_single_profile with auto-login"""
        profile_id = profile_info['id']
        record_id = profile_info['record_id']
        current_status = profile_info['current_status']
        username = profile_info.get('username', 'Unknown')

        with self.thread_lock:
            print(f"\n🔄 [Thread {threading.current_thread().name}] Processing Profile: {profile_id}")
            print(f"📊 Current Airtable status: {current_status}")
            print(f"👤 Username: {username}")

        with self.thread_lock:
            print(f"🚀 [Profile {profile_id}] Starting AdsPower profile...")

        adspower_response = self.start_adspower_profile(profile_id)
        if not adspower_response:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Failed to start AdsPower profile")
            self.update_airtable_status(record_id, "Error")
            return {"profile_id": profile_id, "status": "Error", "reason": "Failed to start profile"}

        driver = None
        try:
            with self.thread_lock:
                print(f"🌐 [Profile {profile_id}] Connecting to browser...")

            driver = self.connect_to_browser(adspower_response)
            if not driver:
                with self.thread_lock:
                    print(f"❌ [Profile {profile_id}] Failed to connect to browser")
                self.update_airtable_status(record_id, "Error")
                return {"profile_id": profile_id, "status": "Error", "reason": "Failed to connect to browser"}

            with self.thread_lock:
                print(f"🔍 [Profile {profile_id}] Checking Instagram status...")

            initial_status = self.detect_instagram_status_quiet(driver, profile_id)

            with self.thread_lock:
                print(f"🔍 [Profile {profile_id}] Initial status: {initial_status}")

            # Handle different checkpoint statuses
            if initial_status == "Automated Behavior Warning":
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Handling automated behavior warning...")
                
                if self.handle_automated_behavior_warning(driver, profile_id):
                    time.sleep(3)
                    final_status = self.detect_instagram_status_quiet(driver, profile_id)
                else:
                    final_status = initial_status
                login_result = "Handled automated behavior warning"

            elif initial_status == "HTTP 429 Error":
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Handling HTTP 429 error...")
                
                if self.handle_http_429_error(driver, profile_id):
                    time.sleep(3)
                    final_status = self.detect_instagram_status_quiet(driver, profile_id)
                else:
                    final_status = "Something went wrong Checkpoint"
                login_result = "Handled HTTP 429 error"

            elif initial_status == "Bad Proxy":
                with self.thread_lock:
                    print(f"🌐 [Profile {profile_id}] Handling bad proxy...")
                
                self.handle_bad_proxy(driver, profile_id)
                final_status = "Bad Proxy"
                login_result = "Closed due to bad proxy"

            elif initial_status == "Something went wrong Checkpoint":
                with self.thread_lock:
                    print(f"⚠️ [Profile {profile_id}] Handling something went wrong checkpoint...")
                
                if self.handle_something_wrong_checkpoint(driver, profile_id):
                    time.sleep(3)
                    final_status = self.detect_instagram_status_quiet(driver, profile_id)
                else:
                    final_status = "Something went wrong Checkpoint"
                login_result = "Handled something went wrong checkpoint"

            elif initial_status == "Change Password Checkpoint":
                with self.thread_lock:
                    print(f"🔒 [Profile {profile_id}] Handling change password checkpoint...")
                
                self.handle_change_password_checkpoint(driver, profile_id)
                final_status = "Change Password Checkpoint"
                login_result = "Closed due to password change required"

            # Attempt auto-login if logged out OR needs phone verification
            elif initial_status in ["Logged out", "Phone verification required"]:
                with self.thread_lock:
                    if initial_status == "Phone verification required":
                        print(f"📱 [Profile {profile_id}] Phone verification detected, attempting to handle...")
                    else:
                        print(f"🔐 [Profile {profile_id}] Account is logged out, attempting auto-login...")

                login_result = self.attempt_auto_login(driver, profile_info)

                with self.thread_lock:
                    print(f"🔐 [Profile {profile_id}] Login result: {login_result}")

                # Re-check status after login attempt
                time.sleep(3)
                final_status = self.detect_instagram_status_quiet(driver, profile_id)

                with self.thread_lock:
                    print(f"🔍 [Profile {profile_id}] Status after login attempt: {final_status}")

            else:
                final_status = initial_status
                login_result = "No login attempted"

            detected_status = final_status
            is_logged_in = detected_status == "Alive"

            with self.thread_lock:
                print(f"🔍 [Profile {profile_id}] Final detected status: {detected_status}")

            # Determine if we should update and what status to set
            should_update, final_status = self.should_update_status(current_status, detected_status, is_logged_in)

            if should_update:
                success = self.update_airtable_status(record_id, final_status)
                with self.thread_lock:
                    if success:
                        print(f"✅ [Profile {profile_id}] Updated to: {final_status}")
                    else:
                        print(f"❌ [Profile {profile_id}] Failed to update Airtable")
            else:
                with self.thread_lock:
                    print(f"ℹ️  [Profile {profile_id}] No update needed")

            return {
                "profile_id": profile_id,
                "status": final_status,
                "detected": detected_status,
                "updated": should_update,
                "login_result": login_result if initial_status == "Logged out" else "N/A"
            }

        except Exception as e:
            with self.thread_lock:
                print(f"❌ [Profile {profile_id}] Error: {e}")
            self.update_airtable_status(record_id, "Error")
            return {"profile_id": profile_id, "status": "Error", "reason": str(e)}

        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

            self.stop_adspower_profile(profile_id)
            with self.thread_lock:
                print(f"⏹️  [Profile {profile_id}] Profile stopped and cleaned up")

    def process_profiles_with_concurrent_limit(self, profiles_to_check, concurrent_limit):
        """Process profiles with a maximum concurrent limit"""
        total_profiles = len(profiles_to_check)
        all_results = []
        completed_count = 0

        print(f"\n🚀 Starting processing with {concurrent_limit} concurrent profiles max...")

        with ThreadPoolExecutor(max_workers=concurrent_limit, thread_name_prefix="Profile") as executor:
            active_futures = {}
            profile_index = 0

            # Start initial batch
            while len(active_futures) < concurrent_limit and profile_index < total_profiles:
                profile_info = profiles_to_check[profile_index]
                future = executor.submit(self.process_single_profile_thread_safe, profile_info)
                active_futures[future] = profile_info
                print(f"🚀 [{profile_index + 1}/{total_profiles}] Started profile {profile_info['id']}")
                profile_index += 1

                if profile_index < total_profiles:
                    time.sleep(self.profile_start_delay)

            # Process results as they complete
            while active_futures:
                done, pending = concurrent.futures.wait(
                    active_futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED
                )

                for future in done:
                    profile_info = active_futures.pop(future)
                    profile_id = profile_info['id']

                    try:
                        result = future.result()
                        all_results.append(result)
                        completed_count += 1

                        print(
                            f"✅ [{completed_count}/{total_profiles}] Profile {profile_id} completed: {result.get('status', 'Unknown')}")

                    except Exception as e:
                        print(f"❌ Profile {profile_id} failed with exception: {e}")
                        all_results.append({
                            "profile_id": profile_id,
                            "status": "Error",
                            "reason": str(e)
                        })
                        completed_count += 1

                    # Start a new profile if any remain
                    if profile_index < total_profiles:
                        profile_info = profiles_to_check[profile_index]
                        future = executor.submit(self.process_single_profile_thread_safe, profile_info)
                        active_futures[future] = profile_info
                        print(f"🚀 [{profile_index + 1}/{total_profiles}] Started profile {profile_info['id']}")
                        profile_index += 1

                        if profile_index < total_profiles:
                            time.sleep(self.profile_start_delay)

        return all_results

    def run(self):
        """Main execution function"""
        print("🚀 Instagram Status Checker for AdsPower Profiles")
        print("=" * 60)

        selection_method = self.get_profile_selection_method()

        if selection_method == 'range':
            start_id, end_id = self.get_profile_range_input()
            profiles_to_check = self.get_profiles_from_airtable(start_id, end_id)
        else:
            profiles_to_check = self.get_profiles_from_view()

        if not profiles_to_check:
            print("❌ No profiles found")
            return

        total_profiles = len(profiles_to_check)
        concurrent_limit = self.get_concurrent_limit(total_profiles)

        print(f"\n📋 Found {total_profiles} profiles to process")
        print(f"⚡ Will process with maximum {concurrent_limit} concurrent profiles")
        print(f"🕐 {self.profile_start_delay}s delay between starting each profile")

        # Show current status breakdown
        status_counts = {}
        for profile in profiles_to_check:
            status = profile['current_status']
            status_counts[status] = status_counts.get(status, 0) + 1

        print(f"\n📊 Current status breakdown:")
        for status, count in status_counts.items():
            print(f"   {status}: {count}")

        confirm = input(
            f"\n⚠️  This will open up to {concurrent_limit} AdsPower profiles simultaneously!\nProceed with processing? (y/N): ").strip().lower()
        if confirm not in ['y', 'yes']:
            print("👋 Cancelled by user")
            return

        # Process profiles
        print(f"\n🚀 Starting processing...")
        start_time = time.time()

        all_results = self.process_profiles_with_concurrent_limit(profiles_to_check, concurrent_limit)

        end_time = time.time()
        total_time = end_time - start_time

        # Print comprehensive summary
        print(f"\n{'=' * 60}")
        print("📊 FINAL PROCESSING SUMMARY")
        print(f"{'=' * 60}")

        status_counts = {}
        updated_count = 0
        error_count = 0
        login_attempts = 0
        successful_logins = 0

        for result in all_results:
            status = result.get('status', 'Unknown')
            status_counts[status] = status_counts.get(status, 0) + 1

            if result.get('updated', False):
                updated_count += 1

            if status == 'Error':
                error_count += 1

            # Track login attempts
            login_result = result.get('login_result', 'N/A')
            if login_result != 'N/A':
                login_attempts += 1
                if 'successful' in login_result.lower():
                    successful_logins += 1

        print(f"✅ Total profiles processed: {len(all_results)}")
        print(f"📝 Records updated in Airtable: {updated_count}")
        print(f"❌ Profiles with errors: {error_count}")
        print(f"🔐 Login attempts: {login_attempts}")
        print(f"✅ Successful logins: {successful_logins}")
        print(f"⏱️  Total processing time: {total_time:.1f} seconds")
        print(f"📈 Average time per profile: {total_time / len(all_results):.1f} seconds")

        print(f"\n📈 Final status breakdown:")
        for status, count in status_counts.items():
            percentage = (count / len(all_results)) * 100
            print(f"   {status}: {count} ({percentage:.1f}%)")

        if login_attempts > 0:
            login_success_rate = (successful_logins / login_attempts) * 100
            print(f"\n🔐 Login success rate: {successful_logins}/{login_attempts} ({login_success_rate:.1f}%)")

        print(f"\n🎉 Processing completed successfully!")


if __name__ == "__main__":
    checker = InstagramStatusChecker()
    checker.run()