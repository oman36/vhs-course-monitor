# Setting up Telegram notifications

## What you need:

### 1. Bot token

1. Open Telegram and find **@BotFather**
2. Send the `/newbot` command
3. Follow the instructions:
   - Choose a name for your bot (for example: "VHS Course Monitor")
   - Choose a username for your bot (it must end in `bot`, for example: `vhs_monitor_bot`)
4. BotFather will give you a token like this: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
5. **Save this token!**

### 2. Chat ID

There are several ways to get a chat ID:

#### Method 1: Direct messages to yourself
1. Find **@userinfobot** in Telegram
2. Send it any message
3. It will reply with your chat ID (a number, for example: `123456789`)

#### Method 2: Through your bot
1. Start a conversation with your bot (find it by its username)
2. Send the bot any message (for example: `/start`)
3. Open this URL in your browser: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   (replace `<YOUR_TOKEN>` with the token from BotFather)
4. Find the `"chat":{"id":123456789}` field in the response — this is your chat ID

#### Method 3: For a group chat
1. Add the bot to the group
2. Send a message to the group
3. Use the same getUpdates URL
4. Find the group's chat ID (it will be a negative number, for example: `-123456789`)

## How to use:

### Option 1: .env file (recommended) ⭐

1. Install the dependencies (if they are not already installed):
```bash
uv sync
# or
pip install python-dotenv
```

2. Create a `.env` file in the project root:
```bash
touch .env
```

3. Add the following lines to the `.env` file:
```
TELEGRAM_BOT_TOKEN=your_token_from_BotFather
TELEGRAM_CHAT_ID=your_chat_id
```

4. Run the script:
```bash
python main.py
```

The script will automatically load the variables from the `.env` file.

**Important:** The `.env` file is already listed in `.gitignore` and will not be uploaded to the repository.

### Option 2: Environment variables

```bash
export TELEGRAM_BOT_TOKEN="your_token_from_BotFather"
export TELEGRAM_CHAT_ID="your_chat_id"
python main.py
```

### Option 3: In code (not recommended for production)

You can pass the token and chat_id directly to the function:
```python
send_telegram_message("Message", bot_token="your_token", chat_id="your_chat_id")
```

## Verifying the setup

After configuring everything, run the script. If everything is set up correctly, you will see:
```
✅ Message sent to Telegram
```

If something is wrong:
```
⚠️  Telegram is not configured: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing
```
