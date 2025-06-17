import logging
import asyncio
import time
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyromod import Client
from aiohttp import web
from config import Config
from database.db import get_user, save_file_data, get_owner_db_channel
from utils.helpers import create_post, clean_filename, calculate_title_similarity

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyromod").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Web Server Handler (unchanged)
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
        self.file_queue = asyncio.Queue()
        self.open_batches = {}
        self.notification_flags, self.notification_timers = {}, {}

    def _reset_notification_flag(self, channel_id):
        self.notification_flags[channel_id] = False
        logger.info(f"Notification flag reset for channel {channel_id}.")

    async def _post_batch(self, user_id, batch_data):
        notification_messages = []
        try:
            messages = batch_data['messages']
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
                    if self.notification_timers.get(channel_id): self.notification_timers[channel_id].cancel()
                    self.notification_timers[channel_id] = asyncio.get_event_loop().call_later(60, self._reset_notification_flag, channel_id)

            posts_to_send = await create_post(self, user_id, messages)
            
            for channel_id in post_channels:
                for post in posts_to_send:
                    poster, caption, footer = post
                    if poster: await self.send_with_protection(self.send_photo, channel_id, poster, caption=caption, reply_markup=footer)
                    else: await self.send_with_protection(self.send_message, channel_id, caption, reply_markup=footer, disable_web_page_preview=True)
                    await asyncio.sleep(2)
        except Exception as e: logger.exception(f"Error finalizing batch: {e}")
        finally:
            for sent_msg in notification_messages:
                await self.send_with_protection(sent_msg.delete)
            if user_id in self.open_batches and not self.open_batches[user_id]:
                del self.open_batches[user_id]

    async def file_processor_worker(self):
        logger.info("Fuzzy Matching Worker started.")
        while True:
            try:
                message, user_id = await self.file_queue.get()
                if not self.owner_db_channel_id: self.owner_db_channel_id = await get_owner_db_channel()
                if not self.owner_db_channel_id:
                    logger.error("Owner DB not set."); await asyncio.sleep(60); continue
                
                copied_message = await self.send_with_protection(message.copy, self.owner_db_channel_id)
                if not copied_message: continue

                await save_file_data(user_id, message, copied_message)
                
                # FIXED: Correctly get the media object from the message, not from message.media
                media_object = getattr(copied_message, copied_message.media.value, None)
                if not media_object or not hasattr(media_object, 'file_name'):
                    logger.warning(f"Message {copied_message.id} has no valid media or file_name.")
                    continue
                
                new_title, _ = clean_filename(media_object.file_name)
                if not new_title: continue

                best_match_id, highest_similarity = None, 0.85
                self.open_batches.setdefault(user_id, {})

                for batch_id, data in self.open_batches[user_id].items():
                    similarity = calculate_title_similarity(new_title, data['title'])
                    if similarity > highest_similarity:
                        highest_similarity, best_match_id = similarity, batch_id
                
                loop = asyncio.get_event_loop()
                if best_match_id:
                    batch = self.open_batches[user_id][best_match_id]
                    batch['messages'].append(copied_message)
                    batch['last_added'] = time.time()
                    if batch.get('timer'): batch['timer'].cancel()
                    batch['timer'] = loop.call_later(7, lambda: asyncio.create_task(self._finalize_batch_by_id(user_id, best_match_id)))
                    logger.info(f"Added to batch '{batch['title']}' (Similarity: {highest_similarity:.2f})")
                else:
                    new_batch_id = copied_message.id
                    self.open_batches[user_id][new_batch_id] = {
                        'title': new_title, 'messages': [copied_message], 'last_added': time.time(),
                        'timer': loop.call_later(7, lambda: asyncio.create_task(self._finalize_batch_by_id(user_id, new_batch_id)))
                    }
                    logger.info(f"Created new batch for '{new_title}'")
            except Exception as e: logger.exception(f"CRITICAL Error in file_processor_worker: {e}")
            finally: self.file_queue.task_done()

    async def batch_finalizer_worker(self):
        logger.info("Batch Finalizer Worker started.")
        while True:
            try:
                await asyncio.sleep(5)
                now = time.time()
                ready_to_pop = {} 
                for user_id, batches in list(self.open_batches.items()):
                    for batch_id, data in list(batches.items()):
                        if now - data['last_added'] > 7:
                            ready_to_pop.setdefault(user_id, []).append(batch_id)
                
                if not ready_to_pop: continue
                logger.info(f"Found {sum(len(b) for b in ready_to_pop.values())} completed batches to post.")
                for user_id, batch_ids in ready_to_pop.items():
                    for batch_id in batch_ids:
                        if batch_id in self.open_batches.get(user_id, {}):
                            batch_data = self.open_batches[user_id].pop(batch_id)
                            await self._post_batch(user_id, batch_data)
            except Exception as e: logger.exception(f"Error in batch_finalizer_worker: {e}")
    
    # This function is now only used by the timer-based logic, which is removed.
    # The batch_finalizer_worker now contains the logic to pop and post.
    # To keep the code clean, I'm removing the now-redundant _finalize_batch_by_id function.

    async def send_with_protection(self, coro, *args, **kwargs):
        while True:
            try:
                return await coro(*args, **kwargs)
            except FloodWait as e:
                logger.warning(f"FloodWait of {e.value}s detected. Sleeping..."); await asyncio.sleep(e.value + 2)
            except Exception as e:
                logger.error(f"SEND_PROTECTION: An error occurred: {e}"); raise

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
        asyncio.create_task(self.batch_finalizer_worker())
        
        app = web.Application()
        app.router.add_get("/get/{file_unique_id}", handle_redirect)
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, Config.VPS_IP, Config.VPS_PORT).start()
        logger.info(f"Bot @{self.me.username} and all services started successfully.")

    async def stop(self, *args):
        logger.info("Stopping bot...");
        if hasattr(self, 'web_runner') and self.web_runner: await self.web_runner.cleanup()
        await super().stop(); logger.info("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
