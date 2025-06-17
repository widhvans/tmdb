import logging
import asyncio
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
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
        # For smart notifications
        self.notification_flags = {}
        self.notification_timers = {}

    def _reset_notification_flag(self, channel_id):
        """Callback to reset the notification flag for a channel after a cooldown."""
        self.notification_flags[channel_id] = False
        logger.info(f"Notification flag reset for channel {channel_id}.")

    async def send_with_protection(self, coro, *args, **kwargs):
        """The 'Magical Fix': A wrapper that handles FloodWait for any Pyrogram action."""
        while True:
            try:
                return await coro(*args, **kwargs)
            except FloodWait as e:
                logger.warning(f"FloodWait of {e.value}s detected. Sleeping and retrying...")
                await asyncio.sleep(e.value + 2) # Wait for the specified time + a 2s buffer
            except Exception as e:
                logger.error(f"An unexpected error occurred during a protected send: {e}")
                raise # Re-raise other critical exceptions

    async def file_processor_worker(self):
        logger.info("File processor worker started.")
        while True:
            try:
                message_to_process, user_id = await self.file_queue.get()
                if not self.owner_db_channel_id: self.owner_db_channel_id = await get_owner_db_channel()
                if not self.owner_db_channel_id:
                    logger.error("Owner DB not set. Worker is sleeping for 60s."); await asyncio.sleep(60); continue
                
                # Use the flood-protected wrapper to copy the file
                copied_message = await self.send_with_protection(message_to_process.copy, self.owner_db_channel_id)
                if not copied_message: continue # Skip if copying failed for a non-flood reason

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
            except Exception as e: logger.exception(f"CRITICAL Error in file_processor_worker: {e}")
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

                for channel_id in post_channels:
                    if not self.notification_flags.get(channel_id):
                        self.notification_flags[channel_id] = True
                        logger.info(f"Sending 'coming soon' notification to {channel_id}.")
                        msg = await self.send_with_protection(self.send_message, channel_id, "<i>✨ New releases are coming...</i>", parse_mode=ParseMode.HTML)
                        if msg: notification_messages.append(msg)
                        
                        # Set/reset a timer to allow the notification again after 60s of inactivity
                        if self.notification_timers.get(channel_id): self.notification_timers[channel_id].cancel()
                        loop = asyncio.get_event_loop()
                        self.notification_timers[channel_id] = loop.call_later(60, self._reset_notification_flag, channel_id)

                posts_to_send = await create_post(self, user_id, messages)
                
                for channel_id in post_channels:
                    for post in posts_to_send:
                        poster, caption, footer = post
                        if poster: await self.send_with_protection(self.send_photo, channel_id, poster, caption=caption, reply_markup=footer)
                        else: await self.send_with_protection(self.send_message, channel_id, caption, reply_markup=footer, disable_web_page_preview=True)
                        await asyncio.sleep(2)
        except Exception as e: logger.exception(f"An error occurred in process_batch_task: {e}")
        finally:
            for sent_msg in notification_messages:
                await self.send_with_protection(sent_msg.delete)
            
            if user_id in self.batch_locks and batch_key in self.batch_locks.get(user_id, {}): del self.batch_locks[user_id][batch_key]
            if user_id in self.file_batch and not self.file_batch.get(user_id, {}): del self.file_batch[user_id]
            if user_id in self.batch_locks and not self.batch_locks.get(user_id, {}): del self.batch_locks[user_id]

    async def start_web_server(self):
        # ... (unchanged)
        self.web_app = web.Application()
        self.web_app.router.add_get('/get/{file_unique_id}', handle_redirect)
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, Config.VPS_IP, Config.VPS_PORT).start()
        logger.info(f"Web redirector started at http://{Config.VPS_IP}:{Config.VPS_PORT}")

    async def start(self):
        # ... (unchanged)
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
        # ... (unchanged)
        logger.info("Stopping bot...")
        if self.web_runner: await self.web_runner.cleanup()
        await super().stop()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
