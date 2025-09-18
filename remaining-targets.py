#!/usr/bin/env python3
"""
Airtable Line Counter - High-Performance Version
Counts lines in text file attachments and calculates remaining targets
Uses concurrent processing for optimal speed
"""

import requests
import time
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from collections import deque

# Configuration
AIRTABLE_API_KEY = 'pataQFQzW4Gvg7MUa.a0c0ab4e0fa49acb1973cf2c85fe3a2a1f28e366b2a441db3a83f8dbc990c18c'
BASE_ID = 'appW95koLyV0VlqbN'
TABLE_ID = 'tblyldHSbHw2QiOlT'
VIEW_ID = 'viwYaFMlW2jqqDnLK'

# Field names
TARGETS_FIELD = 'Targets'
ALREADY_FOLLOWED_FIELD = 'Already Followed'
REMAINING_TARGETS_FIELD = 'Remaining Targets'

# API Configuration
AIRTABLE_BASE_URL = f'https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}'
HEADERS = {
    'Authorization': f'Bearer {AIRTABLE_API_KEY}',
    'Content-Type': 'application/json'
}

# Performance configuration
FETCH_PAGE_SIZE = 100  # Max allowed by Airtable
UPDATE_BATCH_SIZE = 10  # Airtable's limit per request
MAX_CONCURRENT_DOWNLOADS = 30  # Concurrent file downloads
MAX_CONCURRENT_PROCESSES = 50  # Concurrent record processing

# Progress tracking
progress_lock = threading.Lock()
progress_data = {
    'fetched': 0,
    'processed': 0,
    'updated': 0,
    'failed': 0,
    'start_time': None
}


def update_progress(field: str, increment: int = 1):
    """Thread-safe progress update"""
    with progress_lock:
        progress_data[field] += increment


def print_progress():
    """Print current progress"""
    with progress_lock:
        fetched = progress_data['fetched']
        processed = progress_data['processed']
        updated = progress_data['updated']
        failed = progress_data['failed']
        
        if progress_data['start_time']:
            elapsed = time.time() - progress_data['start_time']
            if elapsed > 0:
                rate = processed / elapsed
                print(f"\r⚡ Fetched: {fetched} | Processed: {processed} | "
                      f"Updated: {updated} | Failed: {failed} | "
                      f"Speed: {rate:.1f} rec/s", end='', flush=True)


def count_lines_in_file(url: str) -> int:
    """Download and count non-empty lines in a text file"""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        text = response.text
        lines = [line for line in text.split('\n') if line.strip()]
        return len(lines)
    except Exception:
        return 0


def download_files_concurrent(record: Dict) -> tuple[int, int]:
    """Download all files for a record concurrently
    Returns (targets_count, already_followed_count)
    Counts lines from ALL attachments in each field
    """
    fields = record.get('fields', {})
    targets_attachments = fields.get(TARGETS_FIELD, [])
    already_followed_attachments = fields.get(ALREADY_FOLLOWED_FIELD, [])
    
    targets_count = 0
    already_followed_count = 0  # Default to 0 if no file
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        
        # Submit all target files for download
        for attachment in targets_attachments:
            url = attachment['url']
            future = executor.submit(count_lines_in_file, url)
            futures[future] = ('targets', attachment['filename'])
        
        # Submit all already followed files for download
        for attachment in already_followed_attachments:
            url = attachment['url']
            future = executor.submit(count_lines_in_file, url)
            futures[future] = ('already_followed', attachment['filename'])
        
        # Collect results from all files
        for future in as_completed(futures):
            file_type, filename = futures[future]
            count = future.result()
            
            if file_type == 'targets':
                targets_count += count
            else:
                already_followed_count += count
    
    return targets_count, already_followed_count


def process_record(record: Dict) -> Optional[Dict]:
    """Process a single record"""
    record_id = record['id']
    fields = record.get('fields', {})
    
    try:
        # Check if we should skip this record (no targets file)
        targets_attachments = fields.get(TARGETS_FIELD, [])
        if not targets_attachments:
            update_progress('processed')
            print_progress()
            return None  # Skip records without target files
        
        # Download files concurrently
        targets_count, already_followed_count = download_files_concurrent(record)
        
        # Calculate remaining targets
        remaining_targets = targets_count - already_followed_count
        
        update_progress('processed')
        print_progress()
        
        return {
            'id': record_id,
            'fields': {
                REMAINING_TARGETS_FIELD: remaining_targets
            }
        }
    
    except Exception as e:
        update_progress('failed')
        print(f"\n❌ Error processing {record_id}: {str(e)}")
        return None


