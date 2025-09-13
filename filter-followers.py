import requests
import csv
import os
import time
import concurrent.futures
import json
import base64
from openai import OpenAI
import threading
from queue import Queue
import queue
import random
from datetime import datetime

# Airtable configuration
AIRTABLE_API_KEY = 'patmQOb53QcLrlxAb.f02275b4e5c2e2d25e99a3423b5bc93de386645cfc4fe9fe69bb797ec58893b9'
BASE_ID = 'appqPkgB4AthfZPXh'
TABLE_ID = 'tbll1xRWFKQtr9sJB'
VIEW_ID = 'viwLQZWpq5DR1u19s'

# OpenAI configuration
OPENAI_API_KEY = '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'
client = OpenAI(api_key=OPENAI_API_KEY)

# Airtable API endpoints
AIRTABLE_BASE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
AIRTABLE_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# Rate limit configuration
# Your limits: 30,000 RPM = 500 RPS, 150,000,000 TPM = 2,500,000 TPS
# We'll use 90% of limits to leave some headroom
MAX_REQUESTS_PER_SECOND = 450  # 90% of 500
MAX_TOKENS_PER_SECOND = 2250000  # 90% of 2,500,000
BATCH_SIZE = 10  # Reduced to 10 profiles per request for better throughput
MAX_CONCURRENT_REQUESTS = 450  # Match our requests per second limit

# Statistics tracking
stats = {
    'lock': threading.Lock(),
    'api_calls': 0,
    'rate_limit_hits': 0,
    'total_tokens': 0,
    'start_time': time.time()
}

# Local folder for saving files
SCRAPING_FOLDER = "/Users/victorv/Desktop/Scraping"
FILTERED_FOLDER = "/Users/victorv/Desktop/Scraping/Filtered"
NOT_PASSED_FOLDER = "/Users/victorv/Desktop/Scraping/Not Passed"
NOT_PASSED_FILE = "/Users/victorv/Desktop/Scraping/Not Passed/Not Passed Usernames.txt"
PASSED_FOLDER = "/Users/victorv/Desktop/Scraping/Passed"
PASSED_FILE = "/Users/victorv/Desktop/Scraping/Passed/Passed Usernames.txt"


