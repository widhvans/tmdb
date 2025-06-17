import logging
import asyncio
from pyrogram.enums import ParseMode
from pyromod import Client
from aiohttp import web
from config import Config
from database.db import get_user, save_file_data, get_owner_db_channel
from utils.helpers import create_post
from handlers.new_post import get_batch_key

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyromod").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Web Server Handler
async def handle_redirect(request):
    file_unique_id = request.match_info.get('file_unique_id', None)
    if not file_unique_id: return web.Response(text="File ID missing.", status=400)
    try:
        with open(Config.BOT_USERNAME_FILE, 'r') as f: bot_username = f.read().strip().replace("@", "")
    except FileNotFoundError:
        logger.error(f"FATAL: Bot username file not found at {Config.BOT_USERNAME_FILE}")
        return web.Response(text="Bot configuration error.", status=500)
    return web.HTTPFound(f"https://t.me/{bot_username}?start=get_{file_unique_id}")

class Bot(Client):
    def __init__(self):
        super().__init__("FinalStorageBot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN, plugins=dict(root="handlers"))
        self.me, self.owner_db_channel_id, self.web_app, self.web_runner = None, None, None, None
        self.file_queue, self.file_batch, self.batch_locks = asyncio.Queue(), {}, {}

    async def file_processor_worker(self):
        logger.info("File processor worker started.")
        while True:
            try:
                message_to_process, user_id = await self.file_queue.get()
                if not self.owner_db_channel_id: self.owner_db_channel_id = await get_owner_db_channel()
                if not self.owner_db_channel_id:
                    logger.error("Owner DB not set. Worker is sleeping for 60s."); await asyncio.sleep(60); continue
                
                copied_message = await message_to_process.copy(self.owner_db_channel_id)
                await save_file_data(user_id, message_to_process, copied_message)
                
                filename = getattr(copied_message, copied_message.media.value).file_name
                batch_key = get_batch_key(filename)
                if not batch_key: continue

                self.batch_locks.setdefault(user_id, {})
                if batch_key not in self.batch_locks[user_id]: self.batch_locks[user_id][batch_key] = asyncio.Lock()
                
                async with self.batch_locks[user_id][batch_key]:
                    if batch_key not in self.file_batch.setdefault(user_id, {}):
                        self.file_batch[user_id][batch_key] = [copied_message]
                        asyncio.create_task(self.process_batch_task(user_id, batch_key))
                    else: self.file_batch[user_id][batch_key].append(copied_message)
            except Exception: logger.exception("Error in file processor worker")
            finally: self.file_queue.task_done()

    async def process_batch_task(self, user_id, batch_key):
        notification_messages = []
        try:
            await asyncio.sleep(5)
            if user_id not in self.batch_locks or batch_key not in self.batch_locks.get(user_id, {}): return
            
            async with self.batch_locks[user_id][batch_key]:
                messages = self.file_batch[user_id].pop(batch_key, [])
                if not messages: return
                
                user = await get_user(user_id)
                post_channels = user.get('post_channels', [])
                if not user or not post_channels: return

                logger.info(f"Sending pre-post notification for batch '{batch_key}'")
                for channel_id in post_channels:
                    try:
                        msg = await self.send_message(channel_id, "<i>✨ New releases are coming...</i>", parse_mode=ParseMode.HTML)
                        notification_messages.append(msg)
                    except Exception as e: logger.warning(f"Could not send notification to channel {channel_id}: {e}")

                posts_to_send = await create_post(self, user_id, messages)
                
                for channel_id in post_channels:
                    for post in posts_to_send:
                        poster, caption, footer = post
                        try:
                            if poster: await self.send_photo(channel_id, poster, caption=caption, reply_markup=footer)
                            else: await self.send_message(channel_id, caption, reply_markup=footer, disable_web_page_preview=True)
                            await asyncio.sleep(2)
                        except Exception as e: await self.send_message(user_id, f"Error posting to `{channel_id}`: {e}")
        except Exception:
            logger.exception(f"An error occurred in process_batch_task for user {user_id}")
        finally:
            logger.info(f"Cleaning up notifications for batch '{batch_key}'")
            for sent_msg in notification_messages:
                try: await sent_msg.delete()
                except Exception: pass
            
            if user_id in self.batch_locks and batch_key in self.batch_locks.get(user_id, {}): del self.batch_locks[user_id][batch_key]
            if user_id in self.file_batch and not self.file_batch.get(user_id, {}): del self.file_batch[user_id]
            if user_id in self.batch_locks and not self.batch_locks.get(user_id, {}): del self.batch_locks[user_id]

    async def start_web_server(self):
        self.web_app = web.Application()
        self.web_app.router.add_get('/get/{file_unique_id}', handle_redirect)
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, Config.VPS_IP, Config.VPS_PORT).start()
        logger.info(f"Web redirector started at http://{Config.VPS_IP}:{Config.VPS_PORT}")

    async def start(self):
        await super().start()
        self.me = await self.get_me()
        self.owner_db_channel_id = await get_owner_db_channel()
        if self.owner_db_channel_id: logger.info(f"Loaded Owner DB ID [{self.owner_db_channel_id}]")
        else: logger.warning("Owner DB ID not set. Use 'Set Owner DB' as admin.")
        try:
            with open(Config.BOT_USERNAME_FILE, 'w') as f: f.write(f"@{self.me.username}")
            logger.info(f"Updated bot username to @{self.me.username}")
        except Exception as e: logger.error(f"Could not write to {Config.BOT_USERNAME_FILE}: {e}")
        asyncio.create_task(self.file_processor_worker())
        await self.start_web_server()
        logger.info(f"Bot @{self.me.username} started successfully.")

    async def stop(self, *args):
        logger.info("Stopping bot...")
        if self.web_runner: await self.web_runner.cleanup()
        await super().stop()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
