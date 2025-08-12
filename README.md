
# Mood Bot (Webhook + Google Sheets)

## ENV variables
- BOT_TOKEN: Telegram bot token
- WEBHOOK_BASE_URL: https://<your-service>.onrender.com
- SECRET_KEY: any long secret for /trigger endpoints
- GOOGLE_SHEETS_ID: spreadsheet ID (from its URL)
- GOOGLE_CREDENTIALS: JSON of your Service Account

## Google setup (short)
1) Go to Google Cloud Console → Create Project.
2) Enable "Google Sheets API".
3) Create Service Account → Keys → "Add key" → JSON. Download.
4) Open your Google Sheet, share with service-account email (Editor).
5) Put the whole JSON content into GOOGLE_CREDENTIALS env var.
