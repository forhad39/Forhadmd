# Telegram Bot Admin Command System

This bot includes a comprehensive admin command system that allows administrators to manage users, send broadcasts, and control bot functionality through slash commands. **All admin functions are now command-based with no interactive buttons.**

## 🚀 Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the bot:
```bash
python bot.py
```

## 🔧 Admin Commands

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/demon` | Show all admin commands | `/demon` |
| `/help` | Show all available commands | `/help` |
| `/broadcast` | Send message to all users | `/broadcast Hello everyone!` |
| `/stats` | Show bot statistics | `/stats` |

### User Management

| Command | Description | Example |
|---------|-------------|---------|
| `/addvipuser` | Add VIP user with time limit | `/addvipuser 123456789 30` |
| `/removevipuser` | Remove VIP status from user | `/removevipuser 123456789` |
| `/banuser` | Ban user from bot | `/banuser 123456789` |
| `/unbanuser` | Unban user | `/unbanuser 123456789` |
| `/setpoints` | Set user points | `/setpoints 123456789 100` |

### Admin Management

| Command | Description | Example |
|---------|-------------|---------|
| `/addadmin` | Add new admin | `/addadmin 123456789` |
| `/removeadmin` | Remove admin status | `/removeadmin 123456789` |

### Data & Information

| Command | Description | Example |
|---------|-------------|---------|
| `/download` | Download data files | `/download users` |
| `/settings` | Show current settings | `/settings` |
| `/backup` | Create data backup | `/backup` |

### Channel Management

| Command | Description | Example |
|---------|-------------|---------|
| `/channels` | Show current channels | `/channels` |
| `/channels add` | Add new channel | `/channels add MyChannel https://t.me/mychannel -1001234567890` |
| `/channels remove` | Remove channel | `/channels remove -1001234567890` |

### Settings

| Command | Description | Example |
|---------|-------------|---------|
| `/toggle` | Toggle settings | `/toggle referral` |

### Subscription Management

| Command | Description | Example |
|---------|-------------|---------|
| `/subscription` | Show subscription settings | `/subscription` |
| `/setcaption` | Set subscription caption | `/setcaption 🌟 Premium VIP Subscription` |
| `/setprice` | Set subscription price | `/setprice 1_month 15` |

## 📋 Command Examples

### Broadcasting
```
/broadcast Welcome to our bot! New features are available.
/broadcast Maintenance scheduled for tomorrow at 2 PM.
```

### VIP Management
```
/addvipuser 123456789 30    # Add VIP for 30 days
/addvipuser 987654321 7     # Add VIP for 7 days
/removevipuser 123456789    # Remove VIP status
```

### User Management
```
/banuser 123456789          # Ban user
/unbanuser 123456789        # Unban user
/setpoints 123456789 500    # Set user points to 500
```

### Admin Management
```
/addadmin 123456789         # Add new admin
/removeadmin 123456789      # Remove admin status
```

### Data Downloads
```
/download users             # Download all users data
/download vip               # Download VIP users data
/download admins            # Download admins data
/download predictions       # Download predictions data
/download channels          # Download channels data
```

### Channel Management
```
/channels add MyChannel https://t.me/DK_WlN_official -1003383470525
/channels remove -1002863703421
/channels list
```

### Settings
```
/settings                   # Show current settings
/toggle referral            # Toggle referral system (notifies all users)
/backup                     # Create data backup
```

### Subscription Management
```
/subscription               # Show subscription settings
/subscription preview       # Preview subscription menu
/setcaption 🌟 Premium VIP Subscription
/setprice 1_month 15       # Set 1 month price to $15
/setprice 3_months 40      # Set 3 months price to $40
/setprice 6_months 70      # Set 6 months price to $70
/setprice 1_year 120       # Set 1 year price to $120
```

## 🔐 Admin Access

Only users listed in the admin configuration can use these commands. Admin users are defined in `config.py`:

```python
DEFAULT_ADMIN_ID = 6678848886
```

## 📊 Statistics

The `/stats` command shows:
- Total users
- VIP users
- Banned users
- Admin users
- Total predictions
- Total channels
- Active users

## 🛡️ Security Features

- All admin commands check for admin privileges
- Input validation for user IDs and parameters
- Error handling for invalid inputs
- Logging of admin actions
- **No interactive buttons - all functions are command-based**
- **Session management: Any command cancels active conversations**

## 🔄 Session Management

The bot now includes improved session management:
- **Any command cancels active conversations** (login, prediction, etc.)
- Users can type `/start` or any command to cancel ongoing sessions
- Prevents stuck login sessions and button issues
- Clear feedback when sessions are cancelled

## 📢 Referral System Notifications

When you toggle the referral system:
- **All users are automatically notified** of the change
- Shows whether referral system is enabled or disabled
- Provides clear instructions to users
- Reports success/failure counts

## 💎 Subscription Management System

New subscription management features:
- **Custom captions**: Set any text for subscription menu
- **Flexible pricing**: Set prices for 1 month, 3 months, 6 months, 1 year
- **Preview system**: See how subscription menu looks
- **Real-time updates**: Changes apply immediately

## 📁 File Structure

```
bOTS/
├── bot.py              # Main bot file with admin commands
├── config.py           # Configuration and database functions
├── requirements.txt    # Python dependencies
├── test_bot.py        # Test script
├── README.md          # This documentation
└── data/              # Database files
    ├── users.json     # User data
    ├── admins.json    # Admin list
    ├── config.json    # Bot configuration
    ├── predictions.json # Prediction data
    └── channels.json  # Channel data
```

## 🚀 Running the Bot

1. Make sure you have Python 3.7+ installed
2. Install dependencies: `pip install -r requirements.txt`
3. Run the bot: `python bot.py`
4. Test admin commands in Telegram

## 🔧 Configuration

Edit `config.py` to modify:
- Bot token
- Default admin ID
- Admin usernames
- Group settings
- Referral system settings
- Subscription settings

## 📝 Notes

- All admin commands require admin privileges
- User IDs must be numeric
- VIP time is specified in days
- Points can be any positive integer
- Broadcast messages are sent to all registered users
- Statistics are real-time from the database
- **All admin functions are command-based (no buttons)**
- Download commands create JSON files with timestamps
- Backup creates timestamped directories with all data files
- **Referral toggle notifies all users automatically**
- **Any command cancels active conversations**
- **Subscription management with custom captions and prices**

## 🆘 Support

For issues or questions about the admin command system, check the bot logs or contact the bot administrator.
