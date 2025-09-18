# GitHub API Setup for Automatic File Upload

## Step 1: Create GitHub Personal Access Token

1. Go to https://github.com/settings/tokens
2. Click "Generate new token" → "Generate new token (classic)"
3. Fill out the form:

   - **Note**: "Instagram Bot File Upload"
   - **Expiration**: "No expiration" (or choose your preferred duration)
   - **Scopes**: Select only `gist` (Create gists)

4. Click "Generate token"
5. **IMPORTANT**: Copy the token and save it in a secure place

## Step 2: Add Token to Configuration

1. Open the `api_config.py` file
2. Replace `your_github_token_here` with your token:

```python
GITHUB_TOKEN = "ghp_your_actual_token_here"
```

## How It Works

1. **Bot creates a file** with followed usernames
2. **Uploads file to GitHub Gist** (public)
3. **Gets public URL** of the file
4. **Uploads URL to Airtable** "Already Followed" field
5. **Updates "Remaining Targets" field** automatically

## GitHub Gist Benefits

- ✅ Free
- ✅ Public files always accessible
- ✅ No additional services required
- ✅ Works reliably with Airtable
- ✅ Files stored permanently

## Security

- Token only provides access to create Gists
- No access to your repositories
- Can be revoked at any time
- Files are public but with obscure names

## Alternatives

If you don't want to use GitHub:

- AWS S3 (requires setup)
- Google Drive API (requires setup)
- Manual file upload
