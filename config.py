import os

class Config:
    # Your API details from my.telegram.org
    API_ID = int(os.environ.get("API_ID", "10389378"))
    API_HASH = os.environ.get("API_HASH", "cdd5c820cb6abeecaef38e2bb8db4860")

    # Your Bot Token
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "7320891454:AAHp3AAIZK2RKIkWyYIByB_fSEq9Xuk9-bk")

    # Your Admin User ID
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "1938030055"))

    # Your MongoDB Connection String
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://soniji:chaloji@cluster0.i5zy74f.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
    DATABASE_NAME = os.environ.get("DATABASE_NAME", "telegram_bot")
    
    # --- Your VPS IP Address and Port ---
    # The domain where your bot is running. Use your VPS IP.
    # DO NOT include http://
    VPS_IP = os.environ.get("VPS_IP", "65.21.183.36")
    
    # --- PORT CHANGED TO 4040 AS REQUESTED ---
    VPS_PORT = int(os.environ.get("VPS_PORT", 7071))
    
    # The name of the file that stores your bot's username
    BOT_USERNAME_FILE = "bot_username.txt"
