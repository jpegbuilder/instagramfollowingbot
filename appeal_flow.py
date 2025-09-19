#!/usr/bin/env python3
"""
Instagram Appeal Flow - автоматичне відновлення заблокованих акаунтів
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

class InstagramAppealFlow:
    def __init__(self, driver, profile_id):
        """
        Ініціалізація Appeal Flow
        
        Args:
            driver: Selenium WebDriver
            profile_id: ID профілю для апеляції
        """
        self.driver = driver
        self.profile_id = profile_id
        self.thread_lock = threading.Lock()
        self.appeal_status = "APPEAL_STARTED"
        
    def log(self, message):
        """Thread-safe логування"""
        with self.thread_lock:
            print(f"🔄 [Profile {self.profile_id}] {message}")
    
    def solve_captcha(self, captcha_image_path=None):
        """
        Розв'язання reCAPTCHA v2 через 2captcha API
        
        Args:
            captcha_image_path: Шлях до зображення капчі (не використовується для reCAPTCHA v2)
            
        Returns:
            bool: True якщо капчу розв'язано успішно
        """
        try:
            self.log("🔐 Початок розв'язання reCAPTCHA v2...")
            
            # Знаходимо site key для reCAPTCHA
            site_key = self.find_recaptcha_site_key()
            if not site_key:
                self.log("❌ Site key для reCAPTCHA не знайдено")
                return False
            
            self.log(f"✅ Знайдено site key: {site_key}")
            
            # Отримуємо поточний URL
            page_url = self.driver.current_url
            
            # Відправляємо reCAPTCHA на 2captcha
            submit_url = f"{TWOCAPTCHA_API_URL}/in.php"
            submit_data = {
                'method': 'userrecaptcha',
                'key': TWOCAPTCHA_API_KEY,
                'googlekey': site_key,
                'pageurl': page_url,
                'json': 1
            }
            
            self.log("📤 Відправка reCAPTCHA на 2captcha...")
            response = requests.post(submit_url, data=submit_data, timeout=30)
            
            if response.status_code != 200:
                self.log(f"❌ Помилка відправки reCAPTCHA: HTTP {response.status_code}")
                return False
            
            result = response.json()
            
            if result.get('status') != 1:
                self.log(f"❌ Помилка відправки reCAPTCHA: {result.get('error_text', 'Unknown error')}")
                return False
            
            captcha_id = result.get('request')
            self.log(f"✅ reCAPTCHA відправлено, ID: {captcha_id}")
            
            # Очікуємо розв'язання
            self.log("⏳ Очікування розв'язання reCAPTCHA...")
            start_time = time.time()
            
            while time.time() - start_time < APPEAL_SETTINGS['captcha_timeout']:
                check_url = f"{TWOCAPTCHA_API_URL}/res.php"
                check_params = {
                    'key': TWOCAPTCHA_API_KEY,
                    'action': 'get',
                    'id': captcha_id,
                    'json': 1
                }
                
                response = requests.get(check_url, params=check_params, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    if result.get('status') == 1:
                        captcha_token = result.get('request')
                        self.log(f"✅ reCAPTCHA розв'язано, токен отримано")
                        
                        # Вводимо токен у відповідне поле
                        return self.submit_recaptcha_token(captcha_token)
                        
                    elif result.get('error_text') == 'CAPCHA_NOT_READY':
                        self.log("⏳ reCAPTCHA ще обробляється...")
                        time.sleep(10)  # Чекаємо 10 секунд
                        continue
                    else:
                        self.log(f"❌ Помилка розв'язання reCAPTCHA: {result.get('error_text', 'Unknown error')}")
                        return False
                
                time.sleep(10)
            
            self.log("⏰ Таймаут розв'язання reCAPTCHA")
            return False
            
        except Exception as e:
            self.log(f"❌ Помилка розв'язання reCAPTCHA: {e}")
            return False
    
    def find_recaptcha_site_key(self):
        """
        Знайти site key для reCAPTCHA на сторінці
        
        Returns:
            str: Site key або None якщо не знайдено
        """
        try:
            # Шукаємо data-sitekey атрибут
            site_key_selectors = [
                "div[data-sitekey]",
                ".g-recaptcha[data-sitekey]",
                "#g-recaptcha[data-sitekey]"
            ]
            
            for selector in site_key_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        site_key = element.get_attribute('data-sitekey')
                        if site_key:
                            return site_key
                except:
                    continue
            
            # Шукаємо в iframe
            try:
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    src = iframe.get_attribute('src')
                    if src and 'recaptcha' in src:
                        # Парсимо site key з URL
                        import re
                        match = re.search(r'k=([^&]+)', src)
                        if match:
                            return match.group(1)
            except:
                pass
            
            return None
            
        except Exception as e:
            self.log(f"❌ Помилка пошуку site key: {e}")
            return None
    
    def submit_recaptcha_token(self, token):
        """
        Ввести токен reCAPTCHA у відповідне поле
        
        Args:
            token: Токен розв'язання reCAPTCHA
            
        Returns:
            bool: True якщо токен успішно введено
        """
        try:
            # Шукаємо поле для токена
            token_selectors = [
                "textarea[name='g-recaptcha-response']",
                "textarea[id='g-recaptcha-response']",
                ".g-recaptcha-response",
                "textarea[data-sitekey]"
            ]
            
            token_field = None
            for selector in token_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():
                            token_field = element
                            break
                    if token_field:
                        break
                except:
                    continue
            
            if not token_field:
                self.log("❌ Поле для токена reCAPTCHA не знайдено")
                return False
            
            # Вводимо токен
            self.driver.execute_script(f"arguments[0].innerHTML = '{token}';", token_field)
            self.log("✅ Токен reCAPTCHA введено")
            
            # Натискаємо кнопку Next/Submit
            next_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:contains('Next')",
                "button:contains('Continue')",
                "button:contains('Submit')"
            ]
            
            for selector in next_selectors:
                try:
                    if ':contains(' in selector:
                        # XPath для contains
                        xpath = f"//button[contains(text(), '{selector.split(':contains(')[1].split(')')[0]}')]"
                        elements = self.driver.find_elements(By.XPATH, xpath)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed():
                            element.click()
                            self.log("✅ Кнопка Next натиснута")
                            time.sleep(3)
                            return True
                except:
                    continue
            
            self.log("⚠️ Кнопка Next не знайдена, але токен введено")
            return True
            
        except Exception as e:
            self.log(f"❌ Помилка введення токена: {e}")
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
                    # Вводимо рішення капчі
                    captcha_input = self.driver.find_element(By.CSS_SELECTOR, "input[name*='captcha']")
                    captcha_input.clear()
                    captcha_input.send_keys(captcha_solution)
                    
                    # Натискаємо кнопку підтвердження
                    submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                    submit_button.click()
                    
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
                    
                    # Find and click submit/confirm button
                    confirm_selectors = [
                        "//button[contains(text(), 'Confirm')]",
                        "//button[contains(text(), 'Verify')]",
                        "//button[contains(text(), 'Submit')]",
                        "//button[contains(text(), 'Next')]",
                        "//button[@type='submit']"
                    ]
                    
                    for selector in confirm_selectors:
                        try:
                            button = self.driver.find_element(By.XPATH, selector)
                            if button and button.is_displayed():
                                button.click()
                                self.log("✅ SMS confirmation completed")
                                time.sleep(3)
                                return True
                        except:
                            continue
                    
                    self.log("⚠️ Confirm button not found, but SMS code entered")
                    return True
                else:
                    self.log("❌ SMS code not received")
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
            
            # Обробляємо перший знайдений checkpoint
            if detected_checkpoints:
                checkpoint_type = detected_checkpoints[0]  # Беремо перший знайдений
                self.log(f"🎯 Detected checkpoint: {checkpoint_type} (available: {detected_checkpoints})")
                return checkpoint_type
            
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
                
                # Детектуємо поточний чекпоінт
                checkpoint_type = self.detect_appeal_checkpoint()
                
                if not checkpoint_type:
                    self.log("✅ Апеляція завершена успішно!")
                    self.appeal_status = "APPEAL_SUCCESS"
                    return {
                        'success': True,
                        'status': 'APPEAL_SUCCESS',
                        'message': 'Апеляція успішно завершена'
                    }
                
                self.log(f"🎯 Виявлено чекпоінт: {checkpoint_type}")
                
                # Обробляємо чекпоінт
                success = False
                
                if checkpoint_type == "2fa_verification":
                    success = self.handle_appeal_checkpoint(
                        checkpoint_type, 
                        secret_key=profile_info.get('2fa_secret', '')
                    )
                else:
                    success = self.handle_appeal_checkpoint(checkpoint_type)
                
                if success:
                    self.log(f"✅ Чекпоінт {checkpoint_type} оброблено успішно")
                    time.sleep(2)
                else:
                    self.log(f"❌ Не вдалося обробити чекпоінт {checkpoint_type}")
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
