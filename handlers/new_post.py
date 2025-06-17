import logging
from pyrogram import Client, filters
from database.db import find_owner_by_db_channel
from utils.helpers import clean_filename

logger = logging.getLogger(__name__)

def get_batch_key(filename: str, num_words: int = 4):
    """
    Generates a more stable batch key based on the first N words of a cleaned title.
    This forces more aggressive and accurate merging of related files.
    """
    if not filename: return None
    
    # Get the fully cleaned title from the master cleaning function
    cleaned_title, _ = clean_filename(filename)
    if not cleaned_title: return None

    # Create the key from the first N words
    words = cleaned_title.lower().split()
    batch_key = ' '.join(words[:num_words])
    
    return batch_key

@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def new_file_handler(client, message):
    """
    Listens for new files, finds the owner, and adds the file to the processing queue.
    """
    try:
        user_id = await find_owner_by_db_channel(message.chat.id)
        if not user_id: return

        media = getattr(message, message.media.value, None)
        if not media or not getattr(media, 'file_name', None): return
        
        if not client.owner_db_channel_id:
            logger.warning("Owner Database Channel not set by admin. Ignoring file.")
            return
        
        await client.file_queue.put((message, user_id))
        logger.info(f"Added file '{media.file_name}' to the processing queue for user {user_id}.")

    except Exception:
        logger.exception("Error in new_file_handler while adding to queue")
