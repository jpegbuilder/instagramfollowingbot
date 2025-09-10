# Instagram Automation Bot

An advanced Instagram automation system with AdsPower browser profile management and Airtable integration for organized follower targeting.

## Features

- **Multi-Profile Management**: Run multiple Instagram accounts simultaneously with AdsPower browser profiles
- **Airtable Integration**: Fetch account data and target follower lists from Airtable
- **Smart Following**: Profile-specific follower targeting with customizable delays
- **Web Dashboard**: Real-time monitoring and control of all profiles
- **Concurrent Execution**: Efficiently manage multiple profiles with configurable limits
- **Safety Features**: Built-in delays, error handling, and suspension detection

## Prerequisites

- Python 3.7+
- AdsPower browser installed and running
- Airtable account with configured base
- Chrome/Chromium browser

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/instagram-automation-bot.git
   cd instagram-automation-bot
   ```

2. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure API keys:
   ```bash
   cp api_config_template.py api_config.py
   ```
   Edit `api_config.py` with your actual API keys. See [API Setup Guide](README_API_SETUP.md) for details.

## Configuration

### Airtable Setup

Your Airtable base should have the following structure:

**Main Table Fields:**
- `Profile Number`: Unique identifier for each profile
- `Username`: Instagram username
- `AdsPower ID`: AdsPower profile user ID
- `Status`: Account status (Alive, Follow Block, etc.)
- `Assigned IG`: Link to follower list record

**Linked Table Fields:**
- `Filtered Followers`: Attachment field with .txt file containing target usernames

### Dashboard Configuration

Edit `config.json` to customize:
- Follow delays and intervals
- Maximum follows per hour
- Extended break durations
- Concurrent profile limits

## Usage

### Starting the Dashboard

```bash
python dashboard_controller.py
```

Access the dashboard at `http://localhost:8080`

### Dashboard Features

- **Start All**: Start all profiles sequentially
- **Stop All**: Stop all running profiles
- **Start Selected**: Start only checked profiles
- **Stop Selected**: Stop only checked profiles
- **Start Range**: Start profiles within a number range
- **Test Mode**: Run profiles with minimal follows for testing

### Profile Management

Each profile displays:
- Profile name and number
- Current status (Running, Stopped, Error, etc.)
- Follow statistics
- Airtable sync status

## File Structure

```
├── dashboard_controller.py    # Main dashboard server
├── instagram_bot.py          # Instagram automation logic
├── dashboard.html            # Web dashboard interface
├── config.json              # Bot configuration
├── api_config.py            # API keys (create from template)
├── api_config_template.py   # API configuration template
├── usernames.txt            # Default follower list
└── assigned_followers/      # Profile-specific follower lists
```

## Safety and Best Practices

- Use realistic delays between actions
- Respect Instagram's rate limits
- Monitor for suspension warnings
- Keep follow counts reasonable
- Use VPS isolation for profiles
- Regularly update target lists

## Troubleshooting

### Common Issues

1. **Dashboard won't connect**: Ensure AdsPower is running and API is enabled
2. **Profile won't start**: Check AdsPower profile exists with correct ID
3. **No followers found**: Verify Airtable linked records and file attachments
4. **API errors**: Confirm all API keys are correctly configured

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is for educational purposes only. Use responsibly and in accordance with Instagram's Terms of Service.

## Disclaimer

This tool is for automation of your own accounts only. The developers are not responsible for any misuse or violations of Instagram's terms of service.