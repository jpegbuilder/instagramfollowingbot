# API Configuration Setup

This project requires API keys for Airtable and AdsPower. Follow these steps to configure them:

## Setup Instructions

1. **Copy the template file:**
   ```bash
   cp api_config_template.py api_config.py
   ```

2. **Edit `api_config.py`** and replace the placeholder values with your actual API keys:

   ### Airtable Configuration:
   - `AIRTABLE_PERSONAL_ACCESS_TOKEN`: Your Airtable personal access token
   - `AIRTABLE_BASE_ID`: Your Airtable base ID (starts with "app")
   - `AIRTABLE_TABLE_NAME`: Your Airtable table name
   - `AIRTABLE_VIEW_ID`: Your Airtable view ID (starts with "viw")
   - `AIRTABLE_LINKED_TABLE_ID`: ID of the linked table for follower data

   ### AdsPower Configuration:
   - `ADSPOWER_API_URL`: AdsPower API URL (default: `http://local.adspower.net:50325`)
   - `ADSPOWER_API_KEY`: Your AdsPower API key

## Important Security Notes

- **NEVER commit `api_config.py` to version control!** It contains sensitive API keys.
- The `.gitignore` file is configured to exclude `api_config.py` automatically.
- Only share `api_config_template.py` when sharing code with others.

## Getting API Keys

### Airtable
1. Go to https://airtable.com/create/tokens
2. Create a new personal access token
3. Grant necessary permissions for your base

### AdsPower
1. Open AdsPower application
2. Go to Settings > API
3. Enable API and copy your API key

## Troubleshooting

If you see the error:
```
api_config.py not found! Please copy api_config_template.py to api_config.py and fill in your API keys.
```

This means you need to create the `api_config.py` file following the instructions above.