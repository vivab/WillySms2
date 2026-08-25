import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SUPERADMIN_IDS = [int(x) for x in os.getenv("SUPERADMIN_IDS", "").split(",") if x.strip()]
DB_NAME = os.getenv("DB_NAME", "bot.db")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "")
CRYPTO_ASSET = os.getenv("CRYPTO_ASSET", "USDT")
CRYPTO_PAY_API_URL = os.getenv("CRYPTO_PAY_API_URL", "https://pay.crypt.bot/api")
