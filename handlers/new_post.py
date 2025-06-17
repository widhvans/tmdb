import logging
from pyrogram import Client, filters
from database.db import find_owner_by_db_channel
from utils.helpers import clean_filename

logger = logging.getLogger(__name__)


def get_batch_key(filename: str):
    """
    Generates a batch key for merging posts using the master cleaning function
    from helpers.py to ensure consistency.
    """
    if not filename: return None
    # Uses the first part of the tuple (the title) returned by clean_filename
    cleaned_title, _ = clean_filename(filename)
    return cleaned_title.lower() if cleaned_title else None


@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def new_file_handler(client, message):
    """
    This handler listens for new files in any connected DB channel,
    finds the owner, and adds the file to the processing queue in bot.py.
    """
    try:
        # Find which user this DB channel belongs to
        user_id = await find_owner_by_db_channel(message.chat.id)
        if not user_id:
            return

        media = getattr(message, message.media.value, None)
        if not media or not getattr(media, 'file_name', None):
            return
        
        # The owner_db_channel_id is the main storage where all files are copied to.
        # This check ensures the bot is properly configured by the admin.
        if not client.owner_db_channel_id:
            logger.warning("Owner Database Channel not set by admin. Ignoring file.")
            return
        
        # Add the original message and its owner's ID to the queue.
        # The worker in bot.py will handle copying and processing.
        await client.file_queue.put((message, user_id))
        logger.info(f"Added file '{media.file_name}' to the processing queue for user {user_id}.")

    except Exception:
        logger.exception("Error in new_file_handler while adding to queue")