def fetch_all_records() -> List[Dict]:
    """Fetch all records from Airtable"""
    print("📊 Fetching records from Airtable...")
    
    all_records = []
    offset = None
    
    while True:
        params = {
            'view': VIEW_ID,
            'fields[]': [TARGETS_FIELD, ALREADY_FOLLOWED_FIELD, REMAINING_TARGETS_FIELD],
            'pageSize': FETCH_PAGE_SIZE
        }
        
        if offset:
            params['offset'] = offset
        
        try:
            response = requests.get(AIRTABLE_BASE_URL, headers=HEADERS, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            records = data.get('records', [])
            
            if records:
                all_records.extend(records)
                update_progress('fetched', len(records))
                print_progress()
            
            offset = data.get('offset')
            if not offset:
                break
                
        except Exception as e:
            print(f"\n❌ Error fetching records: {str(e)}")
            break
    
    print(f"\n📋 Total records fetched: {len(all_records)}")
    return all_records


def update_records_batch(updates: List[Dict]) -> int:
    """Update a batch of records in Airtable"""
    if not updates:
        return 0
    
    try:
        payload = {'records': updates}
        response = requests.patch(AIRTABLE_BASE_URL, headers=HEADERS, json=payload, timeout=30)
        response.raise_for_status()
        
        count = len(updates)
        update_progress('updated', count)
        return count
        
    except Exception as e:
        print(f"\n❌ Batch update failed: {str(e)}")
        return 0


def process_and_update_streaming(records: List[Dict]):
    """Process records and update in a streaming fashion"""
    update_queue = deque()
    update_lock = threading.Lock()
    
    def update_worker():
        """Worker thread that continuously updates records"""
        while True:
            batch = []
            
            # Collect a batch
            with update_lock:
                while update_queue and len(batch) < UPDATE_BATCH_SIZE:
                    batch.append(update_queue.popleft())
            
            if batch:
                update_records_batch(batch)
                print_progress()
            else:
                # Check if we should exit
                if progress_data['processed'] >= len(records):
                    break
                time.sleep(0.1)
    
    # Start update worker thread
    update_thread = threading.Thread(target=update_worker)
    update_thread.start()
    
    # Process records concurrently
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PROCESSES) as executor:
        # Submit all records for processing
        future_to_record = {executor.submit(process_record, record): record for record in records}
        
        # Collect results as they complete
        for future in as_completed(future_to_record):
            result = future.result()
            if result:
                with update_lock:
                    update_queue.append(result)
    
    # Wait for all updates to complete
    update_thread.join()


def main():
    """Main function"""
    print("🚀 AIRTABLE LINE COUNTER - HIGH PERFORMANCE VERSION")
    print("=" * 50)
    print(f"Base ID: {BASE_ID}")
    print(f"Table ID: {TABLE_ID}")
    print(f"View ID: {VIEW_ID}")
    print(f"Concurrent downloads: {MAX_CONCURRENT_DOWNLOADS}")
    print(f"Concurrent processing: {MAX_CONCURRENT_PROCESSES}")
    print("=" * 50)
    
    # Initialize progress tracking
    progress_data['start_time'] = time.time()
    
    # Fetch all records
    records = fetch_all_records()
    
    if not records:
        print("❌ No records found!")
        return
    
    print(f"\n🔄 Processing {len(records)} records with streaming updates...")
    
    # Process and update in streaming fashion
    process_and_update_streaming(records)
    
    # Calculate total time
    total_time = time.time() - progress_data['start_time']
    
    # Final summary
    print(f"\n\n{'=' * 20} FINAL SUMMARY {'=' * 20}")
    print(f"🎉 Processing completed in {total_time:.1f} seconds!")
    print(f"📊 Total records: {len(records)}")
    print(f"🔍 Records processed: {progress_data['processed']}")
    print(f"✅ Records updated: {progress_data['updated']}")
    print(f"❌ Records failed: {progress_data['failed']}")
    
    if total_time > 0:
        print(f"⚡ Average speed: {progress_data['processed']/total_time:.1f} records/second")
    
    success_rate = (progress_data['updated'] / len(records) * 100) if len(records) > 0 else 0
    print(f"📈 Success rate: {success_rate:.1f}%")


if __name__ == "__main__":
    main()