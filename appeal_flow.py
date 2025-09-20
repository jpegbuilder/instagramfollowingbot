#!/usr/bin/env python3
"""
Instagram Appeal Flow - automatic recovery of banned accounts
"""

import requests
import time
import random
import base64
import os
import threading
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import pyotp
from appeal_config import *
from twocaptcha import TwoCaptcha

class InstagramAppealFlow:
    def __init__(self, driver, profile_id):
        """
        Initialize Appeal Flow
        
        Args:
            driver: Selenium WebDriver
            profile_id: Profile ID for appeal
        """
        self.driver = driver
        self.profile_id = profile_id
        self.thread_lock = threading.Lock()
        self.appeal_status = "APPEAL_STARTED"
        
    def log(self, message):
        """Thread-safe logging"""
        with self.thread_lock:
            print(f"🔄 [Profile {self.profile_id}] {message}")
    
    def check_2captcha_balance(self):
        """Check 2captcha balance"""
        try:
            solver = TwoCaptcha(TWOCAPTCHA_API_KEY)
            balance = solver.balance()
            self.log(f"💰 2captcha balance: ${balance}")
            return balance
        except Exception as e:
            self.log(f"⚠️ Error checking 2captcha balance: {e}")
            return None
    
    def solve_captcha(self, captcha_image_path=None):
        """
        Solve reCAPTCHA v2 via 2captcha API using official SDK

        Args:
            captcha_image_path: Path to captcha image (not used for reCAPTCHA v2)

        Returns:
            bool: True if captcha solved successfully
        """
        try:
            self.log("🔐 Starting reCAPTCHA v2 solving with official SDK...")

            # Check balance first
            balance = self.check_2captcha_balance()
            if balance is not None and balance <= 0:
                self.log("💰 2captcha balance is empty, trying alternative bypass...")
                return self.try_bypass_captcha()
            
            # Ensure performance logs are enabled for data-s extraction
            # Performance logs are now enabled via CDP and selenium-wire
            self.log("🔧 Note: Performance logs enabled via CDP and selenium-wire for rqdata extraction")

            # Find site key for reCAPTCHA
            site_key = self.find_recaptcha_site_key()
            if not site_key:
                self.log("❌ Site key for reCAPTCHA not found")
                return False

            self.log(f"✅ Found site key: {site_key}")
            
            # Get iframe URL for reCAPTCHA (more reliable than main page URL)
            iframe_url = self.get_recaptcha_iframe_url()
            if not iframe_url:
                self.log("⚠️ Could not find reCAPTCHA iframe URL, using current page URL")
                iframe_url = self.driver.current_url
            
            self.log(f"🔍 Using iframe URL: {iframe_url}")
            
            # Check if this reCAPTCHA requires data-s parameter
            data_s = None
            # Try to find data-s parameter first
            data_s = self.find_recaptcha_data_s()
            if data_s and data_s != "XPolarisUFACController":  # Ignore fake data-s
                self.log(f"🔍 Found valid recaptchaDataSValue: {data_s[:30]}...")
            else:
                self.log("⚠️ No valid data-s parameter found - using standard reCAPTCHA v2")
                data_s = None
            
            # Initialize 2captcha solver with official SDK
            self.log("🔧 Initializing 2captcha SDK...")
            solver = TwoCaptcha(TWOCAPTCHA_API_KEY)
            
            self.log("📤 Sending reCAPTCHA to 2captcha using official SDK...")
            self.log(f"🔧 SDK parameters: sitekey={site_key}, url={iframe_url}")
            
            # Try solving with iframe URL as pageurl (Instagram uses reCAPTCHA Enterprise)
            try:
                self.log("🔧 Trying with enterprise=1 parameter...")
                # Get fbsbx-specific cookies and user agent for better solving
                cookies = self.get_fbsbx_cookies_string()
                user_agent = self.driver.execute_script("return navigator.userAgent;")
                
                # Force isInvisible=False for checkbox reCAPTCHA (critical for Instagram)
                self.log("🔧 Forcing isInvisible=False for checkbox reCAPTCHA")
                
                # Use the actual fbsbx iframe URL instead of parent page
                iframe_url = "https://www.fbsbx.com/captcha/recaptcha/iframe/"
                self.log(f"🔍 Using fbsbx iframe URL as url: {iframe_url}")
                
                # Correct parameters for 2captcha API (following official documentation)
                recaptcha_params = {
                    'sitekey': site_key,
                    'url': iframe_url,  # Correct parameter name for Python SDK
                    'isInvisible': False  # For visible checkbox reCAPTCHA (False = visible, True = invisible)
                }
                
                # Add data-s/rqdata parameter only if valid (not fake)
                if data_s and data_s != "XPolarisUFACController":
                    recaptcha_params['recaptcha_rqdata'] = data_s  # Use rqdata parameter for 2captcha
                    recaptcha_params['enterprise'] = 1  # Enterprise reCAPTCHA
                    self.log(f"🔧 Including valid rqdata parameter: {data_s[:30]}...")
                else:
                    # Try enterprise=1 for Instagram (they often use Enterprise)
                    recaptcha_params['enterprise'] = 1
                    self.log("🔧 Using Enterprise reCAPTCHA for Instagram")
                
                # Add cookies and user agent for better solving
                if cookies:
                    recaptcha_params['cookies'] = cookies
                    self.log(f"🔧 Including cookies: {len(cookies)} characters")
                
                if user_agent:
                    recaptcha_params['userAgent'] = user_agent
                    self.log(f"🔧 Including user agent: {user_agent[:50]}...")
                
                # Add additional parameters for better solving
                recaptcha_params['action'] = 'verify'  # Action parameter
                recaptcha_params['min_score'] = 0.3    # Minimum score for v3 (ignored for v2)
                
                result = solver.recaptcha(**recaptcha_params)
            except Exception as e:
                self.log(f"⚠️ Failed with first attempt, trying fallback parameters: {e}")
                # If fails, try without enterprise parameter
                try:
                    fallback_params = {
                        'sitekey': site_key,
                        'url': iframe_url,  # Correct parameter name for Python SDK
                        'isInvisible': False  # For visible checkbox reCAPTCHA
                    }
                    if data_s and data_s != "XPolarisUFACController":
                        fallback_params['recaptcha_rqdata'] = data_s
                    if cookies:
                        fallback_params['cookies'] = cookies
                    if user_agent:
                        fallback_params['userAgent'] = user_agent
                    result = solver.recaptcha(**fallback_params)
                except Exception as e2:
                    self.log(f"⚠️ Failed with fallback, trying with Instagram base URL: {e2}")
                    # If fails, try with Instagram base URL and enterprise
                    final_params = {
                        'sitekey': site_key,
                        'url': "https://www.instagram.com",  # Correct parameter name for Python SDK
                        'isInvisible': False  # For visible checkbox reCAPTCHA
                    }
                    if data_s and data_s != "XPolarisUFACController":
                        final_params['recaptcha_rqdata'] = data_s
                    if cookies:
                        final_params['cookies'] = cookies
                    if user_agent:
                        final_params['userAgent'] = user_agent
                    result = solver.recaptcha(**final_params)
            
            self.log("✅ reCAPTCHA solved, token received")
            self.log(f"🔧 Result: {result}")
            
            # Submit token with retry logic for Meta/FBSBX
            token = result['code']
            max_retries = 3
            
            for attempt in range(max_retries):
                self.log(f"🔧 Token delivery attempt {attempt + 1}/{max_retries}")
                
                # Submit token using Meta-optimized method
                result_meta = self.deliver_token_meta(token)
                
                if result_meta['delivered'] and result_meta['hasResponse']:
                    self.log("✅ Token delivered and validated successfully")
                    break
                elif result_meta['delivered']:
                    self.log("⚠️ Token delivered but validation failed, proceeding anyway...")
                    break
                else:
                    self.log(f"❌ Token delivery failed on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(0.5)  # Short delay before retry
            
            # Always try to click Next and wait for recaptcha to disappear
            return self.click_next_and_wait_recaptcha_gone()
            
        except Exception as e:
            error_message = str(e)
            self.log(f"❌ Error solving reCAPTCHA: {error_message}")
            self.log(f"🔧 Error type: {type(e).__name__}")
            
            # Check for specific 2captcha errors
            if 'ERROR_CAPTCHA_UNSOLVABLE' in error_message:
                self.log("⚠️ reCAPTCHA is unsolvable by 2captcha, trying bypass...")
                return self.try_bypass_captcha()
            elif 'ERROR_WRONG_USER_KEY' in error_message:
                self.log("❌ Invalid 2captcha API key")
                return False
            elif 'ERROR_ZERO_BALANCE' in error_message or 'balance' in error_message.lower():
                self.log("💰 2captcha balance is empty, trying alternative bypass...")
                return self.try_bypass_captcha()
            elif 'ERROR_NO_SLOT_AVAILABLE' in error_message:
                self.log("⚠️ No workers available, trying bypass...")
                return self.try_bypass_captcha()
            
            # Alternative approach - try to bypass reCAPTCHA
            self.log("🔄 Trying alternative approach to reCAPTCHA...")
            return self.try_bypass_captcha()
    
    def try_bypass_captcha(self):
        """
        Alternative approach - try to bypass reCAPTCHA
        """
        try:
            self.log("🔄 Attempting to bypass reCAPTCHA...")
            
            # Look for "I'm not a robot" button and click it
            robot_selectors = [
                "//span[contains(text(), \"I'm not a robot\")]",
                "//span[contains(text(), 'I am not a robot')]",
                "//*[contains(text(), \"I'm not a robot\")]",
                "//*[contains(text(), 'I am not a robot')]",
                "//div[contains(@class, 'recaptcha-checkbox')]",
                "//span[@id='recaptcha-anchor']",
                "//iframe[contains(@src, 'recaptcha')]",
                # Additional selectors for Facebook reCAPTCHA
                "//div[contains(@class, 'fbsbx')]",
                "//div[contains(@class, 'captcha')]",
                "//button[contains(@class, 'captcha')]",
                "//div[contains(@data-testid, 'captcha')]",
                "//*[contains(@aria-label, 'robot')]",
                "//*[contains(@aria-label, 'captcha')]",
                "//*[contains(@title, 'robot')]",
                "//*[contains(@title, 'captcha')]"
            ]
            
            for i, selector in enumerate(robot_selectors):
                try:
                    if 'iframe' in selector:
                        # Для iframe спробуємо клікнути на сам iframe
                        iframe = self.driver.find_element(By.XPATH, selector)
                        if iframe and iframe.is_displayed():
                            self.log(f"✅ Found reCAPTCHA iframe with selector {i+1}")
                            
                            # First try to click on iframe
                            try:
                                iframe.click()
                                self.log("✅ Clicked on reCAPTCHA iframe")
                                time.sleep(3)
                                return True
                            except:
                                # If failed to click iframe, try switching to it
                                try:
                                    self.log("🔄 Switching to iframe to bypass reCAPTCHA...")
                                    self.driver.switch_to.frame(iframe)
                                    
                                    # Look for button inside iframe
                                    inner_selectors = [
                                        "//div[contains(@class, 'recaptcha-checkbox')]",
                                        "//span[@id='recaptcha-anchor']",
                                        "//div[contains(@class, 'recaptcha-checkbox-border')]",
                                        "//div[contains(@class, 'recaptcha-checkbox-checkmark')]"
                                    ]
                                    
                                    for inner_selector in inner_selectors:
                                        try:
                                            inner_element = self.driver.find_element(By.XPATH, inner_selector)
                                            if inner_element and inner_element.is_displayed():
                                                inner_element.click()
                                                self.log("✅ Clicked on reCAPTCHA element inside iframe")
                                                self.driver.switch_to.default_content()
                                                time.sleep(3)
                                                return True
                                        except:
                                            continue
                                    
                                    # Return to main content
                                    self.driver.switch_to.default_content()
                                    
                                except Exception as e:
                                    self.log(f"⚠️ Error working with iframe: {e}")
                                    # Make sure we returned to main content
                                    try:
                                        self.driver.switch_to.default_content()
                                    except:
                                        pass
                    else:
                        element = self.driver.find_element(By.XPATH, selector)
                        if element and element.is_displayed():
                            self.log(f"✅ Знайдено reCAPTCHA елемент з селектором {i+1}")
                            element.click()
                            self.log("✅ Клікнуто на 'I'm not a robot'")
                            time.sleep(5)  # Чекаємо поки reCAPTCHA обробиться
                            return True
                except:
                    continue
            
            # Якщо не знайшли кнопку, спробуємо знайти всі клікабельні елементи
            self.log("🔍 Пошук всіх клікабельних елементів для обходу reCAPTCHA...")
            try:
                all_elements = self.driver.find_elements(By.XPATH, "//*[text() or @onclick or @role='button']")
                
                for element in all_elements:
                    try:
                        if element.is_displayed():
                            text = element.text.strip().lower()
                            tag = element.tag_name
                            
                            # Шукаємо елементи пов'язані з reCAPTCHA
                            if any(keyword in text for keyword in ['robot', 'captcha', 'verify', 'continue', 'next']):
                                self.log(f"   Знайдено потенційний елемент: <{tag}> '{text}'")
                                
                                try:
                                    element.click()
                                    self.log(f"✅ Клікнуто на елемент: '{text}'")
                                    time.sleep(3)
                                    return True
                                except:
                                    # Спробуємо JavaScript click
                                    try:
                                        self.driver.execute_script("arguments[0].click();", element)
                                        self.log(f"✅ JavaScript клік на елемент: '{text}'")
                                        time.sleep(3)
                                        return True
                                    except:
                                        continue
                    except:
                        continue
                        
            except Exception as e:
                self.log(f"⚠️ Помилка пошуку клікабельних елементів: {e}")
            
            # Остання спроба - спробуємо просто клікнути "Continue" або "Next"
            self.log("🔄 Остання спроба - пошук кнопки Continue/Next...")
            continue_selectors = [
                "//*[contains(text(), 'Continue')]",
                "//*[contains(text(), 'Next')]",
                "//*[contains(text(), 'Submit')]",
                "//*[contains(text(), 'Confirm')]",
                "//button[contains(@class, 'submit')]",
                "//button[contains(@class, 'continue')]",
                "//button[contains(@class, 'next')]",
                "//input[@type='submit']",
                "//button[@type='submit']"
            ]
            
            for selector in continue_selectors:
                try:
                    button = self.driver.find_element(By.XPATH, selector)
                    if button and button.is_displayed():
                        button.click()
                        self.log("✅ Клікнуто на Continue/Next після reCAPTCHA")
                        time.sleep(3)
                        return True
                except:
                    continue
            
            self.log("❌ Не вдалося обійти reCAPTCHA")
            return False
            
        except Exception as e:
            self.log(f"❌ Помилка обходу reCAPTCHA: {e}")
            return False
    
    def check_invisible_recaptcha(self):
        """
        Check if reCAPTCHA is invisible (no checkbox visible)
        
        Returns:
            bool: True if reCAPTCHA appears to be invisible
        """
        try:
            # Look for visible checkbox reCAPTCHA first
            visible_selectors = [
                'div[class*="recaptcha-checkbox"]',
                'div[id*="recaptcha-checkbox"]',
                'iframe[src*="recaptcha/bframe"]',
                'iframe[src*="recaptcha/anchor"]'
            ]
            
            for selector in visible_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        self.log(f"✅ Found visible checkbox reCAPTCHA with selector: {selector}")
                        return False  # It's visible, not invisible
                except Exception:
                    continue
            
            # Look for invisible reCAPTCHA indicators
            invisible_selectors = [
                'div[data-size="invisible"]',
                '.g-recaptcha[data-size="invisible"]',
                'div[class*="invisible"]',
                'div[style*="invisible"]',
                'div[style*="display: none"]'
            ]
            
            for selector in invisible_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        self.log(f"✅ Found invisible reCAPTCHA with selector: {selector}")
                        return True
                except Exception:
                    continue
            
            # Check page source for invisible indicators
            page_source = self.driver.page_source.lower()
            invisible_indicators = [
                'data-size="invisible"',
                'invisible'
            ]
            
            visible_indicators = [
                'recaptcha-checkbox',
                'i\'m not a robot',
                'help us confirm'
            ]
            
            # If we see visible indicators, it's not invisible
            for indicator in visible_indicators:
                if indicator in page_source:
                    self.log(f"✅ Found visible reCAPTCHA indicator: {indicator}")
                    return False
            
            # Check for invisible indicators
            for indicator in invisible_indicators:
                if indicator in page_source:
                    self.log(f"✅ Found invisible reCAPTCHA indicator: {indicator}")
                    return True
            
            # Default to visible (checkbox) if we can't determine
            self.log("⚠️ Could not determine reCAPTCHA type, assuming visible checkbox")
            return False
            
        except Exception as e:
            self.log(f"⚠️ Error checking invisible reCAPTCHA: {e}")
            return False

    def get_recaptcha_iframe_url(self):
        """
        Get the reCAPTCHA iframe URL (more reliable than main page URL)
        
        Returns:
            str: iframe URL or None if not found
        """
        try:
            self.log("🔍 Looking for reCAPTCHA iframe URL...")
            
            # Look for reCAPTCHA iframes
            iframe_selectors = [
                "iframe[src*='recaptcha']",
                "iframe[src*='fbsbx.com/captcha']",
                "iframe[src*='google.com/recaptcha']"
            ]
            
            for selector in iframe_selectors:
                try:
                    iframes = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for iframe in iframes:
                        src = iframe.get_attribute('src')
                        if src and ('recaptcha' in src or 'fbsbx.com' in src):
                            self.log(f"✅ Found reCAPTCHA iframe: {src}")
                            return src
                except Exception:
                    continue
            
            # Look in page source for iframe URLs
            page_source = self.driver.page_source
            import re
            
            iframe_patterns = [
                r'<iframe[^>]+src="([^"]*recaptcha[^"]*)"',
                r'<iframe[^>]+src="([^"]*fbsbx\.com/captcha[^"]*)"',
                r'src="([^"]*fbsbx\.com/captcha/recaptcha/iframe[^"]*)"'
            ]
            
            for pattern in iframe_patterns:
                matches = re.findall(pattern, page_source)
                if matches:
                    iframe_url = matches[0]
                    self.log(f"✅ Found reCAPTCHA iframe in page source: {iframe_url}")
                    return iframe_url
            
            self.log("⚠️ No reCAPTCHA iframe URL found")
            return None
            
        except Exception as e:
            self.log(f"⚠️ Error finding reCAPTCHA iframe URL: {e}")
            return None

    def get_cookies_string(self):
        """
        Get cookies as string for 2captcha
        
        Returns:
            str: Cookies in format KEY1:Value1;KEY2:Value2; or None if failed
        """
        try:
            cookies = self.driver.get_cookies()
            if not cookies:
                return None
            
            cookie_strings = []
            for cookie in cookies:
                if cookie.get('name') and cookie.get('value'):
                    cookie_strings.append(f"{cookie['name']}:{cookie['value']}")
            
            if cookie_strings:
                return ';'.join(cookie_strings)
            return None
            
        except Exception as e:
            self.log(f"⚠️ Error getting cookies: {e}")
            return None

    def get_fbsbx_cookies_string(self):
        """
        Get cookies specifically from fbsbx.com domain for 2captcha API
        
        Returns:
            str: fbsbx.com cookies formatted for 2captcha API or None
        """
        try:
            self.log("🔧 Getting fbsbx.com cookies for 2captcha...")
            
            cookies = self.driver.get_cookies()
            if not cookies:
                self.log("⚠️ No cookies found")
                return None
            
            # Filter cookies for fbsbx.com and related domains
            fbsbx_cookies = []
            for cookie in cookies:
                domain = cookie.get('domain', '').lower()
                if any(fbsbx_domain in domain for fbsbx_domain in ['fbsbx.com', 'facebook.com', 'instagram.com']):
                    fbsbx_cookies.append(f"{cookie['name']}:{cookie['value']}")
            
            if not fbsbx_cookies:
                self.log("⚠️ No fbsbx.com cookies found, using all cookies")
                # Fallback to all cookies if no fbsbx-specific ones found
                for cookie in cookies:
                    fbsbx_cookies.append(f"{cookie['name']}:{cookie['value']}")
            
            cookie_str = ';'.join(fbsbx_cookies)
            self.log(f"✅ Got {len(fbsbx_cookies)} fbsbx-related cookies ({len(cookie_str)} chars)")
            return cookie_str
            
        except Exception as e:
            self.log(f"⚠️ Error getting fbsbx cookies: {e}")
            # Fallback to regular cookies
            return self.get_cookies_string()

    def extract_rqdata_from_performance_logs(self):
        """
        Extract rqdata parameter from performance logs via CDP
        
        Returns:
            str: rqdata parameter value or None if not found
        """
        try:
            self.log("🔍 Extracting rqdata from performance logs via CDP...")
            
            import json
            from urllib.parse import urlparse, parse_qs
            
            # Get performance logs
            try:
                logs = self.driver.get_log("performance")
                self.log(f"🔍 Found {len(logs)} performance log entries")
            except Exception as e:
                self.log(f"⚠️ Could not get performance logs: {e}")
                self.log("🔧 Performance logs not available - trying CDP Network domain instead")
                
                # Try alternative CDP method
                try:
                    self.driver.execute_cdp_cmd("Network.enable", {})
                    self.log("✅ Network domain enabled via CDP")
                    
                    # Wait a bit for network requests
                    import time
                    time.sleep(2)
                    
                    # Try to get network events (this might not work with all ChromeDriver versions)
                    try:
                        network_logs = self.driver.get_log("performance")
                        if network_logs:
                            self.log(f"🔍 Found {len(network_logs)} network log entries via CDP")
                            logs = network_logs
                    except:
                        self.log("⚠️ Could not get network logs via CDP either")
                        return None
                        
                except Exception as cdp_e:
                    self.log(f"⚠️ CDP Network domain failed: {cdp_e}")
                    return None
            
            # Parse logs to find reCAPTCHA requests
            for log_entry in logs:
                try:
                    message = json.loads(log_entry['message'])
                    
                    # Check if this is a network request
                    if (message.get('message', {}).get('method') == 'Network.requestWillBeSent' and
                        'params' in message.get('message', {})):
                        
                        request_data = message['message']['params'].get('request', {})
                        url = request_data.get('url', '')
                        
                        # Look for reCAPTCHA reload requests (contains rqdata)
                        if any(pattern in url.lower() for pattern in [
                            '/recaptcha/api2/reload',
                            '/recaptcha/enterprise/reload'
                        ]):
                            self.log(f"🔍 Found reCAPTCHA reload request: {url[:100]}...")
                            
                            # Parse URL to extract rqdata
                            parsed = urlparse(url)
                            query_params = parse_qs(parsed.query)
                            
                            # Check for rqdata parameter
                            if 'rqdata' in query_params and query_params['rqdata'][0]:
                                rqdata_val = query_params['rqdata'][0]
                                if len(rqdata_val) > 40:  # Valid rqdata should be long
                                    self.log(f"✅ Found rqdata: {rqdata_val[:20]}...")
                                    return rqdata_val
                            
                            # Also check for 's' parameter in reload requests
                            if 's' in query_params and query_params['s'][0]:
                                s_val = query_params['s'][0]
                                if len(s_val) > 40:  # Valid s parameter should be long
                                    self.log(f"✅ Found s parameter in reload: {s_val[:20]}...")
                                    return s_val
                        
                        # Also check anchor requests for s parameter
                        elif any(pattern in url.lower() for pattern in [
                            '/recaptcha/api2/anchor',
                            '/recaptcha/enterprise/anchor'
                        ]):
                            self.log(f"🔍 Found reCAPTCHA anchor request: {url[:100]}...")
                            
                            # Parse URL to extract s parameter
                            parsed = urlparse(url)
                            query_params = parse_qs(parsed.query)
                            
                            # Check for s parameter
                            if 's' in query_params and query_params['s'][0]:
                                s_val = query_params['s'][0]
                                if len(s_val) > 40:  # Valid s parameter should be long
                                    self.log(f"✅ Found s parameter in anchor: {s_val[:20]}...")
                                    return s_val
                                    
                except Exception as e:
                    # Skip malformed log entries
                    continue
            
            self.log("⚠️ No valid rqdata found in performance logs")
            return None
            
        except Exception as e:
            self.log(f"❌ Error extracting rqdata from performance logs: {e}")
            return None

    def extract_rqdata_via_selenium_wire(self):
        """
        Extract rqdata parameter via selenium-wire (most reliable method)
        
        Returns:
            str: rqdata parameter value or None if not found
        """
        try:
            self.log("🔍 Extracting rqdata via selenium-wire...")
            
            import urllib.parse as U
            
            # Check if driver has requests attribute (selenium-wire)
            if not hasattr(self.driver, 'requests'):
                self.log("⚠️ Driver does not support requests (selenium-wire not available)")
                return None
            
            # Look through captured requests for reCAPTCHA reload requests
            for request in self.driver.requests:
                try:
                    url = request.url or ""
                    if "/recaptcha/" in url and ("/reload" in url or "/enterprise/reload" in url):
                        self.log(f"🔍 Found reCAPTCHA reload request: {url[:100]}...")
                        
                        # Parse URL to extract rqdata
                        query_params = dict(U.parse_qsl(U.urlsplit(url).query))
                        
                        # Check for rqdata parameter
                        if "rqdata" in query_params and query_params["rqdata"]:
                            rqdata_val = query_params["rqdata"]
                            if len(rqdata_val) > 40:  # Valid rqdata should be long
                                self.log(f"✅ Found rqdata via selenium-wire: {rqdata_val[:20]}...")
                                return rqdata_val
                        
                        # Also check for 's' parameter in reload requests
                        if "s" in query_params and query_params["s"]:
                            s_val = query_params["s"]
                            if len(s_val) > 40:  # Valid s parameter should be long
                                self.log(f"✅ Found s parameter via selenium-wire: {s_val[:20]}...")
                                return s_val
                                
                except Exception as e:
                    # Skip malformed requests
                    continue
            
            self.log("⚠️ No valid rqdata found via selenium-wire")
            return None
            
        except Exception as e:
            self.log(f"❌ Error extracting rqdata via selenium-wire: {e}")
            return None

    def extract_rqdata_via_javascript(self):
        """
        Extract rqdata parameter via JavaScript monitoring (fallback when performance logs unavailable)
        
        Returns:
            str: rqdata parameter value or None if not found
        """
        try:
            self.log("🔍 Extracting rqdata via JavaScript monitoring...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to fbsbx iframe first
            self.driver.switch_to.default_content()
            
            try:
                fb_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']"
                    ))
                )
                self.driver.switch_to.frame(fb_iframe)
                self.log("✅ Switched to fbsbx iframe for JS monitoring")
            except Exception as e:
                self.log(f"❌ Could not switch to fbsbx iframe: {e}")
                return None
            
            # Inject comprehensive JavaScript to monitor network requests and extract rqdata
            self.driver.execute_script("""
                // Store original functions
                const originalFetch = window.fetch;
                const originalXHR = window.XMLHttpRequest.prototype.open;
                const originalXHRSend = window.XMLHttpRequest.prototype.send;
                
                window._capturedRqdata = null;
                window._capturedData = [];
                
                // Enhanced fetch monitoring
                window.fetch = function(...args) {
                    const url = args[0];
                    if (typeof url === 'string') {
                        console.log('Fetch request to:', url);
                        window._capturedData.push({type: 'fetch', url: url, timestamp: Date.now()});
                        
                        // Extract rqdata from URL
                        try {
                            const urlObj = new URL(url);
                            const rqdata = urlObj.searchParams.get('rqdata') || urlObj.searchParams.get('s');
                            if (rqdata && rqdata.length > 40) {
                                window._capturedRqdata = rqdata;
                                console.log('✅ Captured rqdata from fetch:', rqdata.substring(0, 20) + '...');
                            }
                        } catch(e) {
                            console.log('Error parsing fetch URL:', e);
                        }
                    }
                    return originalFetch.apply(this, args);
                };
                
                // Enhanced XMLHttpRequest monitoring
                window.XMLHttpRequest.prototype.open = function(method, url, ...args) {
                    this._requestUrl = url;
                    if (typeof url === 'string') {
                        console.log('XHR request to:', url);
                        window._capturedData.push({type: 'xhr', url: url, timestamp: Date.now()});
                        
                        // Extract rqdata from URL
                        try {
                            const urlObj = new URL(url);
                            const rqdata = urlObj.searchParams.get('rqdata') || urlObj.searchParams.get('s');
                            if (rqdata && rqdata.length > 40) {
                                window._capturedRqdata = rqdata;
                                console.log('✅ Captured rqdata from XHR:', rqdata.substring(0, 20) + '...');
                            }
                        } catch(e) {
                            console.log('Error parsing XHR URL:', e);
                        }
                    }
                    return originalXHR.call(this, method, url, ...args);
                };
                
                // Monitor XHR responses for additional data
                window.XMLHttpRequest.prototype.send = function(data) {
                    const xhr = this;
                    xhr.addEventListener('load', function() {
                        if (xhr._requestUrl && xhr._requestUrl.includes('recaptcha')) {
                            console.log('XHR response received for:', xhr._requestUrl);
                            try {
                                // Sometimes rqdata comes in response headers or body
                                const responseText = xhr.responseText;
                                if (responseText && responseText.includes('rqdata')) {
                                    console.log('Found rqdata in response:', responseText.substring(0, 100));
                                }
                            } catch(e) {}
                        }
                    });
                    return originalXHRSend.call(this, data);
                };
                
                // Also monitor for any iframe src changes that might contain rqdata
                const observer = new MutationObserver(function(mutations) {
                    mutations.forEach(function(mutation) {
                        if (mutation.type === 'attributes' && mutation.attributeName === 'src') {
                            const target = mutation.target;
                            if (target.tagName === 'IFRAME' && target.src && target.src.includes('recaptcha')) {
                                console.log('Iframe src changed:', target.src);
                                try {
                                    const urlObj = new URL(target.src);
                                    const rqdata = urlObj.searchParams.get('rqdata') || urlObj.searchParams.get('s');
                                    if (rqdata && rqdata.length > 40) {
                                        window._capturedRqdata = rqdata;
                                        console.log('✅ Captured rqdata from iframe src:', rqdata.substring(0, 20) + '...');
                                    }
                                } catch(e) {}
                            }
                        }
                    });
                });
                
                observer.observe(document, { attributes: true, subtree: true });
                
                console.log('🔍 Network monitoring initialized');
            """)
            
            # Wait for potential requests and then check for captured data
            import time
            time.sleep(5)  # Wait longer for requests to be made
            
            # Check for captured rqdata and all captured data
            result = self.driver.execute_script("""
                return {
                    rqdata: window._capturedRqdata,
                    allData: window._capturedData || [],
                    timestamp: Date.now()
                };
            """)
            
            self.driver.switch_to.default_content()
            
            if result and result.get('rqdata'):
                self.log(f"✅ Found rqdata via JavaScript: {result['rqdata'][:20]}...")
                return result['rqdata']
            else:
                # Log all captured data for debugging
                if result and result.get('allData'):
                    self.log(f"🔍 Captured {len(result['allData'])} network requests:")
                    for i, data in enumerate(result['allData'][-5:]):  # Show last 5 requests
                        self.log(f"   {i+1}. {data['type']}: {data['url'][:80]}...")
                
                self.log("⚠️ No rqdata found via JavaScript monitoring")
                return None
                
        except Exception as e:
            self.log(f"❌ Error extracting rqdata via JavaScript: {e}")
            self.driver.switch_to.default_content()
            return None

    def extract_s_from_anchor_iframe(self):
        """
        Extract real s/rqdata parameter from anchor iframe src attribute
        
        Returns:
            str: data-s parameter value or None if not found
        """
        try:
            self.log("🔍 Extracting real s/rqdata from anchor iframe...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            from urllib.parse import urlparse, parse_qs
            
            # Switch to default content first
            self.driver.switch_to.default_content()
            
            # Wait for and switch to fbsbx iframe
            try:
                fb_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']"
                    ))
                )
                self.driver.switch_to.frame(fb_iframe)
                self.log("✅ Switched to fbsbx iframe")
            except Exception as e:
                self.log(f"❌ Could not find fbsbx iframe: {e}")
                self.driver.switch_to.default_content()
                return None
            
            # Find inner anchor iframe
            try:
                anchor_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='recaptcha/enterprise/anchor']"
                    ))
                )
                anchor_src = anchor_iframe.get_attribute("src")
                self.log(f"🔍 Found anchor iframe src: {anchor_src[:100]}...")
                
                # Parse URL parameters
                parsed = urlparse(anchor_src)
                query_params = parse_qs(parsed.query)
                
                # Check for s parameter
                if "s" in query_params and query_params["s"][0]:
                    s_val = query_params["s"][0]
                    if len(s_val) > 40:  # Valid s parameter should be long
                        self.log(f"✅ Found s parameter: {s_val[:20]}...")
                        return s_val
                
                # Check for rqdata parameter
                if "rqdata" in query_params and query_params["rqdata"][0]:
                    s_val = query_params["rqdata"][0]
                    if len(s_val) > 40:  # Valid rqdata parameter should be long
                        self.log(f"✅ Found rqdata parameter: {s_val[:20]}...")
                        return s_val
                
                self.log("⚠️ No valid s or rqdata parameter found in anchor iframe (standard reCAPTCHA v2)")
                return None
                
            except Exception as e:
                self.log(f"❌ Could not find anchor iframe: {e}")
                return None
            finally:
                self.driver.switch_to.default_content()
            
        except Exception as e:
            self.log(f"❌ Error extracting s parameter: {e}")
            return None

    def _extract_s_from_c_param(self, c_val):
        """
        Extract s/rqdata from c parameter (base64 JSON)
        
        Args:
            c_val: c parameter value
            
        Returns:
            str: s/rqdata value or None if not found
        """
        try:
            from urllib.parse import unquote
            import base64
            import json
            
            # c can be urlencoded base64 json, sometimes with dots/padding
            raw = unquote(c_val)
            
            # Add padding for base64 if needed
            pad = '=' * (-len(raw) % 4)
            data = base64.urlsafe_b64decode(raw + pad)
            obj = json.loads(data.decode("utf-8", errors="ignore"))
            
            # Different names can be used: s / rqdata / stoken
            for key in ("s", "rqdata", "stoken"):
                if key in obj and isinstance(obj[key], str) and len(obj[key]) > 80:
                    return obj[key]
                    
        except Exception as e:
            self.log(f"⚠️ Error extracting s from c parameter: {e}")
            
        return None

    def extract_real_s_from_fbsbx_scripts(self):
        """
        Extract real s/rqdata parameter from script[data-s] elements in fbsbx iframe
        
        Returns:
            str: data-s parameter value or None if not found
        """
        try:
            self.log("🔍 Extracting real s/rqdata from fbsbx script elements...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            from urllib.parse import urlparse, parse_qs
            
            # Switch to default content first
            self.driver.switch_to.default_content()
            
            # Wait for and switch to fbsbx iframe
            try:
                fb_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']"
                    ))
                )
                self.driver.switch_to.frame(fb_iframe)
                self.log("✅ Switched to fbsbx iframe")
            except Exception as e:
                self.log(f"❌ Could not find fbsbx iframe: {e}")
                self.driver.switch_to.default_content()
                return None
            
            # Method 1: Look for script[data-s] elements (most reliable for real s)
            try:
                scripts = self.driver.find_elements(By.CSS_SELECTOR, "script[data-s]")
                self.log(f"🔍 Found {len(scripts)} script[data-s] elements")
                
                s_vals = []
                for i, script in enumerate(scripts):
                    try:
                        data_s = script.get_attribute("data-s")
                        if data_s and len(data_s) > 80:  # Real data-s should be long
                            s_vals.append(data_s)
                            self.log(f"   script {i+1}: {data_s[:50]}... (length: {len(data_s)})")
                    except Exception as e:
                        self.log(f"   script {i+1}: error reading data-s: {e}")
                
                if s_vals:
                    # Take the longest one (most likely to be real)
                    real_s = max(s_vals, key=len)
                    self.log(f"✅ Found real data-s from scripts: {real_s[:50]}... (length: {len(real_s)})")
                    self.driver.switch_to.default_content()
                    return real_s
                else:
                    self.log("❌ No valid data-s found in script elements")
                    
            except Exception as e:
                self.log(f"⚠️ Error reading script[data-s] elements: {e}")
            
            # Method 2: Try to extract from anchor iframe src as fallback
            try:
                self.log("🔍 Trying fallback: extract from anchor iframe src...")
                
                anchor_selectors = [
                    "iframe[src*='recaptcha/enterprise/anchor']",
                    "iframe[src*='recaptcha/anchor']",
                    "iframe[src*='anchor']",
                    "iframe[src*='google.com/recaptcha']",
                    "iframe[src*='recaptcha']"
                ]
                
                for selector in anchor_selectors:
                    try:
                        anchor_iframe = self.driver.find_element(By.CSS_SELECTOR, selector)
                        anchor_src = anchor_iframe.get_attribute("src")
                        if anchor_src:
                            self.log(f"✅ Found anchor iframe: {anchor_src}")
                            
                            # Extract s parameter from anchor URL
                            parsed_url = urlparse(anchor_src)
                            qs = parse_qs(parsed_url.query)
                            s_val = (qs.get('s') or qs.get('rqdata') or [''])[0]
                            
                            if s_val and len(s_val) > 80:
                                self.log(f"✅ Found real s/rqdata from anchor: {s_val[:50]}... (length: {len(s_val)})")
                                self.driver.switch_to.default_content()
                                return s_val
                            else:
                                self.log(f"❌ Invalid s/rqdata from anchor: {s_val} (length: {len(s_val) if s_val else 0})")
                    except:
                        continue
                        
            except Exception as e:
                self.log(f"⚠️ Error extracting from anchor iframe: {e}")
            
            # Method 3: Try global cfg as last resort
            try:
                self.log("🔍 Trying fallback: global grecaptcha cfg...")
                
                rq = self.driver.execute_script("""
                    try {
                        const cfg = window.___grecaptcha_cfg;
                        if (cfg && cfg.clients) {
                            for (const client of Object.values(cfg.clients)) {
                                for (const widget of Object.values(client)) {
                                    if (widget && widget.D && widget.D.D && widget.D.D.rqdata) {
                                        return widget.D.D.rqdata;
                                    }
                                    if (widget && widget.rqdata) {
                                        return widget.rqdata;
                                    }
                                    if (widget && widget.data_s) {
                                        return widget.data_s;
                                    }
                                }
                            }
                        }
                        return null;
                    } catch (e) {
                        return null;
                    }
                """)
                
                if rq and len(rq) > 80:
                    self.log(f"✅ Found rqdata from global cfg: {rq[:50]}... (length: {len(rq)})")
                    self.driver.switch_to.default_content()
                    return rq
                else:
                    self.log("❌ No valid rqdata found in global cfg")
                    
            except Exception as e:
                self.log(f"⚠️ Error extracting from global cfg: {e}")
            
            self.log("❌ Could not extract real data-s/rqdata from any method")
            self.driver.switch_to.default_content()
            return None
            
        except Exception as e:
            self.log(f"⚠️ Error extracting real data-s: {e}")
            try:
                self.driver.switch_to.default_content()
            except:
                pass
            return None

    def extract_rqdata_from_dom_analysis(self):
        """
        Extract rqdata from DOM analysis (last resort method)
        
        Returns:
            str: rqdata parameter value or None if not found
        """
        try:
            self.log("🔍 Extracting rqdata from DOM analysis...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to fbsbx iframe
            self.driver.switch_to.default_content()
            
            try:
                fb_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']"
                    ))
                )
                self.driver.switch_to.frame(fb_iframe)
                self.log("✅ Switched to fbsbx iframe for DOM analysis")
            except Exception as e:
                self.log(f"❌ Could not switch to fbsbx iframe: {e}")
                return None
            
            # Try to extract from various DOM sources
            rqdata = self.driver.execute_script("""
                // Look for rqdata in various places
                let rqdata = null;
                
                // 1) Check window objects
                try {
                    if (window.grecaptcha && window.grecaptcha.getResponse) {
                        const cfg = window.___grecaptcha_cfg || {};
                        const clients = cfg.clients || {};
                        
                        for (const id in clients) {
                            const client = clients[id];
                            if (client && typeof client === 'object') {
                                // Look for rqdata in client properties
                                for (const key in client) {
                                    const value = client[key];
                                    if (typeof value === 'string' && value.length > 40 && 
                                        (value.includes('rqdata') || key.includes('rqdata'))) {
                                        rqdata = value;
                                        console.log('Found rqdata in client:', key, value.substring(0, 20));
                                        break;
                                    }
                                }
                                if (rqdata) break;
                            }
                        }
                    }
                } catch(e) {
                    console.log('Error checking window objects:', e);
                }
                
                // 2) Check all script tags for rqdata
                if (!rqdata) {
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        const content = script.textContent || script.innerHTML;
                        if (content && content.includes('rqdata')) {
                            // Try to extract rqdata from script content
                            const matches = content.match(/rqdata['"]?\s*[:=]\s*['"]([^'"]{40,})['"]/i);
                            if (matches && matches[1]) {
                                rqdata = matches[1];
                                console.log('Found rqdata in script:', rqdata.substring(0, 20));
                                break;
                            }
                        }
                    }
                }
                
                // 3) Check all data attributes
                if (!rqdata) {
                    const elements = document.querySelectorAll('*[data-rqdata], *[data-s]');
                    for (const el of elements) {
                        const value = el.getAttribute('data-rqdata') || el.getAttribute('data-s');
                        if (value && value.length > 40) {
                            rqdata = value;
                            console.log('Found rqdata in data attribute:', rqdata.substring(0, 20));
                            break;
                        }
                    }
                }
                
                // 4) Check global variables
                if (!rqdata) {
                    const globalVars = ['window.rqdata', 'window.data_s', 'window.recaptchaData'];
                    for (const varName of globalVars) {
                        try {
                            const value = eval(varName);
                            if (value && typeof value === 'string' && value.length > 40) {
                                rqdata = value;
                                console.log('Found rqdata in global var:', varName, rqdata.substring(0, 20));
                                break;
                            }
                        } catch(e) {}
                    }
                }
                
                return rqdata;
            """)
            
            self.driver.switch_to.default_content()
            
            if rqdata:
                self.log(f"✅ Found rqdata via DOM analysis: {rqdata[:20]}...")
                return rqdata
            else:
                self.log("⚠️ No rqdata found via DOM analysis")
                return None
                
        except Exception as e:
            self.log(f"❌ Error in DOM analysis: {e}")
            self.driver.switch_to.default_content()
            return None

    def find_recaptcha_data_s(self):
        """
        Find recaptchaDataSValue/rqdata parameter (critical for Enterprise reCAPTCHA)
        
        Returns:
            str: data-s value or None if not found
        """
        try:
            self.log("🔍 Looking for recaptchaDataSValue/rqdata...")
            
            # Method 1: Extract rqdata via selenium-wire (most reliable)
            real_data_s = self.extract_rqdata_via_selenium_wire()
            if real_data_s:
                return real_data_s
            
            # Method 2: Extract rqdata from performance logs (fallback)
            real_data_s = self.extract_rqdata_from_performance_logs()
            if real_data_s:
                return real_data_s
            
            # Method 3: Extract rqdata via JavaScript monitoring (fallback)
            real_data_s = self.extract_rqdata_via_javascript()
            if real_data_s:
                return real_data_s
            
            # Method 4: Extract real s/rqdata from anchor iframe (fallback)
            real_data_s = self.extract_s_from_anchor_iframe()
            if real_data_s:
                return real_data_s
            
            # Method 5: Extract real s/rqdata from fbsbx script elements (fallback)
            real_data_s = self.extract_real_s_from_fbsbx_scripts()
            if real_data_s:
                return real_data_s
            
            # Method 6: Try to extract from DOM analysis (last resort)
            real_data_s = self.extract_rqdata_from_dom_analysis()
            if real_data_s:
                return real_data_s
            
            # Method 5: Look in DOM elements
            selectors = [
                'div[data-s]',
                '.g-recaptcha[data-s]',
                '#g-recaptcha[data-s]',
                '*[data-s]',
                'input[name="data-s"]',
                'input[name="recaptchaDataSValue"]'
            ]
            
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        data_s = element.get_attribute('data-s')
                        if data_s:
                            self.log(f"✅ Found data-s in DOM: {data_s}")
                            return data_s
                except Exception:
                    continue
            
            # Method 2: Look in JavaScript variables
            try:
                js_variables = [
                    'window.recaptchaDataSValue',
                    'window.rqdata',
                    'window.__recaptchaDataSValue',
                    'window.grecaptchaDataSValue'
                ]
                
                for var in js_variables:
                    try:
                        value = self.driver.execute_script(f"return {var};")
                        if value:
                            self.log(f"✅ Found data-s in JS variable {var}: {value}")
                            return str(value)
                    except Exception:
                        continue
            except Exception as e:
                self.log(f"⚠️ Error checking JS variables: {e}")
            
            # Method 3: Look in page source with regex
            page_source = self.driver.page_source
            
            # Look for various patterns
            patterns = [
                r'recaptchaDataSValue["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'rqdata["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'data-s["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'"s":\s*"([^"]+)"',
                r'&s=([^&"\']+)',
                r'data-s="([^"]+)"'
            ]
            
            for pattern in patterns:
                import re
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    data_s = matches[0]
                    # Filter out obviously wrong values
                    if len(data_s) > 10 and not data_s.startswith('http'):
                        self.log(f"✅ Found data-s in page source: {data_s}")
                        return data_s
            
            # Method 4: Look for network requests (if we can access them)
            try:
                # Check if we can find the parameter in any network requests
                logs = self.driver.get_log('performance')
                for log in logs:
                    message = log.get('message', {})
                    if isinstance(message, str):
                        import json
                        try:
                            message_data = json.loads(message)
                            if message_data.get('method') == 'Network.requestWillBeSent':
                                url = message_data.get('params', {}).get('request', {}).get('url', '')
                                if 'recaptcha' in url and 's=' in url:
                                    # Extract s parameter from URL
                                    import urllib.parse
                                    parsed = urllib.parse.urlparse(url)
                                    params = urllib.parse.parse_qs(parsed.query)
                                    if 's' in params:
                                        data_s = params['s'][0]
                                        self.log(f"✅ Found data-s in network request: {data_s}")
                                        return data_s
                        except Exception:
                            continue
            except Exception as e:
                self.log(f"⚠️ Error checking network logs: {e}")
            
            self.log("⚠️ No recaptchaDataSValue found")
            return None
            
        except Exception as e:
            self.log(f"⚠️ Error finding recaptchaDataSValue: {e}")
            return None

    def find_recaptcha_site_key(self):
        """
        Знайти site key для reCAPTCHA на сторінці
        
        Returns:
            str: Site key або None якщо не знайдено
        """
        try:
            self.log("🔍 Пошук reCAPTCHA site key...")
            
            # Розширені селектори для data-sitekey
            site_key_selectors = [
                "div[data-sitekey]",
                ".g-recaptcha[data-sitekey]",
                "#g-recaptcha[data-sitekey]",
                "*[data-sitekey]",  # Будь-який елемент з data-sitekey
                "[data-sitekey]",   # Альтернативний синтаксис
                "div[class*='recaptcha'][data-sitekey]",
                "div[id*='recaptcha'][data-sitekey]"
            ]
            
            self.log(f"🔍 Перевіряємо {len(site_key_selectors)} селекторів для data-sitekey...")
            
            for i, selector in enumerate(site_key_selectors):
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    self.log(f"   Селектор {i+1}: '{selector}' - знайдено {len(elements)} елементів")
                    
                    for j, element in enumerate(elements):
                        site_key = element.get_attribute('data-sitekey')
                        if site_key:
                            self.log(f"✅ Знайдено site key: {site_key}")
                            return site_key
                        else:
                            self.log(f"   Елемент {j+1}: data-sitekey = None")
                except Exception as e:
                    self.log(f"   Селектор {i+1}: помилка - {e}")
                    continue
            
            # Шукаємо в iframe
            self.log("🔍 Пошук site key в iframe...")
            try:
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                self.log(f"   Знайдено {len(iframes)} iframe елементів")
                
                for i, iframe in enumerate(iframes):
                    try:
                        src = iframe.get_attribute('src')
                        self.log(f"   iframe {i+1}: src = {src}")
                        
                        if src and 'recaptcha' in src.lower():
                            # Парсимо site key з URL
                            import re
                            match = re.search(r'k=([^&]+)', src)
                            if match:
                                site_key = match.group(1)
                                self.log(f"✅ Знайдено site key в iframe: {site_key}")
                                return site_key
                            
                            # Альтернативні паттерни
                            patterns = [
                                r'sitekey=([^&]+)',
                                r'key=([^&]+)',
                                r'data-sitekey=([^&]+)'
                            ]
                            
                            for pattern in patterns:
                                match = re.search(pattern, src)
                                if match:
                                    site_key = match.group(1)
                                    self.log(f"✅ Знайдено site key в iframe (паттерн {pattern}): {site_key}")
                                    return site_key
                    except Exception as e:
                        self.log(f"   iframe {i+1}: помилка - {e}")
                        continue
                        
                # Додатковий пошук - перевіряємо всі iframe на наявність reCAPTCHA
                self.log("🔍 Додатковий пошук reCAPTCHA в iframe...")
                for i, iframe in enumerate(iframes):
                    try:
                        # Спробуємо переключитися на iframe та знайти reCAPTCHA всередині
                        self.driver.switch_to.frame(iframe)
                        
                        # Шукаємо reCAPTCHA елементи всередині iframe
                        recaptcha_elements = self.driver.find_elements(By.CSS_SELECTOR, "*")
                        for element in recaptcha_elements:
                            try:
                                # Перевіряємо data-sitekey всередині iframe
                                site_key = element.get_attribute('data-sitekey')
                                if site_key:
                                    self.log(f"✅ Знайдено site key всередині iframe {i+1}: {site_key}")
                                    self.driver.switch_to.default_content()
                                    return site_key
                                
                                # Перевіряємо інші атрибути
                                for attr in ['data-site-key', 'sitekey', 'googlekey']:
                                    value = element.get_attribute(attr)
                                    if value:
                                        self.log(f"✅ Знайдено {attr} всередині iframe {i+1}: {value}")
                                        self.driver.switch_to.default_content()
                                        return value
                            except:
                                continue
                        
                        # Повертаємося до основного контенту
                        self.driver.switch_to.default_content()
                        
                    except Exception as e:
                        self.log(f"   iframe {i+1}: помилка переключення - {e}")
                        # Переконаємося що повернулися до основного контенту
                        try:
                            self.driver.switch_to.default_content()
                        except:
                            pass
                        continue
                        
            except Exception as e:
                self.log(f"❌ Помилка пошуку в iframe: {e}")
                # Переконаємося що повернулися до основного контенту
                try:
                    self.driver.switch_to.default_content()
                except:
                    pass
            
            # Додатковий пошук в page source
            self.log("🔍 Пошук site key в page source...")
            try:
                page_source = self.driver.page_source
                
                # Шукаємо різні паттерни site key в HTML
                import re
                patterns = [
                    r'data-sitekey="([^"]+)"',
                    r"data-sitekey='([^']+)'",
                    r'data-sitekey=([^"\s>]+)',
                    r'grecaptcha\.render\([^,]*,\s*{\s*sitekey\s*:\s*["\']([^"\']+)["\']',
                    r'grecaptcha\.execute\(["\']([^"\']+)["\']',
                    r'sitekey["\']?\s*:\s*["\']([^"\']+)["\']',
                    r'googlekey["\']?\s*:\s*["\']([^"\']+)["\']',
                    # Додаткові паттерни для Facebook reCAPTCHA
                    r'"sitekey"\s*:\s*"([^"]+)"',
                    r"'sitekey'\s*:\s*'([^']+)'",
                    r'sitekey\s*=\s*["\']([^"\']+)["\']',
                    r'googlekey\s*=\s*["\']([^"\']+)["\']',
                    # Паттерни для iframe src
                    r'iframe[^>]*src="[^"]*k=([^&"]+)[^"]*"',
                    r'iframe[^>]*src=\'[^\']*k=([^&\']+)[^\']*\'',
                    # Загальні паттерни
                    r'6[A-Za-z0-9_-]{39}',  # Типовий формат Google reCAPTCHA site key
                    r'6[A-Za-z0-9_-]{38,40}',  # З варіацією довжини
                ]
                
                for i, pattern in enumerate(patterns):
                    matches = re.findall(pattern, page_source, re.IGNORECASE)
                    if matches:
                        site_key = matches[0]
                        self.log(f"✅ Знайдено site key в page source (паттерн {i+1}): {site_key}")
                        return site_key
                
                # Додатковий пошук - шукаємо будь-які довгі рядки що можуть бути site key
                self.log("🔍 Пошук потенційних site key в page source...")
                potential_keys = re.findall(r'6[A-Za-z0-9_-]{35,45}', page_source)
                if potential_keys:
                    for key in potential_keys:
                        self.log(f"   Потенційний site key: {key}")
                        # Перевіряємо чи це дійсно site key (починається з 6 і має правильну довжину)
                        if len(key) >= 35 and key[0] == '6':
                            self.log(f"✅ Використовуємо потенційний site key: {key}")
                            return key
                
                self.log("❌ Site key не знайдено в page source")
                
            except Exception as e:
                self.log(f"❌ Помилка пошуку в page source: {e}")
            
            # Якщо site key не знайдено, спробуємо використати тестовий site key
            self.log("⚠️ Site key не знайдено, спробуємо тестовий site key...")
            test_site_key = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"  # Тестовий site key від Google
            self.log(f"🧪 Використовуємо тестовий site key: {test_site_key}")
            return test_site_key
            
        except Exception as e:
            self.log(f"❌ Критична помилка пошуку site key: {e}")
            return None
    
    def submit_recaptcha_token(self, token):
        """
        Submit reCAPTCHA token to google bframe iframe and trigger parent flow
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token submitted successfully
        """
        try:
            self.log("🔧 Submitting reCAPTCHA token to google bframe...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to fbsbx iframe first
            self.driver.switch_to.default_content()
            
            try:
                fb_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']"
                    ))
                )
                self.driver.switch_to.frame(fb_iframe)
                self.log("✅ Switched to fbsbx iframe")
                
                # Find google bframe iframes specifically
                bframes = self.driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha/enterprise/bframe']")
                self.log(f"🔍 Found {len(bframes)} bframe iframes")
                
                # If no bframe found, try other reCAPTCHA iframes
                if not bframes:
                    self.log("🔍 No bframe found, looking for other reCAPTCHA iframes...")
                    all_iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                    self.log(f"🔍 Found {len(all_iframes)} total iframes")
                    
                    for i, iframe in enumerate(all_iframes):
                        src = iframe.get_attribute("src") or "no-src"
                        self.log(f"   iframe {i+1}: {src}")
                        
                        # Try different reCAPTCHA iframe patterns
                        if any(pattern in src.lower() for pattern in ['recaptcha', 'google', 'bframe', 'anchor']):
                            bframes.append(iframe)
                            self.log(f"   ✅ Added iframe {i+1} as potential reCAPTCHA target")
                
                self.log(f"🔍 Using {len(bframes)} potential reCAPTCHA iframes")
                
            except Exception as e:
                self.log(f"❌ Could not switch to fbsbx iframe: {e}")
                self.driver.switch_to.default_content()
                return False
            
            if not bframes:
                self.log("❌ No bframe iframes found, trying direct DOM injection...")
                # Try injecting directly into fbsbx iframe DOM
                try:
                    # Look for any textarea or input that might accept the token
                    textareas = self.driver.find_elements(By.TAG_NAME, "textarea")
                    inputs = self.driver.find_elements(By.TAG_NAME, "input")
                    
                    self.log(f"🔍 Found {len(textareas)} textareas and {len(inputs)} inputs")
                    
                    # Try to inject into first textarea
                    if textareas:
                        ta = textareas[0]
                        self.driver.execute_script("""
                            const el = arguments[0], v = arguments[1];
                            el.value = v;
                            ['input','change','blur'].forEach(e => 
                                el.dispatchEvent(new Event(e, {bubbles: true}))
                            );
                        """, ta, token)
                        self.log("✅ Token injected into textarea")
                        self.driver.switch_to.default_content()
                        return True
                    
                    # Try to inject into first input
                    if inputs:
                        inp = inputs[0]
                        self.driver.execute_script("""
                            const el = arguments[0], v = arguments[1];
                            el.value = v;
                            ['input','change','blur'].forEach(e => 
                                el.dispatchEvent(new Event(e, {bubbles: true}))
                            );
                        """, inp, token)
                        self.log("✅ Token injected into input")
                        self.driver.switch_to.default_content()
                        return True
                        
                except Exception as e:
                    self.log(f"❌ Failed to inject token directly: {e}")
                
                self.driver.switch_to.default_content()
                return False
            
            # Submit token to bframe iframes (if any found)
            ok = False
            if bframes:  # Only proceed if bframes were found
                for i, bf in enumerate(bframes):
                    try:
                        self.driver.switch_to.frame(bf)
                        self.log(f"🔧 Submitting token to bframe {i+1}...")
                        
                        # Look for textarea elements in bframe with explicit wait
                        from selenium.webdriver.support.ui import WebDriverWait as W
                        from selenium.webdriver.support import expected_conditions as EC
                        
                        try:
                            # Wait for textarea to appear (up to 10 seconds)
                            ta = W(self.driver, 10).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea[name="g-recaptcha-response"]'))
                            )
                            
                            self.log(f"✅ Found textarea in bframe {i+1}")
                            
                            # Submit token with proper event dispatching
                            self.driver.execute_script("""
                                var ta = arguments[0], val = arguments[1];
                                ta.value = val;
                                ta.dispatchEvent(new Event('input', {bubbles:true}));
                                ta.dispatchEvent(new Event('change', {bubbles:true}));
                            """, ta, token)
                            
                            self.log(f"✅ Token submitted to bframe {i+1}")
                            ok = True
                            
                        except Exception as wait_e:
                            self.log(f"⚠️ No textarea found in bframe {i+1} after waiting: {wait_e}")
                            
                            # Try alternative selectors
                            alt_selectors = [
                                'textarea#g-recaptcha-response',
                                'textarea[name="g-recaptcha-enterprise-response"]',
                                'textarea',
                                'input[name="g-recaptcha-response"]'
                            ]
                            
                            for selector in alt_selectors:
                                try:
                                    tas = self.driver.find_elements(By.CSS_SELECTOR, selector)
                                    if tas:
                                        ta = tas[0]
                                        self.driver.execute_script("""
                                            var ta = arguments[0], val = arguments[1];
                                            ta.value = val;
                                            ta.dispatchEvent(new Event('input', {bubbles:true}));
                                            ta.dispatchEvent(new Event('change', {bubbles:true}));
                                        """, ta, token)
                                        self.log(f"✅ Token submitted to bframe {i+1} using selector: {selector}")
                                        ok = True
                                        break
                                except Exception:
                                    continue
                            
                            if not ok:
                                self.log(f"❌ No suitable elements found in bframe {i+1}")
                        
                        self.driver.switch_to.parent_frame()
                        
                    except Exception as e:
                        self.log(f"❌ Error submitting to bframe {i+1}: {e}")
                        self.driver.switch_to.parent_frame()
            
            self.driver.switch_to.default_content()
            
            if ok:
                self.log("✅ Token successfully submitted to bframe")
                # Also try Meta-optimized delivery as backup
                self.log("🔧 Also delivering token via Meta method as backup...")
                result = self.deliver_token_meta(token)
                return True
            else:
                self.log("❌ Failed to submit token to any bframe")
                # Try Meta-optimized delivery as primary method (recommended for Meta/FBSBX)
                self.log("🔧 Trying Meta-optimized delivery as primary method...")
                result = self.deliver_token_meta(token)
                if result['delivered']:
                    return True
                
                # Try fbsbx form injection as fallback
                self.log("🔧 Trying fbsbx form injection as fallback...")
                if self.inject_token_in_fbsbx_form(token):
                    return True
                
                return False
            
        except Exception as e:
            self.log(f"❌ Error submitting reCAPTCHA token: {e}")
            return False
    
    def inject_token_in_fbsbx_form(self, token):
        """
        Inject reCAPTCHA token in fbsbx iframe form
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token injected successfully
        """
        try:
            self.log("🔧 Injecting token in fbsbx form...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to fbsbx iframe
            self.driver.switch_to.default_content()
            
            try:
                fbsbx_iframe = W(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'iframe[src*="fbsbx.com/captcha/recaptcha/iframe"]'))
                )
                self.driver.switch_to.frame(fbsbx_iframe)
                self.log("✅ Switched to fbsbx iframe")
            except Exception as e:
                self.log(f"❌ Could not switch to fbsbx iframe: {e}")
                return False
            
            # Try to find existing hidden input
            try:
                existing_input = self.driver.find_element(By.CSS_SELECTOR, 
                    'textarea#g-recaptcha-response, input#g-recaptcha-response, textarea[name="g-recaptcha-response"], input[name="g-recaptcha-response"]')
                
                self.driver.execute_script("""
                    var inp = arguments[0], val = arguments[1];
                    inp.value = val;
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                """, existing_input, token)
                
                self.log("✅ Token injected into existing reCAPTCHA response field")
                self.driver.switch_to.default_content()
                return True
                
            except Exception:
                # Create hidden input if none exists
                self.log("🔧 No existing reCAPTCHA response field found, creating one...")
                
                success = self.driver.execute_script("""
                    var val = arguments[0];
                    var inp = document.createElement('textarea');
                    inp.id = 'g-recaptcha-response';
                    inp.name = 'g-recaptcha-response';
                    inp.style.display = 'none';
                    inp.value = val;
                    document.body.appendChild(inp);
                    
                    // Trigger events
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                    
                    // Also try to trigger reCAPTCHA callback
                    if (typeof window.grecaptchaCallback === 'function') {
                        window.grecaptchaCallback(val);
                    }
                    
                    return true;
                """, token)
                
                if success:
                    self.log("✅ Token injected via created hidden field")
                    self.driver.switch_to.default_content()
                    return True
                else:
                    self.log("❌ Failed to create hidden field")
                    self.driver.switch_to.default_content()
                    return False
                    
        except Exception as e:
            self.log(f"❌ Error injecting token in fbsbx form: {e}")
            self.driver.switch_to.default_content()
            return False

    def deliver_token_meta(self, token):
        """
        Deliver reCAPTCHA token with reset, callback, and validation (Meta/FBSBX optimized)
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            dict: {'delivered': bool, 'hasResponse': bool}
        """
        try:
            self.log("🔧 Delivering token with reset and validation...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to fbsbx iframe (where widget is rendered)
            self.driver.switch_to.default_content()
            
            try:
                fbsbx_iframe = W(self.driver, 15).until(
                    EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, 'iframe[src*="fbsbx.com/captcha/recaptcha/iframe"]'))
                )
                self.log("✅ Switched to fbsbx iframe for token delivery")
            except Exception as e:
                self.log(f"❌ Could not switch to fbsbx iframe: {e}")
                return {'delivered': False, 'hasResponse': False}
            
            # Execute comprehensive token delivery with reset and validation
            result = self.driver.execute_script("""
                const token = arguments[0];
                
                function findWidgets() {
                    const cfg = window.___grecaptcha_cfg || {};
                    const clients = cfg.clients || {};
                    const widgets = [];
                    for (const id in clients) {
                        const c = clients[id];
                        widgets.push(c);
                    }
                    return widgets;
                }
                
                // 1) Try to reset widget(s) to clear any old state
                try {
                    if (window.grecaptcha && window.grecaptcha.reset) {
                        // Check for explicit widgetIds
                        const ids = (window.___grecaptcha_cfg && ___grecaptcha_cfg.widgetIds) || [];
                        if (Array.isArray(ids) && ids.length) {
                            ids.forEach(id => { 
                                try { 
                                    grecaptcha.reset(id); 
                                    console.log('Reset widget:', id);
                                } catch(e){} 
                            });
                        } else {
                            // Universal reset if no widgetIds
                            try { 
                                grecaptcha.reset(); 
                                console.log('Universal reset executed');
                            } catch(e){}
                        }
                    }
                } catch(e){
                    console.log('Reset failed:', e);
                }
                
                // 2) Find universal callback inside clients
                function findCallback(obj, depth=0) {
                    if (!obj || depth>7) return null;
                    for (const k in obj) {
                        const v = obj[k];
                        if (typeof v === 'function') return v;
                        if (v && typeof v === 'object') {
                            const r = findCallback(v, depth+1);
                            if (r) return r;
                        }
                    }
                    return null;
                }
                
                let delivered = false;
                const widgets = findWidgets();
                for (const w of widgets) {
                    let cb = findCallback(w);
                    if (cb) {
                        try { 
                            cb(token); 
                            console.log('Callback executed successfully');
                            delivered = true; 
                            break; 
                        } catch(e){
                            console.log('Callback execution failed:', e);
                        }
                    }
                }
                
                // 3) Fallback: create hidden textarea if page reads it
                if (!delivered) {
                    try {
                        let el = document.querySelector('textarea#g-recaptcha-response, input#g-recaptcha-response, [name="g-recaptcha-response"]');
                        if (!el) {
                            el = document.createElement('textarea');
                            el.id = 'g-recaptcha-response';
                            el.name = 'g-recaptcha-response';
                            el.style.display='none';
                            document.body.appendChild(el);
                        }
                        el.value = token;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                        console.log('Token set in hidden textarea');
                        delivered = true;
                    } catch(e){
                        console.log('Textarea fallback failed:', e);
                    }
                }
                
                // 4) Validation: try to get response from grecaptcha if possible
                let hasResponse = false;
                try {
                    if (window.grecaptcha && window.grecaptcha.getResponse) {
                        const r = grecaptcha.getResponse();
                        hasResponse = !!(r && r.length > 10);
                        console.log('Response validation:', hasResponse ? 'Valid' : 'Invalid');
                    }
                } catch(e){
                    console.log('Response validation failed:', e);
                }
                
                // 5) Trigger postMessage for some builds
                try { 
                    window.postMessage({event:'grecaptcha-token', value:'set'}, '*'); 
                    console.log('PostMessage triggered');
                } catch(e){
                    console.log('PostMessage failed:', e);
                }
                
                return {delivered, hasResponse};
            """, token)
            
            self.driver.switch_to.default_content()
            
            if result['delivered']:
                self.log("✅ Token delivered successfully")
                if result['hasResponse']:
                    self.log("✅ Response validation passed")
                else:
                    self.log("⚠️ Response validation failed, but token was delivered")
            else:
                self.log("❌ Failed to deliver token")
            
            return result
                
        except Exception as e:
            self.log(f"❌ Error in deliver_token_meta: {e}")
            self.driver.switch_to.default_content()
            return {'delivered': False, 'hasResponse': False}

    def deliver_token_via_callback(self, token):
        """
        Deliver reCAPTCHA token via grecaptcha callback (legacy method)
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token delivered successfully
        """
        # Use the new Meta-optimized delivery method
        result = self.deliver_token_meta(token)
        return result['delivered']

    def inject_token_via_grecaptcha_callback(self, token):
        """
        Inject reCAPTCHA token via ___grecaptcha_cfg callback (legacy method)
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token injected successfully
        """
        # Use the new callback delivery method
        return self.deliver_token_via_callback(token)

    def wait_for_bframe_inside_fbsbx(self, tries=20):
        """
        Wait for bframe to appear inside fbsbx iframe (sometimes loads dynamically)
        
        Args:
            tries: Number of attempts to wait for bframe
            
        Returns:
            bool: True if bframe found
        """
        try:
            self.log("🔍 Waiting for bframe to appear inside fbsbx...")
            
            from selenium.webdriver.common.by import By
            import time
            
            for attempt in range(tries):
                try:
                    # Switch to fbsbx iframe
                    self.driver.switch_to.default_content()
                    fbsbx_iframes = self.driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="fbsbx.com/captcha/recaptcha/iframe"]')
                    
                    if fbsbx_iframes:
                        self.driver.switch_to.frame(fbsbx_iframes[0])
                        
                        # Look for bframe iframes
                        bframes = []
                        all_iframes = self.driver.find_elements(By.TAG_NAME, 'iframe')
                        
                        for iframe in all_iframes:
                            src = iframe.get_attribute('src') or ''
                            if ('/recaptcha/api2/bframe' in src or '/enterprise/bframe' in src):
                                bframes.append(iframe)
                        
                        self.driver.switch_to.default_content()
                        
                        if bframes:
                            self.log(f"✅ Found {len(bframes)} bframe(s) on attempt {attempt + 1}")
                            return True
                    
                    self.log(f"🔍 Attempt {attempt + 1}/{tries}: No bframe found, waiting...")
                    time.sleep(0.5)
                    
                except Exception as e:
                    self.log(f"⚠️ Error on attempt {attempt + 1}: {e}")
                    self.driver.switch_to.default_content()
                    time.sleep(0.5)
                    continue
            
            self.log("⚠️ bframe never appeared after waiting")
            return False
            
        except Exception as e:
            self.log(f"❌ Error waiting for bframe: {e}")
            self.driver.switch_to.default_content()
            return False

    def click_next_and_wait_recaptcha_gone(self):
        """
        Click Next button and wait for recaptcha frame to disappear
        
        Returns:
            bool: True if recaptcha frame disappeared successfully
        """
        try:
            self.log("🔧 Clicking Next button and waiting for recaptcha to disappear...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to default content first
            self.driver.switch_to.default_content()
            
            # Click Next/Continue button
            try:
                next_btn = W(self.driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH, 
                        "//*[contains(text(),'Next') or contains(text(),'Continue')]/ancestor::*[self::button or @role='button']"
                    ))
                )
                self.driver.execute_script("arguments[0].click();", next_btn)
                self.log("✅ Clicked Next button")
            except Exception as e:
                self.log(f"⚠️ Could not find/click Next button: {e}")
            
            # Wait for recaptcha frame to disappear or next step to appear
            try:
                self.log("🔧 Waiting for reCAPTCHA frame to disappear or next step...")
                
                from selenium.common.exceptions import TimeoutException
                
                # Wait for fbsbx iframe to disappear
                try:
                    W(self.driver, 20).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']")) == 0
                    )
                    self.log("✅ fbsbx reCAPTCHA frame disappeared")
                except TimeoutException:
                    self.log("⚠️ fbsbx frame did not disappear, checking for next step...")
                
                # Wait for any reCAPTCHA iframes to disappear
                try:
                    W(self.driver, 15).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha/api2'], iframe[src*='recaptcha/enterprise']")) == 0
                    )
                    self.log("✅ All reCAPTCHA iframes disappeared")
                except TimeoutException:
                    self.log("⚠️ Some reCAPTCHA iframes still present")
                
                # Check for next step (SMS verification)
                try:
                    W(self.driver, 10).until(
                        lambda d: len(d.find_elements(By.XPATH, "//*[contains(., 'SMS') or contains(., 'verification') or contains(., 'code') or contains(., 'phone')]")) > 0
                    )
                    self.log("✅ Next step (SMS verification) detected - reCAPTCHA passed!")
                    return True
                except TimeoutException:
                    pass
                
                # Additional check for page change (URL change or content change)
                import time
                time.sleep(2)  # Give page time to process
                
                # Check if we're still on the same page or if content changed
                current_url = self.driver.current_url
                if 'suspended' not in current_url.lower() or 'captcha' not in current_url.lower():
                    self.log("✅ Page changed - reCAPTCHA successfully bypassed")
                    return True
                
                # Check for any visible reCAPTCHA elements
                recaptcha_elements = self.driver.find_elements(By.CSS_SELECTOR, 
                    ".g-recaptcha, [data-sitekey], iframe[src*='recaptcha']")
                if len(recaptcha_elements) == 0:
                    self.log("✅ No visible reCAPTCHA elements found")
                    return True
                else:
                    self.log(f"⚠️ Still found {len(recaptcha_elements)} reCAPTCHA elements")
                    return True  # Still consider it successful if frames disappeared
                
            except Exception as e:
                self.log(f"❌ Error waiting for reCAPTCHA to disappear: {e}")
                
                # Final check - maybe it's invisible but still working
                try:
                    # Check if there are any error messages or if we can proceed
                    error_elements = self.driver.find_elements(By.CSS_SELECTOR, 
                        "[class*='error'], [class*='invalid'], .recaptcha-error")
                    if len(error_elements) == 0:
                        self.log("✅ No reCAPTCHA errors found - assuming success")
                        return True
                    else:
                        self.log(f"❌ Found {len(error_elements)} error elements")
                        return False
                except Exception:
                    return False
                
        except Exception as e:
            self.log(f"❌ Error in click_next_and_wait_recaptcha_gone: {e}")
            return False
    
    def submit_token_via_parent_callback(self, token):
        """
        Submit token via parent fbsbx frame callback (correct approach for Enterprise)
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token submitted successfully
        """
        try:
            self.log("🔧 Trying parent callback method in fbsbx frame...")
            
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Switch to fbsbx iframe first
            self.driver.switch_to.default_content()
            
            try:
                fb_iframe = W(self.driver, 15).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, 
                        "iframe[src*='fbsbx.com/captcha/recaptcha/iframe']"
                    ))
                )
                self.driver.switch_to.frame(fb_iframe)
                self.log("✅ Switched to fbsbx iframe")
            except Exception as e:
                self.log(f"❌ Could not switch to fbsbx iframe: {e}")
                self.driver.switch_to.default_content()
                return False
            
            # Inject token via parent callback and textarea fields
            success = self.driver.execute_script("""
                (function(token){
                    try {
                        let success = false;
                        
                        // 1) Fill textarea fields in parent DOM if they exist
                        var sels = [
                            'textarea#g-recaptcha-response',
                            'textarea#g-recaptcha-response-100000',
                            'textarea[name="g-recaptcha-response"]'
                        ];
                        for (var i=0;i<sels.length;i++){
                            var el = document.querySelector(sels[i]);
                            if (el){
                                console.log('Found textarea:', sels[i]);
                                el.value = token;
                                el.dispatchEvent(new Event('input', {bubbles:true}));
                                el.dispatchEvent(new Event('change', {bubbles:true}));
                                success = true;
                            }
                        }

                        // 2) Try to find and call callback from ___grecaptcha_cfg
                        var cfg = window.___grecaptcha_cfg;
                        if (cfg && cfg.clients){
                            var called = false;
                            for (var id in cfg.clients){
                                var c = cfg.clients[id];
                                // Different branches in different versions
                                var cb = (c && (
                                    c.V?.V?.callback || c.W?.W?.callback || c.X?.X?.callback || 
                                    c.h?.h?.callback || c.l?.l?.callback
                                ));
                                if (typeof cb === 'function'){
                                    try { 
                                        console.log('Calling grecaptcha callback');
                                        cb(token); 
                                        called = true; 
                                        success = true;
                                    } catch(e){
                                        console.log('Callback call failed:', e);
                                    }
                                }
                                
                                // Sometimes callback is deeper in arrays
                                if (!called) {
                                    for (var k in c){
                                        try {
                                            var maybe = c[k];
                                            if (maybe && typeof maybe.callback === 'function'){ 
                                                console.log('Found callback in array');
                                                maybe.callback(token); 
                                                called = true; 
                                                success = true;
                                                break; 
                                            }
                                            if (maybe && typeof maybe === 'object'){
                                                for (var k2 in maybe){
                                                    var m2 = maybe[k2];
                                                    if (m2 && typeof m2.callback === 'function'){ 
                                                        console.log('Found callback in nested array');
                                                        m2.callback(token); 
                                                        called = true; 
                                                        success = true;
                                                        break; 
                                                    }
                                                }
                                            }
                                        } catch(e){
                                            console.log('Array callback search failed:', e);
                                        }
                                        if (called) break;
                                    }
                                }
                            }
                        }
                        
                        console.log('Parent callback method result:', success);
                        return success;
                    } catch(e){
                        console.log('Parent callback method error:', e);
                        return false;
                    }
                })(arguments[0]);
            """, token)
            
            if success:
                self.log("✅ Token injected via parent callback method")
            else:
                self.log("❌ Parent callback method failed")
            
            # Switch back to main document
            self.driver.switch_to.default_content()
            
            # Click Next button and wait for page change
            return self.click_next_and_wait()
            
        except Exception as e:
            self.log(f"❌ Error in parent callback method: {e}")
            try:
                self.driver.switch_to.default_content()
            except:
                pass
            return False

    def submit_token_via_grecaptcha_hook(self, token):
        """
        Submit token via grecaptcha hook method (alternative approach)
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token submitted successfully
        """
        try:
            self.log("🔧 Trying grecaptcha hook method...")
            
            # Switch back to main document
            self.driver.switch_to.default_content()
            
            # Inject comprehensive grecaptcha hook with callback trigger
            self.driver.execute_script("""
                window.__igSolvedToken = arguments[0];
                
                (function attachComprehensiveHook(){
                    function hookAll() {
                        try {
                            const g = window.grecaptcha;
                            if (!g) return false;
                            
                            const ent = g.enterprise || g;
                            if (!ent) return false;
                            
                            let hooked = false;
                            
                            // Hook execute method
                            if (typeof ent.execute === 'function') {
                                const origExecute = ent.execute.bind(ent);
                                ent.execute = function(siteKey, opts) {
                                    console.log('grecaptcha.execute hooked, returning solved token');
                                    return Promise.resolve(window.__igSolvedToken);
                                };
                                hooked = true;
                            }
                            
                            // Hook getResponse method
                            if (typeof ent.getResponse === 'function') {
                                const origGetResponse = ent.getResponse.bind(ent);
                                ent.getResponse = function() {
                                    console.log('grecaptcha.getResponse hooked, returning solved token');
                                    return window.__igSolvedToken;
                                };
                                hooked = true;
                            }
                            
                            // Try to trigger existing callback immediately
                            try {
                                const cfg = window.___grecaptcha_cfg;
                                if (cfg && cfg.clients) {
                                    for (const client of Object.values(cfg.clients)) {
                                        for (const widget of Object.values(client)) {
                                            if (widget && typeof widget.callback === 'function') {
                                                console.log('Triggering existing callback with solved token');
                                                widget.callback(window.__igSolvedToken);
                                                hooked = true;
                                            }
                                        }
                                    }
                                }
                            } catch (e) {
                                console.log('Callback trigger failed:', e);
                            }
                            
                            return hooked;
                        } catch (e) { 
                            console.log('Hook error:', e);
                            return false; 
                        }
                    }
                    
                    // Try to hook immediately
                    if (hookAll()) {
                        console.log('Comprehensive grecaptcha hook attached immediately');
                        return;
                    }
                    
                    // Wait for grecaptcha to load
                    let tries = 0;
                    const interval = setInterval(() => {
                        tries++;
                        if (hookAll() || tries > 100) {
                            clearInterval(interval);
                            if (tries <= 100) {
                                console.log('Comprehensive grecaptcha hook attached after', tries, 'tries');
                            } else {
                                console.log('Comprehensive grecaptcha hook failed after 100 tries');
                            }
                        }
                    }, 100);
                })();
            """, token)
            
            self.log("✅ Grecaptcha hook injected")
            
            # Click Next button to trigger execute
            return self.click_next_and_wait()
            
        except Exception as e:
            self.log(f"❌ Error in grecaptcha hook method: {e}")
            return False

    def submit_token_to_parent_document(self, token):
        """
        Submit token to parent document elements
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token submitted successfully
        """
        try:
            self.log("🔧 Submitting token to parent document...")
            
            # Switch to main document
            self.driver.switch_to.default_content()
            
            # Try to find and submit to any recaptcha-related elements in parent
            success = self.driver.execute_script("""
                const token = arguments[0];
                let success = false;
                
                // Method 1: Look for hidden textarea/input in parent document
                const selectors = [
                    'textarea#g-recaptcha-response',
                    'textarea[name="g-recaptcha-response"]',
                    'textarea[name="g-recaptcha-enterprise-response"]',
                    'input[name="g-recaptcha-response"]',
                    'input[id="g-recaptcha-response"]',
                    'textarea[id*="recaptcha"]',
                    'input[id*="recaptcha"]',
                    'textarea[name*="recaptcha"]',
                    'input[name*="recaptcha"]'
                ];
                
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        console.log('Found parent element with selector:', selector);
                        el.value = token;
                        ['input','change','blur'].forEach(e => 
                            el.dispatchEvent(new Event(e, {bubbles: true}))
                        );
                        success = true;
                    }
                }
                
                // Method 2: Try to trigger grecaptcha callback
                try {
                    if (window.grecaptcha && window.grecaptcha.enterprise) {
                        const cfg = window.___grecaptcha_cfg;
                        if (cfg && cfg.clients) {
                            for (const client of Object.values(cfg.clients)) {
                                for (const widget of Object.values(client)) {
                                    if (widget && typeof widget.callback === 'function') {
                                        console.log('Calling grecaptcha callback');
                                        widget.callback(token);
                                        success = true;
                                    }
                                }
                            }
                        }
                    }
                } catch (e) {
                    console.log('Callback method failed:', e);
                }
                
                console.log('Parent document token submission result:', success);
                return success;
            """, token)
            
            if success:
                self.log("✅ Token submitted to parent document")
            else:
                self.log("❌ No suitable elements found in parent document")
            
            return self.click_next_and_wait()
            
        except Exception as e:
            self.log(f"❌ Error submitting to parent document: {e}")
            return False

    def submit_token_fallback_in_fbsbx(self, token):
        """
        Fallback method to submit token directly in fbsbx iframe
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token submitted successfully
        """
        try:
            self.log("🔧 Fallback: Submitting token directly in fbsbx iframe...")
            
            # We're already in fbsbx iframe, try to find textarea here
            success = self.driver.execute_script("""
                const t = arguments[0];
                const sels = [
                    'textarea#g-recaptcha-response',
                    'textarea[name="g-recaptcha-response"]',
                    'textarea[name="g-recaptcha-enterprise-response"]',
                    'textarea[id*="recaptcha"]',
                    'textarea[name*="recaptcha"]'
                ];
                let ok = false;
                
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) {
                        console.log('Found textarea in fbsbx:', s);
                        el.value = t;
                        ['input','change','blur'].forEach(e => 
                            el.dispatchEvent(new Event(e, {bubbles: true}))
                        );
                        ok = true;
                    }
                }
                
                console.log('Fallback token submission result in fbsbx:', ok);
                return ok;
            """, token)
            
            if success:
                self.log("✅ Token submitted via fallback method in fbsbx")
            else:
                self.log("❌ No textarea fields found even in fbsbx fallback")
            
            # Switch back to main document
            self.driver.switch_to.default_content()
            
            # Click Next button
            return self.click_next_and_wait()
            
        except Exception as e:
            self.log(f"❌ Error in fallback method: {e}")
            try:
                self.driver.switch_to.default_content()
            except:
                pass
            return False

    def captcha_cleared(self, timeout=8.0):
        """
        Check if captcha is actually cleared (no more anchor/bframe iframes)
        
        Args:
            timeout: Maximum time to wait for captcha to clear
            
        Returns:
            bool: True if captcha is cleared
        """
        try:
            import time
            from selenium.webdriver.common.by import By
            
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    # Check if there are no more recaptcha iframes in fbsbx
                    iframes = self.driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="recaptcha/api2/"]')
                    if not iframes:
                        self.log("✅ Captcha cleared - no more recaptcha iframes")
                        return True
                        
                    # Check if SMS elements appeared (next checkpoint)
                    sms_elements = self.driver.find_elements(By.XPATH, "//*[contains(.,'phone') or contains(.,'mobile') or contains(.,'SMS')]")
                    if sms_elements:
                        self.log("✅ Captcha cleared - SMS verification appeared")
                        return True
                        
                except Exception as e:
                    self.log(f"⚠️ Error checking captcha state: {e}")
                    
                time.sleep(0.25)
                
            self.log("⚠️ Captcha validation timeout")
            return False
            
        except Exception as e:
            self.log(f"❌ Error in captcha_cleared: {e}")
            return False

    def click_next_and_wait(self):
        """Click Next button and wait for page change"""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait as W
            from selenium.webdriver.support import expected_conditions as EC
            
            # Click Next button
            self.log("🔧 Clicking Next button...")
            
            try:
                next_btn = W(self.driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH, 
                        "//*[contains(text(),'Next') or contains(text(),'Continue')]/ancestor::*[self::button or @role='button']"
                    ))
                )
                self.driver.execute_script("arguments[0].click();", next_btn)
                self.log("✅ Next button clicked")
                
            except Exception as e:
                self.log(f"⚠️ Could not click Next button: {e}")
                if self.click_next_button():
                    self.log("✅ Next button clicked via fallback method")
                else:
                    self.log("❌ Failed to click Next button")
                    return False
            
            # Wait for captcha to actually clear
            self.log("⏳ Waiting for captcha to clear...")
            
            if self.captcha_cleared(25.0):
                self.log("✅ Captcha successfully cleared!")
                return True
            else:
                self.log("⚠️ Captcha did not clear within timeout")
                return False
                
        except Exception as e:
            self.log(f"❌ Error in click_next_and_wait: {e}")
            return False

    def submit_token_fallback(self, token):
        """
        Fallback method to submit token in main content
        
        Args:
            token: reCAPTCHA solution token
            
        Returns:
            bool: True if token submitted successfully
        """
        try:
            # Look for token fields in main content
            token_selectors = [
                "textarea[name='g-recaptcha-response']",
                "textarea[id='g-recaptcha-response']",
                "textarea[name='g-recaptcha-response-enterprise']",
                "textarea[id='g-recaptcha-response-enterprise']",
                ".g-recaptcha-response"
            ]
            
            token_field = None
            for selector in token_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        token_field = element
                        self.log(f"✅ Found token field in main content: {selector}")
                        break
                    if token_field:
                        break
                except Exception as e:
                    continue
            
            if not token_field:
                self.log("❌ No token field found in main content")
                return False
            
            # Fill token field
            self.driver.execute_script("arguments[0].value = '';", token_field)
            self.driver.execute_script(f"arguments[0].value = '{token}';", token_field)
            
            # Trigger events
            self.driver.execute_script("""
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, token_field)
            
            self.log("✅ Token filled in main content field")
            
            # Try to click Next button
            return self.click_next_button()
            
        except Exception as e:
            self.log(f"❌ Error in fallback method: {e}")
            return False
    
    def click_next_button(self):
        """
        Try to click Next/Continue/Submit button
        
        Returns:
            bool: True if button clicked successfully
        """
        try:
            # Look for Next/Continue/Submit buttons
            button_selectors = [
                "//*[contains(text(), 'Next')]",
                "//*[contains(text(), 'Continue')]", 
                "//*[contains(text(), 'Submit')]",
                "//*[contains(text(), 'Confirm')]",
                "//button[@type='submit']",
                "//input[@type='submit']",
                "//button[contains(@class, 'submit')]",
                "//button[contains(@class, 'next')]",
                "//button[contains(@class, 'continue')]"
            ]
            
            for selector in button_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        if element.is_displayed():
                            # Try multiple click methods
                            try:
                                element.click()
                                self.log(f"✅ Next button clicked with selector: {selector}")
                                time.sleep(3)
                                return True
                            except:
                                try:
                                    # Try JavaScript click
                                    self.driver.execute_script("arguments[0].click();", element)
                                    self.log(f"✅ Next button clicked via JavaScript: {selector}")
                                    time.sleep(3)
                                    return True
                                except:
                                    continue
                except:
                    continue
            
            self.log("⚠️ Next button not found")
            return False
            
        except Exception as e:
            self.log(f"❌ Error clicking Next button: {e}")
            return False
    
    def find_captcha_image(self):
        """
        Знайти зображення капчі на сторінці та зберегти його
        
        Returns:
            str: Шлях до збереженого зображення або None
        """
        try:
            self.log("🔍 Пошук капчі на сторінці...")
            
            # Селектори для капчі
            captcha_selectors = [
                "img[src*='captcha']",
                "img[alt*='captcha']",
                "img[alt*='verification']",
                ".captcha img",
                "#captcha img",
                "img[src*='recaptcha']"
            ]
            
            captcha_element = None
            for selector in captcha_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():
                            captcha_element = element
                            break
                    if captcha_element:
                        break
                except:
                    continue
            
            if not captcha_element:
                self.log("❌ Капчу не знайдено на сторінці")
                return None
            
            # Отримуємо URL зображення
            captcha_url = captcha_element.get_attribute('src')
            if not captcha_url:
                self.log("❌ URL капчі не знайдено")
                return None
            
            # Завантажуємо зображення
            self.log("📥 Завантаження зображення капчі...")
            response = requests.get(captcha_url, timeout=30)
            
            if response.status_code != 200:
                self.log(f"❌ Помилка завантаження капчі: HTTP {response.status_code}")
                return None
            
            # Зберігаємо зображення
            captcha_path = f"captcha_{self.profile_id}_{int(time.time())}.png"
            with open(captcha_path, 'wb') as f:
                f.write(response.content)
            
            self.log(f"✅ Капчу збережено: {captcha_path}")
            return captcha_path
            
        except Exception as e:
            self.log(f"❌ Помилка пошуку капчі: {e}")
            return None
    
    def get_sms_number(self, service_name="ig"):
        """
        Отримати номер телефону для SMS верифікації через daisysms
        
        Args:
            service_name: Код сервісу для якого потрібен номер (ig = Instagram)
            
        Returns:
            dict: {'number': str, 'id': str} або None якщо не вдалося
        """
        try:
            self.log("📱 Запит номера телефону для SMS...")
            
            # Перевіряємо тестовий режим
            test_mode = APPEAL_SETTINGS.get('test_mode', False) or os.environ.get('APPEAL_TEST_MODE', 'false').lower() == 'true'
            
            if test_mode:
                self.log("🧪 ТЕСТОВИЙ РЕЖИМ: Симуляція отримання номера")
                return {
                    'number': '1234567890',
                    'id': 'test_number_id'
                }
            
            # Спочатку перевіряємо баланс
            balance = self.check_sms_balance()
            if balance is not None:
                self.log(f"💰 DaisySMS баланс: ${balance}")
                if balance < 0.50:  # Якщо баланс менше $0.50
                    self.log("⚠️ Низький баланс DaisySMS! Може не вистачити для SMS")
            
            # Запитуємо номер згідно з API daisysms
            get_number_url = f"{DAISYSMS_API_URL}"
            params = {
                'api_key': DAISYSMS_API_KEY,
                'action': 'getNumber',
                'service': service_name,  # ig для Instagram
                'max_price': '1.00'  # Максимальна ціна $1.00
            }
            
            response = requests.get(get_number_url, params=params, timeout=30)
            
            if response.status_code != 200:
                self.log(f"❌ Помилка запиту номера: HTTP {response.status_code}")
                return None
            
            result = response.text.strip()
            self.log(f"📊 Відповідь API: {result}")
            
            if result.startswith('ACCESS_NUMBER'):
                # Формат: ACCESS_NUMBER:ID:NUMBER
                parts = result.split(':')
                if len(parts) >= 3:
                    number_id = parts[1]
                    phone_number = parts[2]
                    
                    self.log(f"✅ Номер отримано: {phone_number} (ID: {number_id})")
                    return {
                        'number': phone_number,
                        'id': number_id
                    }
            
            # Перевіряємо інші можливі помилки
            if result == 'NO_NUMBERS':
                self.log("❌ Немає доступних номерів")
            elif result == 'NO_BALANCE':
                self.log("❌ Недостатньо коштів на балансі")
            elif result == 'WRONG_SERVICE':
                self.log("❌ Неправильний код сервісу")
            else:
                self.log(f"❌ Невідома помилка: {result}")
            
            return None
            
        except Exception as e:
            self.log(f"❌ Помилка запиту номера: {e}")
            return None
    
    def get_sms_code(self, number_id):
        """
        Отримати SMS код
        
        Args:
            number_id: ID номера телефону
            
        Returns:
            str: SMS код або None якщо не вдалося
        """
        try:
            self.log(f"📨 Очікування SMS коду для ID: {number_id}")
            
            start_time = time.time()
            
            # Перевіряємо тестовий режим з конфігурації або змінної середовища
            test_mode = APPEAL_SETTINGS.get('test_mode', False) or os.environ.get('APPEAL_TEST_MODE', 'false').lower() == 'true'
            test_code_received = False
            
            if test_mode:
                self.log("🧪 ТЕСТОВИЙ РЕЖИМ: Буде симулюватися SMS код")
            
            while time.time() - start_time < APPEAL_SETTINGS['sms_timeout']:
                # Тестовий режим - симулюємо отримання коду
                if test_mode and time.time() - start_time > 30 and not test_code_received:
                    test_code = "123456"  # Тестовий код
                    self.log(f"🧪 ТЕСТОВИЙ РЕЖИМ: Симуляція SMS коду: {test_code}")
                    return test_code
                
                check_url = f"{DAISYSMS_API_URL}"
                params = {
                    'api_key': DAISYSMS_API_KEY,
                    'action': 'getStatus',
                    'id': number_id
                }
                
                response = requests.get(check_url, params=params, timeout=30)
                
                if response.status_code == 200:
                    result = response.text.strip()
                    self.log(f"📊 Статус SMS: {result}")
                    
                    if result.startswith('STATUS_OK:'):
                        # Формат: STATUS_OK:CODE
                        code = result.split(':')[1]
                        self.log(f"✅ SMS код отримано: {code}")
                        return code
                    elif result == 'STATUS_WAIT_CODE':
                        elapsed = int(time.time() - start_time)
                        remaining = int(APPEAL_SETTINGS['sms_timeout'] - elapsed)
                        self.log(f"⏳ Очікування SMS коду... ({elapsed}s пройшло, {remaining}s залишилось)")
                        time.sleep(5)  # Чекаємо 5 секунд
                        continue
                    elif result == 'STATUS_CANCEL':
                        self.log("❌ SMS код скасовано")
                        return None
                    elif result == 'STATUS_WAIT_RETRY':
                        self.log("⏳ Очікування повторної спроби...")
                        time.sleep(10)
                        continue
                    elif result == 'STATUS_BAD_ACTION':
                        self.log("❌ Помилка дії API")
                        return None
                    elif result == 'STATUS_BAD_SERVICE':
                        self.log("❌ Помилка сервісу")
                        return None
                    else:
                        self.log(f"❌ Невідомий статус SMS: {result}")
                        time.sleep(10)
                        continue
                else:
                    self.log(f"❌ Помилка HTTP: {response.status_code}")
                    time.sleep(10)
                    continue
            
            elapsed = int(time.time() - start_time)
            self.log(f"⏰ Таймаут очікування SMS коду ({elapsed}s пройшло)")
            
            # Якщо це не тестовий режим, додаємо пораду
            if not test_mode:
                self.log("💡 Поради для SMS:")
                self.log("   - Перевірте баланс DaisySMS")
                self.log("   - Переконайтеся що номер підходить для Instagram")
                self.log("   - Спробуйте інший номер або сервіс")
            
            return None
            
        except Exception as e:
            self.log(f"❌ Помилка отримання SMS коду: {e}")
            return None
    
    def check_sms_balance(self):
        """
        Перевірити баланс SMS сервісу
        
        Returns:
            float: Баланс у доларах або None якщо не вдалося
        """
        try:
            self.log("💰 Перевірка балансу SMS сервісу...")
            
            check_url = f"{DAISYSMS_API_URL}"
            params = {
                'api_key': DAISYSMS_API_KEY,
                'action': 'getBalance'
            }
            
            response = requests.get(check_url, params=params, timeout=30)
            
            if response.status_code == 200:
                result = response.text.strip()
                
                if result.startswith('ACCESS_BALANCE:'):
                    balance = float(result.split(':')[1])
                    self.log(f"✅ Баланс: ${balance:.2f}")
                    return balance
                else:
                    self.log(f"❌ Помилка отримання балансу: {result}")
                    return None
            else:
                self.log(f"❌ Помилка HTTP: {response.status_code}")
                return None
                
        except Exception as e:
            self.log(f"❌ Помилка перевірки балансу: {e}")
            return None
    
    def generate_2fa_code(self, secret_key):
        """
        Генерація 2FA коду
        
        Args:
            secret_key: Секретний ключ для TOTP
            
        Returns:
            str: 2FA код або None якщо не вдалося
        """
        try:
            self.log("🔐 Генерація 2FA коду...")
            
            if not secret_key or secret_key.strip() == '':
                self.log("❌ Секретний ключ 2FA не вказано")
                return None
            
            # Очищаємо секретний ключ
            clean_secret = secret_key.replace(' ', '').upper()
            
            # Генеруємо TOTP код
            totp = pyotp.TOTP(clean_secret)
            current_code = totp.now()
            
            self.log(f"✅ 2FA код згенеровано: {current_code}")
            return current_code
            
        except Exception as e:
            self.log(f"❌ Помилка генерації 2FA коду: {e}")
            return None
    
    def upload_selfie(self, selfie_path=None):
        """
        Завантаження selfie для апеляції
        
        Args:
            selfie_path: Шлях до selfie файлу
            
        Returns:
            bool: True якщо успішно завантажено
        """
        try:
            self.log("📸 Завантаження selfie...")
            
            if not selfie_path:
                selfie_path = APPEAL_SETTINGS['selfie_path']
            
            if not os.path.exists(selfie_path):
                self.log(f"❌ Файл selfie не знайдено: {selfie_path}")
                return False
            
            # Знаходимо поле для завантаження файлу
            file_input_selectors = [
                "input[type='file']",
                "input[accept*='image']",
                "input[name*='photo']",
                "input[name*='image']",
                "input[name*='selfie']"
            ]
            
            file_input = None
            for selector in file_input_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():
                            file_input = element
                            break
                    if file_input:
                        break
                except:
                    continue
            
            if not file_input:
                self.log("❌ Поле для завантаження файлу не знайдено")
                return False
            
            # Завантажуємо файл
            absolute_path = os.path.abspath(selfie_path)
            file_input.send_keys(absolute_path)
            
            self.log(f"✅ Selfie завантажено: {selfie_path}")
            return True
            
        except Exception as e:
            self.log(f"❌ Помилка завантаження selfie: {e}")
            return False
    
    def handle_appeal_checkpoint(self, checkpoint_type, **kwargs):
        """
        Обробка різних типів чекпоінтів апеляції
        
        Args:
            checkpoint_type: Тип чекпоінту
            **kwargs: Додаткові параметри
            
        Returns:
            bool: True якщо чекпоінт оброблено успішно
        """
        try:
            self.log(f"🔄 Обробка чекпоінту: {checkpoint_type}")
            
            if checkpoint_type == "captcha":
                # Розв'язуємо капчу
                captcha_solution = self.solve_captcha()
                if captcha_solution:
                    # reCAPTCHA токен вже введено через grecaptcha hook
                    # Просто чекаємо на оновлення сторінки
                    self.log("✅ reCAPTCHA token submitted via grecaptcha hook")
                    time.sleep(3)
                    return True
                return False
            
            elif checkpoint_type == "email_confirmation":
                # Підтвердження email
                email_input = self.driver.find_element(By.CSS_SELECTOR, "input[name*='email']")
                email_input.clear()
                email_input.send_keys(APPEAL_EMAIL)
                
                submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                submit_button.click()
                
                time.sleep(3)
                return True
            
            elif checkpoint_type == "sms_verification":
                # SMS верифікація
                sms_number_info = self.get_sms_number()
                if not sms_number_info:
                    return False
                
                phone_number = sms_number_info['number']
                self.log(f"📞 Got phone number: {phone_number}")
                
                # Find phone input field and enter number
                phone_input_selectors = [
                    "//input[@type='tel']",
                    "//input[contains(@placeholder, 'Phone')]",
                    "//input[contains(@placeholder, 'phone')]",
                    "//input[contains(@name, 'phone')]",
                    "//input[contains(@id, 'phone')]"
                ]
                
                phone_entered = False
                for selector in phone_input_selectors:
                    try:
                        phone_input = self.driver.find_element(By.XPATH, selector)
                        if phone_input and phone_input.is_displayed():
                            phone_input.clear()
                            phone_input.send_keys(phone_number)
                            self.log(f"✅ Phone number entered: {phone_number}")
                            phone_entered = True
                            break
                    except:
                        continue
                
                if not phone_entered:
                    self.log("❌ Could not find phone input field")
                    return False
                
                # Find and click "Send code" button - search by text regardless of HTML structure
                # Instagram constantly changes their HTML structure, so we search by text content
                send_code_texts = [
                    "Send code",
                    "Send Code", 
                    "Send",
                    "Continue",
                    "Next",
                    "Submit"
                ]
                
                # Create selectors for any element containing these texts
                send_code_selectors = []
                for text in send_code_texts:
                    send_code_selectors.extend([
                        f"//*[contains(text(), '{text}')]",
                        f"//*[contains(normalize-space(text()), '{text}')]",
                        f"//*[text()='{text}']",
                        f"//*[normalize-space(text())='{text}']"
                    ])
                
                send_code_clicked = False
                for i, selector in enumerate(send_code_selectors):
                    try:
                        button = self.driver.find_element(By.XPATH, selector)
                        if button and button.is_displayed():
                            self.log(f"✅ Found send button with selector {i+1}: {selector}")
                            
                            # Спробуємо клікнути на сам елемент
                            try:
                                button.click()
                                self.log("✅ Send code button clicked")
                                send_code_clicked = True
                                time.sleep(5)  # Wait for SMS to be sent
                                break
                            except:
                                # Якщо не вдалося клікнути, спробуємо клікнути на батьківський елемент
                                try:
                                    parent = button.find_element(By.XPATH, "..")
                                    if parent:
                                        self.log("🔄 Trying to click parent element...")
                                        parent.click()
                                        self.log("✅ Parent element clicked")
                                        send_code_clicked = True
                                        time.sleep(5)
                                        break
                                except:
                                    # Якщо і батьківський елемент не клікається, спробуємо JavaScript click
                                    try:
                                        self.driver.execute_script("arguments[0].click();", button)
                                        self.log("✅ JavaScript click executed")
                                        send_code_clicked = True
                                        time.sleep(5)
                                        break
                                    except:
                                        self.log("⚠️ All click methods failed for this element")
                                        continue
                    except:
                        continue
                
                # Якщо не знайшли кнопку, спробуємо знайти всі елементи з текстом
                if not send_code_clicked:
                    self.log("🔍 Searching for all clickable elements with text on page...")
                    try:
                        # Шукаємо всі елементи які можуть бути кнопками
                        all_elements = self.driver.find_elements(By.XPATH, "//*[text() or @value or @placeholder]")
                        self.log(f"📋 Found {len(all_elements)} elements with text/value/placeholder:")
                        
                        for i, element in enumerate(all_elements):
                            try:
                                if element.is_displayed():
                                    text = element.text.strip() if hasattr(element, 'text') and element.text else element.get_attribute('value') or element.get_attribute('placeholder') or 'no text'
                                    tag = element.tag_name
                                    classes = element.get_attribute('class') or 'no class'
                                    role = element.get_attribute('role') or 'no role'
                                    data_bloks = element.get_attribute('data-bloks-name') or 'no bloks'
                                    
                                    # Показуємо тільки елементи з потенційно корисним текстом
                                    if any(keyword in text.lower() for keyword in ['send', 'code', 'continue', 'next', 'submit', 'confirm', 'verify']):
                                        self.log(f"   Element {i+1}: <{tag}> '{text}' role='{role}' class='{classes[:50]}...' bloks='{data_bloks}'")
                            except:
                                continue
                    except Exception as e:
                        self.log(f"⚠️ Error searching for elements: {e}")
                
                if not send_code_clicked:
                    self.log("⚠️ Send code button not found")
                    return False
                
                # Wait for SMS code and enter it
                self.log("📨 Waiting for SMS code...")
                sms_code = self.get_sms_code(sms_number_info['id'])
                
                if sms_code:
                    self.log(f"✅ SMS code received: {sms_code}")
                    
                    # Find SMS code input field
                    code_input_selectors = [
                        "//input[@type='text']",
                        "//input[contains(@placeholder, 'code')]",
                        "//input[contains(@placeholder, 'Code')]",
                        "//input[contains(@name, 'code')]",
                        "//input[contains(@id, 'code')]"
                    ]
                    
                    code_entered = False
                    for selector in code_input_selectors:
                        try:
                            code_input = self.driver.find_element(By.XPATH, selector)
                            if code_input and code_input.is_displayed():
                                code_input.clear()
                                code_input.send_keys(sms_code)
                                self.log(f"✅ SMS code entered: {sms_code}")
                                code_entered = True
                                break
                        except:
                            continue
                    
                    if not code_entered:
                        self.log("❌ Could not find SMS code input field")
                        return False
                    
                    # Find and click submit/confirm button - using adaptive search
                    confirm_texts = [
                        "Confirm",
                        "Verify", 
                        "Submit",
                        "Next",
                        "Continue"
                    ]
                    
                    # Create adaptive selectors for any element containing these texts
                    confirm_selectors = []
                    for text in confirm_texts:
                        confirm_selectors.extend([
                            f"//*[contains(text(), '{text}')]",
                            f"//*[contains(normalize-space(text()), '{text}')]",
                            f"//*[text()='{text}']",
                            f"//*[normalize-space(text())='{text}']"
                        ])
                    
                    # Also add button-specific selectors
                    confirm_selectors.extend([
                        "//button[@type='submit']",
                        "//input[@type='submit']",
                        "//button[contains(@class, 'submit')]",
                        "//button[contains(@class, 'confirm')]",
                        "//button[contains(@class, 'next')]"
                    ])
                    
                    confirm_clicked = False
                    for i, selector in enumerate(confirm_selectors):
                        try:
                            button = self.driver.find_element(By.XPATH, selector)
                            if button and button.is_displayed():
                                self.log(f"✅ Found confirm button with selector {i+1}: {selector}")
                                
                                # Try to click the element
                                try:
                                    button.click()
                                    self.log("✅ Confirm button clicked")
                                    confirm_clicked = True
                                    time.sleep(3)
                                    break
                                except:
                                    # Try clicking parent element
                                    try:
                                        parent = button.find_element(By.XPATH, "..")
                                        if parent:
                                            self.log("🔄 Trying to click parent element...")
                                            parent.click()
                                            self.log("✅ Parent element clicked")
                                            confirm_clicked = True
                                            time.sleep(3)
                                            break
                                    except:
                                        # Try JavaScript click
                                        try:
                                            self.driver.execute_script("arguments[0].click();", button)
                                            self.log("✅ JavaScript click executed")
                                            confirm_clicked = True
                                            time.sleep(3)
                                            break
                                        except:
                                            self.log("⚠️ All click methods failed for this element")
                                            continue
                        except:
                            continue
                    
                    # If we couldn't find a button, search for all clickable elements
                    if not confirm_clicked:
                        self.log("🔍 Searching for all clickable elements after SMS code entry...")
                        try:
                            all_elements = self.driver.find_elements(By.XPATH, "//*[text() or @value or @placeholder]")
                            self.log(f"📋 Found {len(all_elements)} elements with text/value/placeholder:")
                            
                            for i, element in enumerate(all_elements):
                                try:
                                    if element.is_displayed():
                                        text = element.text.strip() if hasattr(element, 'text') and element.text else element.get_attribute('value') or element.get_attribute('placeholder') or 'no text'
                                        tag = element.tag_name
                                        classes = element.get_attribute('class') or 'no class'
                                        
                                        # Show elements with potential button text
                                        if any(keyword in text.lower() for keyword in ['confirm', 'verify', 'submit', 'next', 'continue']):
                                            self.log(f"   Element {i+1}: <{tag}> '{text}' class='{classes[:50]}...'")
                                            
                                            # Try to click this element
                                            try:
                                                element.click()
                                                self.log(f"✅ Clicked element with text: '{text}'")
                                                confirm_clicked = True
                                                time.sleep(3)
                                                break
                                            except:
                                                # Try parent or JavaScript click
                                                try:
                                                    parent = element.find_element(By.XPATH, "..")
                                                    parent.click()
                                                    self.log(f"✅ Clicked parent of element: '{text}'")
                                                    confirm_clicked = True
                                                    time.sleep(3)
                                                    break
                                                except:
                                                    try:
                                                        self.driver.execute_script("arguments[0].click();", element)
                                                        self.log(f"✅ JavaScript clicked element: '{text}'")
                                                        confirm_clicked = True
                                                        time.sleep(3)
                                                        break
                                                    except:
                                                        continue
                                except:
                                    continue
                        except Exception as e:
                            self.log(f"⚠️ Error searching for elements: {e}")
                    
                    if confirm_clicked:
                        self.log("✅ SMS confirmation completed")
                        return True
                    else:
                        self.log("⚠️ Confirm button not found, but SMS code entered")
                        return True
                else:
                    self.log("❌ SMS code not received")
                    return False
            
            elif checkpoint_type == "human_confirm":
                # Human confirmation - click Continue/Next button
                self.log("🤖 Processing human confirmation checkpoint...")
                
                confirm_texts = [
                    "Continue",
                    "Next", 
                    "Confirm",
                    "Verify",
                    "Submit",
                    "I'm not a robot",
                    "I am not a robot"
                ]
                
                # Create adaptive selectors for any element containing these texts
                confirm_selectors = []
                for text in confirm_texts:
                    confirm_selectors.extend([
                        f"//*[contains(text(), '{text}')]",
                        f"//*[contains(normalize-space(text()), '{text}')]",
                        f"//*[text()='{text}']",
                        f"//*[normalize-space(text())='{text}']"
                    ])
                
                # Also add button-specific selectors
                confirm_selectors.extend([
                    "//button[@type='submit']",
                    "//input[@type='submit']",
                    "//button[contains(@class, 'submit')]",
                    "//button[contains(@class, 'continue')]",
                    "//button[contains(@class, 'next')]",
                    "//button[contains(@class, 'confirm')]"
                ])
                
                self.log(f"🔍 Searching with {len(confirm_selectors)} selectors for human confirm...")
                confirm_clicked = False
                for i, selector in enumerate(confirm_selectors):
                    try:
                        button = self.driver.find_element(By.XPATH, selector)
                        if button and button.is_displayed():
                            self.log(f"✅ Found confirm button with selector {i+1}: {selector}")
                            self.log(f"   Button text: '{button.text}', tag: {button.tag_name}")
                            
                            # Try to click the element
                            try:
                                button.click()
                                self.log("✅ Human confirm button clicked successfully")
                                confirm_clicked = True
                                time.sleep(3)
                                break
                            except:
                                # Try clicking parent element
                                try:
                                    parent = button.find_element(By.XPATH, "..")
                                    if parent:
                                        self.log("🔄 Trying to click parent element...")
                                        parent.click()
                                        self.log("✅ Parent element clicked")
                                        confirm_clicked = True
                                        time.sleep(3)
                                        break
                                except:
                                    # Try JavaScript click
                                    try:
                                        self.driver.execute_script("arguments[0].click();", button)
                                        self.log("✅ JavaScript click executed")
                                        confirm_clicked = True
                                        time.sleep(3)
                                        break
                                    except:
                                        self.log("⚠️ All click methods failed for this element")
                                        continue
                    except:
                        continue
                
                # If we couldn't find a button, search for all clickable elements
                if not confirm_clicked:
                    self.log("🔍 Searching for all clickable elements for human confirm...")
                    try:
                        all_elements = self.driver.find_elements(By.XPATH, "//*[text() or @value or @placeholder]")
                        self.log(f"📋 Found {len(all_elements)} elements with text/value/placeholder:")
                        
                        for i, element in enumerate(all_elements):
                            try:
                                if element.is_displayed():
                                    text = element.text.strip() if hasattr(element, 'text') and element.text else element.get_attribute('value') or element.get_attribute('placeholder') or 'no text'
                                    tag = element.tag_name
                                    classes = element.get_attribute('class') or 'no class'
                                    
                                    # Show elements with potential button text
                                    if any(keyword in text.lower() for keyword in ['continue', 'next', 'submit', 'confirm', 'verify', 'robot']):
                                        self.log(f"   Element {i+1}: <{tag}> '{text}' class='{classes[:50]}...'")
                                        
                                        # Try to click this element
                                        try:
                                            element.click()
                                            self.log(f"✅ Clicked element with text: '{text}'")
                                            confirm_clicked = True
                                            time.sleep(3)
                                            break
                                        except:
                                            # Try parent or JavaScript click
                                            try:
                                                parent = element.find_element(By.XPATH, "..")
                                                parent.click()
                                                self.log(f"✅ Clicked parent of element: '{text}'")
                                                confirm_clicked = True
                                                time.sleep(3)
                                                break
                                            except:
                                                try:
                                                    self.driver.execute_script("arguments[0].click();", element)
                                                    self.log(f"✅ JavaScript clicked element: '{text}'")
                                                    confirm_clicked = True
                                                    time.sleep(3)
                                                    break
                                                except:
                                                    continue
                            except:
                                continue
                    except Exception as e:
                        self.log(f"⚠️ Error searching for elements: {e}")
                
                # Final fallback - look specifically for "Continue" button
                if not confirm_clicked:
                    self.log("🔄 Final fallback - searching specifically for 'Continue' button...")
                    try:
                        # Look for any element containing "Continue" text
                        continue_elements = self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Continue') or contains(text(), 'CONTINUE')]")
                        self.log(f"🔍 Found {len(continue_elements)} elements containing 'Continue'")
                        
                        for i, element in enumerate(continue_elements):
                            try:
                                if element.is_displayed():
                                    text = element.text.strip()
                                    tag = element.tag_name
                                    self.log(f"   Continue element {i+1}: <{tag}> '{text}'")
                                    
                                    # Try to click this element
                                    try:
                                        element.click()
                                        self.log(f"✅ Clicked Continue element: '{text}'")
                                        confirm_clicked = True
                                        time.sleep(3)
                                        break
                                    except:
                                        # Try JavaScript click
                                        try:
                                            self.driver.execute_script("arguments[0].click();", element)
                                            self.log(f"✅ JavaScript clicked Continue element: '{text}'")
                                            confirm_clicked = True
                                            time.sleep(3)
                                            break
                                        except:
                                            continue
                            except:
                                continue
                    except Exception as e:
                        self.log(f"⚠️ Error in final fallback: {e}")
                
                if confirm_clicked:
                    self.log("✅ Human confirmation completed")
                    return True
                else:
                    self.log("⚠️ Human confirm button not found")
                    return False
            
            elif checkpoint_type == "selfie_verification":
                # Завантаження selfie
                return self.upload_selfie()
            
            elif checkpoint_type == "2fa_verification":
                # 2FA верифікація
                secret_key = kwargs.get('secret_key', '')
                if not secret_key:
                    return False
                
                code = self.generate_2fa_code(secret_key)
                if code:
                    code_input = self.driver.find_element(By.CSS_SELECTOR, "input[name*='code']")
                    code_input.clear()
                    code_input.send_keys(code)
                    
                    submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                    submit_button.click()
                    
                    time.sleep(3)
                    return True
                return False
            
            return False
            
        except Exception as e:
            self.log(f"❌ Помилка обробки чекпоінту {checkpoint_type}: {e}")
            return False
    
    def detect_appeal_checkpoint(self):
        """
        Детекція типу чекпоінту апеляції на поточній сторінці
        Використовує паралельну детекцію для підтримки рандомного порядку checkpoint'ів
        
        Returns:
            str: Тип чекпоінту або None якщо не виявлено
        """
        try:
            current_url = self.driver.current_url.lower()
            page_source = self.driver.page_source.lower()
            
            # ПАРАЛЕЛЬНА ДЕТЕКЦІЯ - перевіряємо ВСІ checkpoint'и одночасно
            # Instagram може показати будь-який checkpoint в будь-якому порядку
            
            # Phone number confirmation checkpoint
            phone_indicators = [
                "enter your mobile number",
                "confirm this mobile number", 
                "send code",
                "phone number",
                "mobile number",
                "you'll need to confirm this mobile number",
                "sms or whatsapp",
                "via sms",
                "via whatsapp"
            ]
            
            # reCAPTCHA checkpoint
            captcha_indicators = [
                "help us confirm it's you",
                "i'm not a robot",
                "recaptcha",
                "verify you're human",
                "security check"
            ]
            
            # Human confirmation checkpoint
            human_confirm_indicators = [
                "confirm you're human",
                "confirm you are human",
                "verify you are human"
            ]
            
            # Email confirmation checkpoint
            email_indicators = [
                "enter your email",
                "confirm your email",
                "email address",
                "verify your email"
            ]
            
            # 2FA checkpoint
            twofa_indicators = [
                "enter authentication code",
                "two-factor authentication",
                "authenticator app",
                "security code",
                "verification code"
            ]
            
            # Selfie checkpoint
            selfie_indicators = [
                "take a photo",
                "upload a photo",
                "selfie",
                "verify your identity"
            ]
            
            # Детектуємо який checkpoint присутній на сторінці
            detected_checkpoints = []
            
            if any(indicator in page_source for indicator in phone_indicators):
                detected_checkpoints.append("sms_verification")
            
            if any(indicator in page_source for indicator in captcha_indicators):
                # Додаткова перевірка для reCAPTCHA - шукаємо специфічні елементи
                try:
                    recaptcha_selectors = [
                        "iframe[src*='recaptcha']",
                        "div[class*='recaptcha']", 
                        "div[id*='recaptcha']",
                        ".g-recaptcha",
                        "#g-recaptcha",
                        "div[data-sitekey]",
                        "iframe[src*='google.com/recaptcha']"
                    ]
                    
                    for selector in recaptcha_selectors:
                        try:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                            for element in elements:
                                if element.is_displayed():
                                    detected_checkpoints.append("captcha")
                                    break
                            if "captcha" in detected_checkpoints:
                                break
                        except:
                            continue
                except Exception as e:
                    self.log(f"⚠️ Помилка пошуку reCAPTCHA: {e}")
                
            if any(indicator in page_source for indicator in human_confirm_indicators):
                detected_checkpoints.append("human_confirm")
                
            if any(indicator in page_source for indicator in email_indicators):
                detected_checkpoints.append("email_confirmation")
                
            if any(indicator in page_source for indicator in twofa_indicators):
                detected_checkpoints.append("2fa_verification")
                
            if any(indicator in page_source for indicator in selfie_indicators):
                detected_checkpoints.append("selfie_verification")
            
            # Return all detected checkpoints - let the caller decide which to handle
            if detected_checkpoints:
                self.log(f"🎯 Detected checkpoints: {detected_checkpoints}")
                return detected_checkpoints
            
            return None
            
        except Exception as e:
            self.log(f"❌ Помилка детекції чекпоінту: {e}")
            return None
    
    def run_appeal_flow(self, profile_info):
        """
        Запуск повного Appeal Flow
        
        Args:
            profile_info: Інформація про профіль (включає 2fa_secret)
            
        Returns:
            dict: Результат апеляції
        """
        try:
            self.log("🚀 Запуск Appeal Flow...")
            self.appeal_status = "APPEAL_STARTED"
            
            max_attempts = APPEAL_SETTINGS['max_appeal_attempts']
            attempt = 0
            
            while attempt < max_attempts:
                attempt += 1
                self.log(f"🔄 Спроба апеляції {attempt}/{max_attempts}")
                
                # Детектуємо всі доступні чекпоінти
                detected_checkpoints = self.detect_appeal_checkpoint()
                
                if not detected_checkpoints:
                    self.log("✅ Апеляція завершена успішно!")
                    self.appeal_status = "APPEAL_SUCCESS"
                    return {
                        'success': True,
                        'status': 'APPEAL_SUCCESS',
                        'message': 'Апеляція успішно завершена'
                    }
                
                # Обробляємо чекпоінти в порядку пріоритету, але адаптивно
                priority_order = ["human_confirm", "captcha", "sms_verification", "email_confirmation", "2fa_verification", "selfie_verification"]
                
                checkpoint_handled = False
                for priority_checkpoint in priority_order:
                    if priority_checkpoint in detected_checkpoints:
                        self.log(f"🎯 Обробляємо чекпоінт: {priority_checkpoint}")
                        
                        # Обробляємо чекпоінт
                        success = False
                        
                        if priority_checkpoint == "2fa_verification":
                            success = self.handle_appeal_checkpoint(
                                priority_checkpoint, 
                                secret_key=profile_info.get('2fa_secret', '')
                            )
                        else:
                            success = self.handle_appeal_checkpoint(priority_checkpoint)
                        
                        if success:
                            self.log(f"✅ Чекпоінт {priority_checkpoint} оброблено успішно")
                            checkpoint_handled = True
                            time.sleep(2)
                            break  # Переходимо до наступної спроби
                        else:
                            self.log(f"⚠️ Чекпоінт {priority_checkpoint} не вдалося обробити, спробуємо наступний")
                            continue  # Спробуємо наступний чекпоінт
                
                if not checkpoint_handled:
                    self.log(f"❌ Не вдалося обробити жоден з чекпоінтів: {detected_checkpoints}")
                    time.sleep(APPEAL_SETTINGS['retry_delay'])
            
            # Якщо всі спроби невдалі
            self.log("❌ Апеляція не вдалася після всіх спроб")
            self.appeal_status = "APPEAL_FAILED"
            return {
                'success': False,
                'status': 'APPEAL_FAILED',
                'message': 'Апеляція не вдалася після всіх спроб'
            }
            
        except Exception as e:
            self.log(f"❌ Критична помилка Appeal Flow: {e}")
            self.appeal_status = "APPEAL_FAILED"
            return {
                'success': False,
                'status': 'APPEAL_FAILED',
                'message': f'Критична помилка: {str(e)}'
            }