def load_not_passed_usernames():
    """Load the list of usernames that didn't pass from previous runs"""
    not_passed_usernames = set()
    
    if os.path.exists(NOT_PASSED_FILE):
        try:
            with open(NOT_PASSED_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    username = line.strip()
                    if username:
                        not_passed_usernames.add(username)
            print(f"  Loaded {len(not_passed_usernames)} not-passed usernames from history")
        except Exception as e:
            print(f"  Error loading not-passed usernames: {str(e)}")
    
    return not_passed_usernames


def save_not_passed_usernames(usernames):
    """Append new not-passed usernames to the file"""
    # Create directory if it doesn't exist
    if not os.path.exists(NOT_PASSED_FOLDER):
        os.makedirs(NOT_PASSED_FOLDER)
    
    try:
        with open(NOT_PASSED_FILE, 'a', encoding='utf-8') as f:
            for username in usernames:
                f.write(f"{username}\n")
        # Don't print for individual saves to avoid spam
        if len(usernames) > 1:
            print(f"  Saved {len(usernames)} new not-passed usernames to history")
    except Exception as e:
        print(f"  Error saving not-passed usernames: {str(e)}")


def load_passed_usernames():
    """Load the list of usernames that passed from previous runs"""
    passed_usernames = set()
    
    if os.path.exists(PASSED_FILE):
        try:
            with open(PASSED_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    username = line.strip()
                    if username:
                        passed_usernames.add(username)
            print(f"  Loaded {len(passed_usernames)} passed usernames from history")
        except Exception as e:
            print(f"  Error loading passed usernames: {str(e)}")
    
    return passed_usernames


def save_passed_usernames(usernames):
    """Append new passed usernames to the file"""
    # Create directory if it doesn't exist
    if not os.path.exists(PASSED_FOLDER):
        os.makedirs(PASSED_FOLDER)
    
    try:
        with open(PASSED_FILE, 'a', encoding='utf-8') as f:
            for username in usernames:
                f.write(f"{username}\n")
        # Don't print for individual saves to avoid spam
        if len(usernames) > 1:
            print(f"  Saved {len(usernames)} new passed usernames to history")
    except Exception as e:
        print(f"  Error saving passed usernames: {str(e)}")


def get_airtable_records():
    """Fetch all records from Airtable view"""
    records = []
    offset = None
    
    while True:
        params = {
            "view": VIEW_ID,
            "pageSize": 100
        }
        
        if offset:
            params["offset"] = offset
            
        response = requests.get(AIRTABLE_BASE_URL, headers=AIRTABLE_HEADERS, params=params)
        
        if response.status_code != 200:
            print(f"Error fetching records: {response.text}")
            break
            
        data = response.json()
        records.extend(data.get("records", []))
        
        offset = data.get("offset")
        if not offset:
            break
            
    return records


def download_csv_from_airtable(attachment_url, filename):
    """Download CSV file from Airtable attachment URL"""
    try:
        # Add headers to avoid 410 errors
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/csv,application/csv,*/*',
            'Connection': 'keep-alive'
        }
        
        response = requests.get(attachment_url, headers=headers, timeout=30)
        if response.status_code == 200:
            filepath = os.path.join(SCRAPING_FOLDER, filename)
            with open(filepath, 'wb') as f:
                f.write(response.content)
            print(f"  Downloaded {filename} ({len(response.content)} bytes)")
            return filepath
        elif response.status_code == 410:
            print(f"  ERROR: {filename} URL has expired (410) - Airtable attachment link is no longer valid")
            print(f"  This usually means the CSV needs to be re-uploaded to Airtable")
            return None
        else:
            print(f"  Failed to download {filename}: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"  Error downloading {filename}: {str(e)}")
        return None


def filter_verified_users(input_filepath, user_id):
    """Remove all verified users from CSV and save filtered version"""
    # Create filtered folder if it doesn't exist
    if not os.path.exists(FILTERED_FOLDER):
        os.makedirs(FILTERED_FOLDER)
    
    output_filename = f"{user_id} Filtered Followers.csv"
    output_filepath = os.path.join(FILTERED_FOLDER, output_filename)
    
    # Read and filter the CSV
    filtered_rows = []
    headers = []
    
    try:
        with open(input_filepath, 'r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            headers = reader.fieldnames
            
            for row in reader:
                # Keep only non-verified users
                if row.get('is_verified', '').lower() != 'true':
                    filtered_rows.append(row)
        
        # Write filtered data
        with open(output_filepath, 'w', newline='', encoding='utf-8') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=headers)
            writer.writeheader()
            writer.writerows(filtered_rows)
        
        return output_filepath, len(filtered_rows)
        
    except Exception as e:
        print(f"  Error filtering CSV: {str(e)}")
        return None, 0




def _download_image_as_base64(image_url):
    """Download image and convert to base64 - exact same as instagram_bot.py"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.instagram.com/'
        }

        response = requests.get(image_url, headers=headers, timeout=0.5)  # Ultra fast timeout

        if response.status_code == 200:
            # Convert to base64
            image_base64 = base64.b64encode(response.content).decode('utf-8')
            return image_base64
        else:
            return None

    except Exception as e:
        return None


def call_openai_with_retry(messages, max_tokens, retry_count=0, max_retries=5):
    """Call OpenAI API with exponential backoff retry logic"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens
        )
        
        with stats['lock']:
            stats['api_calls'] += 1
            # Estimate tokens (rough approximation)
            stats['total_tokens'] += max_tokens
        
        return response
        
    except Exception as e:
        error_str = str(e)
        
        # Check if it's a rate limit error
        if "rate_limit" in error_str.lower() or "429" in error_str:
            with stats['lock']:
                stats['rate_limit_hits'] += 1
            
            if retry_count < max_retries:
                # Exponential backoff: 2^retry_count seconds
                wait_time = (2 ** retry_count) + random.uniform(0, 1)
                print(f"Rate limit hit, waiting {wait_time:.1f}s before retry {retry_count + 1}/{max_retries}")
                time.sleep(wait_time)
                return call_openai_with_retry(messages, max_tokens, retry_count + 1, max_retries)
            else:
                print(f"Max retries reached for OpenAI API call")
                raise
        else:
            # Not a rate limit error, raise immediately
            raise


def _analyze_batch_with_openai(profiles):
    """Analyze a batch of profiles with OpenAI"""
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=OPENAI_API_KEY)

        # Build the batch prompt
        prompt = "Please analyze these Instagram profiles and determine if each is most likely to be male.\n\n"

        for idx, profile in enumerate(profiles):
            prompt += f"Profile {idx + 1}:\n"
            prompt += f"Username: {profile['username']}\n"
            prompt += f"Name: {profile['display_name']}\n\n"

        prompt += "\nFor each profile, respond with ONLY 'Passed' if most likely male, or 'Not Passed' if not most likely male.\n"
        prompt += "Format your response as:\n"
        prompt += "Profile 1: [Passed/Not Passed]\n"
        prompt += "Profile 2: [Passed/Not Passed]\n"
        prompt += "etc."

        # Build content with images
        content = [{"type": "text", "text": prompt}]

        # Add images using base64
        for profile in profiles:
            if 'image_base64' in profile and profile['image_base64']:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{profile['image_base64']}",
                        "detail": "low"
                    }
                })

        # Create messages for chat completion
        messages = [
            {
                "role": "system",
                "content": "You are an expert at analyzing Instagram profiles to determine gender. Respond concisely with only 'Passed' or 'Not Passed' for each profile."
            },
            {
                "role": "user",
                "content": content
            }
        ]

        # Make API call with retry logic
        max_tokens = min(150 * len(profiles), 16384)
        response = call_openai_with_retry(messages, max_tokens)

        response_text = response.choices[0].message.content.strip()

        # Log the batch response
        print(f"    Batch OpenAI response: {response_text[:200]}...")

        # Parse response
        results = {}
        lines = response_text.strip().split('\n')
        for idx, profile in enumerate(profiles):
            # Look for "Profile X: Passed/Not Passed" pattern
            for line in lines:
                if f"Profile {idx + 1}:" in line:
                    if "Passed" in line and "Not Passed" not in line:
                        results[profile['username']] = "Passed"
                    else:
                        results[profile['username']] = "Not Passed"
                    break

        return results

    except Exception as e:
        print(f"    Error in batch OpenAI analysis: {str(e)}")
        return {}


def _download_images_parallel(profiles, max_workers=200):
    """Download images in parallel for multiple profiles"""
    import concurrent.futures
    import threading
    
    results = {}
    lock = threading.Lock()
    
    def download_for_profile(profile):
        username = profile['username']
        url = profile.get('profile_pic_url', '')
        
        if not url or url == 'N/A':
            return username, None
            
        image_base64 = _download_image_as_base64(url)
        return username, image_base64
    
    # Download images in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all download tasks
        future_to_profile = {executor.submit(download_for_profile, p): p for p in profiles}
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_profile):
            try:
                username, image_base64 = future.result()
                with lock:
                    results[username] = image_base64
            except Exception as e:
                profile = future_to_profile[future]
                print(f"    Error downloading for {profile['username']}: {str(e)[:50]}")
                with lock:
                    results[profile['username']] = None
    
    return results


def continuous_image_downloader(followers, download_queue, stats):
    """Continuously download images and add to queue"""
    def download_worker(follower_index):
        follower = followers[follower_index]
        username = follower.get('username', '')
        display_name = follower.get('full_name', username)
        profile_pic_url = follower.get('profile_pic_url', '')
        
        profile_data = {
            'index': follower_index,
            'username': username,
            'display_name': display_name,
            'profile_pic_url': profile_pic_url,
            'image_base64': None
        }
        
        if profile_pic_url and profile_pic_url != 'N/A':
            image_base64 = _download_image_as_base64(profile_pic_url)
            if image_base64:
                profile_data['image_base64'] = image_base64
                with stats['lock']:
                    stats['images_downloaded'] += 1
        
        download_queue.put(profile_data)
        
        with stats['lock']:
            stats['profiles_processed'] += 1
            if stats['profiles_processed'] % 100 == 0:
                elapsed = time.time() - stats['start_time']
                rate = stats['images_downloaded'] / elapsed if elapsed > 0 else 0
                print(f"    Download progress: {stats['profiles_processed']}/{len(followers)} profiles, "
                      f"{stats['images_downloaded']} images ({rate:.1f} img/s)")
    
    # Start downloading with maximum workers for ultra speed
    with concurrent.futures.ThreadPoolExecutor(max_workers=200) as executor:
        futures = [executor.submit(download_worker, i) for i in range(len(followers))]
        concurrent.futures.wait(futures)
    
    # Signal completion
    download_queue.put(None)


def process_openai_batch_async(batch, batch_num):
    """Process a single batch with OpenAI asynchronously"""
    print(f"\n  Processing batch {batch_num} ({len(batch)} profiles)...")
    batch_results = _analyze_batch_with_openai(batch)
    
    passed = sum(1 for r in batch_results.values() if r == "Passed")
    print(f"    Batch {batch_num}: {passed} passed, {len(batch_results) - passed} not passed")
    
    # Show not passed accounts
    for profile in batch:
        username = profile['username']
        if username in batch_results and batch_results[username] == "Not Passed":
            print(f"      NOT PASSED: @{username} ({profile['display_name']})")
    
    return batch_results


def openai_batch_processor(download_queue, results_dict, stats):
    """Process batches of profiles with OpenAI using PARALLEL requests for ultra speed"""
    batch = []
    batch_num = 0
    
    # Use ThreadPoolExecutor for parallel OpenAI requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as openai_executor:
        active_futures = {}
        
        while True:
            try:
                # Collect profiles for a batch
                profile = download_queue.get(timeout=2)
                
                if profile is None:  # End signal
                    # Process any remaining batch
                    if batch:
                        batch_num += 1
                        future = openai_executor.submit(process_openai_batch_async, batch, batch_num)
                        active_futures[future] = (batch_num, batch)
                    break
                
                batch.append(profile)
                
                # Process batch when it reaches BATCH_SIZE profiles for ultra-fast parallel processing
                if len(batch) >= BATCH_SIZE:
                    batch_num += 1
                    current_batch = batch[:BATCH_SIZE]
                    batch = batch[BATCH_SIZE:]
                    
                    # Submit batch for parallel processing
                    future = openai_executor.submit(process_openai_batch_async, current_batch, batch_num)
                    active_futures[future] = (batch_num, current_batch)
                
            except queue.Empty:
                # Check if we should process partial batch
                if batch and len(batch) >= 5:
                    batch_num += 1
                    future = openai_executor.submit(process_openai_batch_async, batch, batch_num)
                    active_futures[future] = (batch_num, batch)
                    batch = []
        
        # Wait for all OpenAI requests to complete and collect results
        print(f"\n  Waiting for {len(active_futures)} parallel OpenAI batches to complete...")
        for future in concurrent.futures.as_completed(active_futures):
            try:
                batch_results = future.result()
                batch_num, batch_profiles = active_futures[future]
                
                # Process each profile result individually and save to file immediately
                for profile in batch_profiles:
                    username = profile['username']
                    if username in batch_results:
                        result = batch_results[username]
                        results_dict[username] = result
                        
                        # Save to file immediately based on result
                        if result == "Passed":
                            save_passed_usernames([username])
                        else:
                            save_not_passed_usernames([username])
                            
            except Exception as e:
                batch_num, _ = active_futures[future]
                print(f"    Error in batch {batch_num}: {str(e)}")


def filter_with_openai(input_filepath, user_id):
    """Use OpenAI GPT-4o to filter followers with continuous downloading"""
    try:
        # Load previously processed usernames
        not_passed_history = load_not_passed_usernames()
        passed_history = load_passed_usernames()
        
        # Read the CSV
        all_followers = []
        with open(input_filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            all_followers = list(reader)
        
        # Filter out usernames that have already been processed
        followers = []
        already_not_passed = 0
        already_passed = 0
        for follower in all_followers:
            username = follower.get('username', '')
            if username in not_passed_history:
                already_not_passed += 1
            elif username in passed_history:
                already_passed += 1
            else:
                followers.append(follower)
        
        print(f"\n  === OpenAI GPT-4o Gender Filtering for User {user_id} ===")
        print(f"  Total followers in CSV: {len(all_followers)}")
        print(f"  Already marked as not passed: {already_not_passed}")
        print(f"  Already marked as passed: {already_passed}")
        print(f"  New followers to analyze: {len(followers)}")
        
        if not followers:
            print(f"  All followers already processed. No new followers to analyze.")
            # Don't create any files or upload anything if there are no new followers
            return None, None, 0
        
        print(f"  Using continuous download + parallel OpenAI processing")
        
        # Shared data structures for ULTRA SPEED
        download_queue = Queue(maxsize=1000)  # Massive buffer for ultra speed
        results_dict = {}
        download_stats = {
            'lock': threading.Lock(),
            'images_downloaded': 0,
            'profiles_processed': 0,
            'start_time': time.time()
        }
        
        # Start continuous image downloading in background
        download_thread = threading.Thread(
            target=continuous_image_downloader,
            args=(followers, download_queue, download_stats)
        )
        download_thread.start()
        
        # Start OpenAI processing in main thread
        openai_batch_processor(download_queue, results_dict, download_stats)
        
        # Wait for download thread to complete
        download_thread.join()
        
        # Final statistics
        elapsed = time.time() - download_stats['start_time']
        final_rate = download_stats['images_downloaded'] / elapsed if elapsed > 0 else 0
        
        print(f"\n  === DOWNLOAD COMPLETE ===")
        print(f"  Total time: {elapsed:.1f}s")
        print(f"  Images downloaded: {download_stats['images_downloaded']}/{len(followers)}")
        print(f"  Average download rate: {final_rate:.1f} img/s")
        
        # Process results (usernames already saved individually during batch processing)
        all_passed_usernames = []
        new_not_passed_usernames = []
        
        for username, result in results_dict.items():
            if result == "Passed":
                all_passed_usernames.append(username)
            else:
                new_not_passed_usernames.append(username)
        
        # No need to save here as they were already saved individually
        print(f"  All usernames have been saved incrementally during processing")
        
        print(f"\n  === FINAL RESULTS ===")
        print(f"  Total analyzed: {len(results_dict)}")
        print(f"  Passed (male): {len(all_passed_usernames)}")
        print(f"  Not Passed: {len(new_not_passed_usernames)}")
        if len(results_dict) > 0:
            print(f"  Male percentage: {(len(all_passed_usernames) / len(results_dict) * 100):.1f}%")
        print(f"  Note: All usernames were saved incrementally as each profile was processed")
        
        # API usage statistics
        global stats  # Use the global stats dictionary
        if 'api_calls' in stats:
            api_elapsed = time.time() - stats['start_time']
            print(f"\n  === API USAGE STATS ===")
            print(f"  Total API calls: {stats['api_calls']}")
            print(f"  Rate limit hits: {stats['rate_limit_hits']}")
            print(f"  Estimated total tokens: {stats['total_tokens']:,}")
            if api_elapsed > 0:
                print(f"  Average API calls/second: {stats['api_calls'] / api_elapsed:.1f}")
                print(f"  Average tokens/second: {stats['total_tokens'] / api_elapsed:.1f}")
        
        # Only include NEW followers that were just analyzed and passed
        # Do NOT include any followers from history
        account_passed_usernames = set(all_passed_usernames)  # Only newly analyzed
        
        # Filter the original data based on NEW passed usernames only
        filtered_followers = [f for f in all_followers if f.get('username') in account_passed_usernames]
        
        # Save newly analyzed passed usernames to history (append mode)
        if all_passed_usernames:
            save_passed_usernames(all_passed_usernames)
        
        # Save AI-filtered CSV
        ai_filtered_filename = f"{user_id} AI Filtered Followers.csv"
        ai_filtered_filepath = os.path.join(FILTERED_FOLDER, ai_filtered_filename)
        
        with open(ai_filtered_filepath, 'w', newline='', encoding='utf-8') as f:
            if filtered_followers:
                writer = csv.DictWriter(f, fieldnames=followers[0].keys())
                writer.writeheader()
                writer.writerows(filtered_followers)
        
        print(f"\n  Saved filtered CSV: {ai_filtered_filepath}")
        
        # Create text file with just usernames
        usernames_filename = f"{user_id} Usernames.txt"
        usernames_filepath = os.path.join(FILTERED_FOLDER, usernames_filename)
        
        with open(usernames_filepath, 'w', encoding='utf-8') as f:
            for follower in filtered_followers:
                f.write(follower.get('username', '') + '\n')
        
        print(f"  Total NEW passed usernames for this account: {len(account_passed_usernames)}")
        
        print(f"  Saved usernames text file: {usernames_filepath}")
        
        # Debug output
        print(f"\n  DEBUG: Returning from filter_with_openai:")
        print(f"    ai_filtered_filepath: {ai_filtered_filepath}")
        print(f"    usernames_filepath: {usernames_filepath}")
        print(f"    filtered_count: {len(filtered_followers)}")
        
        return ai_filtered_filepath, usernames_filepath, len(filtered_followers)
        
    except Exception as e:
        print(f"\n  CRITICAL ERROR in OpenAI filtering: {str(e)}")
        import traceback
        print(f"  Traceback: {traceback.format_exc()}")
        return None, None, 0


def upload_file_to_airtable(record_id, file_path, field_name="Filtered Followers"):
    """Upload file to Airtable attachment field"""
    try:
        # Determine content type
        content_type = 'text/plain' if file_path.endswith('.txt') else 'text/csv'
        
        # First, upload to tmpfiles.org
        with open(file_path, 'rb') as f:
            response = requests.post(
                'https://tmpfiles.org/api/v1/upload',
                files={'file': (os.path.basename(file_path), f, content_type)}
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'success':
                    # Convert the URL to direct download link
                    file_url = result['data']['url'].replace('tmpfiles.org/', 'tmpfiles.org/dl/')
                    
                    # Update Airtable record with the attachment
                    update_url = f"{AIRTABLE_BASE_URL}/{record_id}"
                    update_data = {
                        "fields": {
                            field_name: [{
                                "url": file_url,
                                "filename": os.path.basename(file_path)
                            }]
                        }
                    }
                    
                    response = requests.patch(update_url, headers=AIRTABLE_HEADERS, json=update_data)
                    
                    if response.status_code == 200:
                        print(f"  Successfully uploaded {os.path.basename(file_path)} to Airtable")
                        return True
                    else:
                        print(f"  Airtable update failed: {response.status_code} - {response.text}")
                        print(f"  Update URL: {update_url}")
                        print(f"  Update data: {json.dumps(update_data, indent=2)}")
                else:
                    print(f"  tmpfiles upload failed: {result}")
            else:
                print(f"  tmpfiles upload failed: {response.status_code} - {response.text}")
                
    except Exception as e:
        print(f"  Upload error: {str(e)}")
    
    return False


def process_record(record_data):
    """Process a single record"""
    record_id, fields, index, total = record_data
    user_id = fields.get('ID')
    attachments = fields.get('All Followers', [])
    
    print(f"\n{'='*60}")
    print(f"DEBUG: Processing record {index}/{total}")
    print(f"  Record ID: {record_id}")
    print(f"  User ID: {user_id}")
    print(f"  Fields available: {list(fields.keys())}")
    
    if not user_id:
        print(f"Record {index}/{total}: No ID found, skipping...")
        return
    
    if not attachments:
        print(f"Record {index}/{total}: User {user_id} - No attachments found")
        return
    
    print(f"Processing record {index}/{total}: User ID {user_id}")
    
    try:
        # Get the first attachment (assuming one CSV per record)
        attachment = attachments[0]
        attachment_url = attachment.get('url')
        filename = attachment.get('filename', f"{user_id} Followers.csv")
        
        # Download the CSV
        downloaded_path = download_csv_from_airtable(attachment_url, filename)
        
        if downloaded_path:
            # Step 1: Filter out verified users
            filtered_path, filtered_count = filter_verified_users(downloaded_path, user_id)
            
            if filtered_path and filtered_count > 0:
                print(f"  User {user_id}: Filtered to {filtered_count} non-verified followers")
                
                # Step 2: Apply OpenAI filtering
                # For testing, let's use vision API for first few followers
                use_vision = False  # Set to True to use vision API (slower but analyzes images)
                
                print(f"  User {user_id}: Applying GPT-4o gender filtering (keeping males only)...")
                ai_filtered_path, usernames_path, ai_filtered_count = filter_with_openai(filtered_path, user_id)
                
                print(f"\n  DEBUG: filter_with_openai returned:")
                print(f"    usernames_path: {usernames_path}")
                print(f"    ai_filtered_count: {ai_filtered_count}")
                
                if usernames_path and ai_filtered_count > 0:
                    print(f"  User {user_id}: {ai_filtered_count} followers passed all filters")
                    print(f"  Usernames file path: {usernames_path}")
                    print(f"  File exists: {os.path.exists(usernames_path)}")
                    
                    # Step 3: Upload the text file with usernames to Airtable
                    print(f"  Uploading to Airtable record: {record_id}")
                    success = upload_file_to_airtable(record_id, usernames_path, "Filtered Followers")
                    
                    if success:
                        print(f"  User {user_id}: Upload successful!")
                    else:
                        print(f"  User {user_id}: Upload failed")
                else:
                    print(f"  User {user_id}: No followers passed OpenAI filtering")
            else:
                print(f"  User {user_id}: No non-verified followers found")
        else:
            print(f"  User {user_id}: Download failed")
            
    except Exception as e:
        print(f"  Error processing user {user_id}: {str(e)}")


def main():
    """Main function to process all Airtable records"""
    print("Starting Verified Followers Filter...")
    print("This will remove all verified users from follower CSVs\n")
    
    # Create folders if they don't exist
    for folder in [SCRAPING_FOLDER, FILTERED_FOLDER, NOT_PASSED_FOLDER, PASSED_FOLDER]:
        if not os.path.exists(folder):
            os.makedirs(folder)
    
    # Process records in batches of 100 to prevent attachment URL expiration
    FETCH_BATCH_SIZE = 100
    total_processed = 0
    batch_number = 1
    
    while True:
        print(f"\n{'='*60}")
        print(f"Fetching batch {batch_number} (up to {FETCH_BATCH_SIZE} records)...")
        
        # Get up to 100 records from Airtable
        # IMPORTANT: The Airtable view should be configured to only show records 
        # where "Filtered Followers" field is empty. This way, once we process 
        # and upload results, those records disappear from the view automatically.
        # This prevents re-processing the same records.
        records = get_airtable_records()
        
        # Limit to first 100 records to prevent URL expiration
        records = records[:FETCH_BATCH_SIZE]
        
        if not records:
            print("No more records to process!")
            break
            
        print(f"Found {len(records)} records in this batch\n")
        
        # Prepare data for processing
        record_data = []
        for i, record in enumerate(records, 1):
            record_id = record.get('id')
            fields = record.get('fields', {})
            record_data.append((record_id, fields, i, len(records)))
        
        # Process records in this batch
        print(f"Processing batch {batch_number} records...")
        for data in record_data:
            process_record(data)
            total_processed += 1
        
        print(f"\nBatch {batch_number} complete! Total processed so far: {total_processed}")
        batch_number += 1
        
        # Small delay between batches to ensure Airtable updates are reflected
        if len(records) == FETCH_BATCH_SIZE:
            print("Waiting 2 seconds before fetching next batch...")
            import time
            time.sleep(2)
    
    print(f"\n{'='*60}")
    print(f"ALL PROCESSING COMPLETE!")
    print(f"Total records processed: {total_processed}")
    print(f"Original CSVs saved in: {SCRAPING_FOLDER}")
    print(f"Filtered CSVs saved in: {FILTERED_FOLDER}")


if __name__ == "__main__":
    main()