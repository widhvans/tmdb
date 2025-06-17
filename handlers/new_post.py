import asyncio
import re
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, ChatAdminRequired, UserNotParticipant
from config import Config
from database.db import get_user, save_file_data, find_owner_by_db_channel
from utils.helpers import create_post

logger = logging.getLogger(__name__)
file_batch = {}
batch_locks = {}

# (The get_batch_key function is unchanged)
def get_batch_key(filename: str):
    name = re.sub(r'\.\w+$', '', filename)
    name = re.sub(r'[\._]', ' ', name)
    delimiters = [
        r'S\d{1,2}', r'Season\s?\d{1,2}', r'Part\s?\d{1,2}', r'E\d{1,3}', r'EP\d{1,3}',
        r'\b(19|20)\d{2}\b', r'\b(4k|2160p|1080p|720p|480p)\b', r'\[.*?\]'
    ]
    match = re.search('|'.join(delimiters), name, re.I)
    base_name = name[:match.start()].strip() if match else name.strip()
    return re.sub(r'\s+', ' ', base_name).lower()

async def process_batch(client, user_id, batch_key):
    try:
        await asyncio.sleep(2)
        if user_id not in batch_locks or batch_key not in batch_locks.get(user_id, {}): return
        async with batch_locks[user_id][batch_key]:
            messages = file_batch[user_id].pop(batch_key, [])
            if not messages: return
            
            user = await get_user(user_id)
            post_channels = user.get('post_channels', [])
            if not post_channels: return

            poster, caption, footer_keyboard = await create_post(client, user_id, messages)
            
            if caption:
                for channel_id in post_channels:
                    try:
                        if poster:
                            await client.send_photo(channel_id, photo=poster, caption=caption, reply_markup=footer_keyboard)
                        else:
                            await client.send_message(channel_id, caption, reply_markup=footer_keyboard, disable_web_page_preview=True)
                    # --- NEW: Smart Notification for Users ---
                    except (ChatAdminRequired, UserNotParticipant) as e:
                        logger.error(f"Permission error in channel {channel_id} for user {user_id}. Error: {e}")
                        await client.send_message(
                            chat_id=user_id,
                            text=f"⚠️ **Action Required!**\n\nI could not post to your channel with ID `{channel_id}`. It seems I am no longer an admin there.\n\nPlease make me an admin in that channel again to resume auto-posting."
                        )
                    except Exception as e:
                        logger.error(f"Error posting to channel `{channel_id}`: {e}")
                        await client.send_message(user_id, f"Error posting to `{channel_id}`: {e}")
    
    except Exception:
        logger.exception(f"An error occurred in process_batch for user {user_id}")
    finally:
        # Cleanup
        if user_id in batch_locks and batch_key in batch_locks.get(user_id, {}): del batch_locks[user_id][batch_key]
        if user_id in file_batch and not file_batch.get(user_id, {}): del file_batch[user_id]
        if user_id in batch_locks and not batch_locks.get(user_id, {}): del batch_locks[user_id]

@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def new_file_handler(client, message):
    # This handler now uses the globally available client.owner_db_channel_id
    # set by the bot at startup. The logic is self-contained in the worker.
    try:
        user_id = await find_owner_by_db_channel(message.chat.id)
        if not user_id: return

        media = getattr(message, message.media.value, None)
        if not media or not getattr(media, 'file_name', None): return
        
        if not client.owner_db_channel_id:
             logger.warning("Owner Database Channel not yet set up by admin. Ignoring file.")
             return
        
        # Add the message to the queue for the worker in bot.py to process
        await client.file_queue.put((message, user_id))
        logger.info(f"Added file '{media.file_name}' to the processing queue for user {user_id}.")

    except Exception:
        logger.exception("Error in new_file_handler while adding to queue")
