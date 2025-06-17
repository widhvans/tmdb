import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.db import get_user
from features.poster import get_poster

logger = logging.getLogger(__name__)

# Max files per post before splitting.
FILES_PER_POST = 20

def clean_filename(name: str):
    """
    The master function to clean filenames, now with a smart promotional skipper.
    """
    if not name: return "Untitled", None

    # Step 1: Initial cleanup of delimiters and file extension
    cleaned_name = re.sub(r'\.\w+$', '', name)
    cleaned_name = re.sub(r'[\._-]', ' ', cleaned_name)

    # Step 2: NEW - Remove web URLs from anywhere in the name
    cleaned_name = re.sub(r'\b(www\.\S+|\S+\.(com|net|org|in|xyz|pro|me|io|club|site|co))\b', '', cleaned_name, flags=re.I)
    
    # Step 3: NEW - Smartly skip promotional words at the beginning
    words = cleaned_name.split()
    promo_keywords = [
        'movies', 'hub', 'flix', 'rips', 'team', 'group', 'hd', 'exclusive', 
        'uploader', 'channel', 'movies', 'official', 'telegram', 'series'
    ]
    words_to_skip = 0
    for word in words:
        lower_word = word.lower()
        # A word is likely promotional if it starts with @ or contains a promo keyword
        if lower_word.startswith('@') or any(promo in lower_word for promo in promo_keywords):
            words_to_skip += 1
        else:
            # Once we find a word that doesn't look like a promotion, we stop.
            break
            
    # Rebuild the name with the initial promo words skipped
    if words_to_skip > 0:
        logger.info(f"Skipped promotional words: {' '.join(words[:words_to_skip])}")
        cleaned_name = ' '.join(words[words_to_skip:])

    # Step 4: Continue with the existing robust cleaning process
    # Remove all content within brackets and parentheses
    cleaned_name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', cleaned_name)
    # Aggressively remove symbols
    cleaned_name = re.sub(r'[:|*&^%$#@!()]', ' ', cleaned_name)
    cleaned_name = re.sub(r'[^A-Za-z0-9 ]', '', cleaned_name)
    
    # Extract year
    year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_name)
    year = year_match.group(0) if year_match else None
    if year: cleaned_name = cleaned_name.replace(year, '')
        
    # Remove common technical/format tags
    tags = ['1080p', '720p', '480p', '2160p', '4k', 'HD', 'FHD', 'UHD', 'BluRay', 'WEBRip', 'WEB-DL', 'HDRip', 'x264', 'x265', 'HEVC', 'AAC', 'Dual Audio', 'Hindi', 'English', 'Esubs', 'Dubbed', r'S\d+E\d+', r'S\d+', r'Season\s?\d+', r'Part\s?\d+', r'E\d+', r'EP\d+', 'COMPLETE']
    for tag in tags:
        cleaned_name = re.sub(r'\b' + tag + r'\b', '', cleaned_name, flags=re.I)
    
    # Final cleanup of extra spaces
    final_title = re.sub(r'\s+', ' ', cleaned_name).strip()
    
    return (final_title, year) if final_title else (re.sub(r'\.\w+$', '', name).replace(".", " "), None)


async def create_post(client, user_id, messages):
    """Creates post(s) with smart formatting and automatic splitting."""
    user = await get_user(user_id)
    if not user: return []

    messages.sort(key=lambda m: natural_sort_key(getattr(m, m.media.value).file_name))
    title, year = clean_filename(getattr(messages[0], messages[0].media.value).file_name)
    base_caption_header = f"🎬 **{title} {f'({year})' if year else ''}**"
    
    post_poster = await get_poster(title, year) if user.get('show_poster', True) else None
    
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    if len(messages) == 1:
        media = getattr(messages[0], messages[0].media.value)
        file_label, _ = clean_filename(media.file_name)
        file_size_str = format_bytes(media.file_size)
        link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        caption_body = f"📁 `{file_label or media.file_name}` ({file_size_str})\n\n[🔗 Click Here to Get File]({link})"
        final_caption = f"{base_caption_header}\n\n{caption_body}"
        return [(post_poster, final_caption, footer_keyboard)]
    else:
        posts, total = [], len(messages)
        num_posts = (total + FILES_PER_POST - 1) // FILES_PER_POST
        for i in range(num_posts):
            chunk = messages[i*FILES_PER_POST:(i+1)*FILES_PER_POST]
            header = f"{base_caption_header} (Part {i+1}/{num_posts})" if num_posts > 1 else base_caption_header
            links = []
            for m in chunk:
                media = getattr(m, m.media.value)
                label, _ = clean_filename(media.file_name)
                link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
                links.append(f"📁 `{label or media.file_name}` - [Click Here]({link})")
            
            final_caption = f"{header}\n\n" + "\n\n".join(links)
            posts.append((post_poster, final_caption, footer_keyboard))
        return posts

# --- (The rest of the helper functions are unchanged and provided for completeness) ---

async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings: return InlineKeyboardMarkup([])
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    fsub_text = "⚙️ Manage FSub" if user_settings.get('fsub_channel') else "📢 Set FSub"
    buttons = [
        [InlineKeyboardButton("➕ Manage Auto Post", callback_data="manage_post_ch")],
        [InlineKeyboardButton("🗃️ Manage Index DB", callback_data="manage_db_ch")],
        [InlineKeyboardButton(shortener_text, callback_data="shortener_menu"), InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")],
        [InlineKeyboardButton("🔗 Set Filename Link", callback_data="set_filename_link"), InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")],
        [InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"), InlineKeyboardButton("📂 My Files", callback_data="my_files_1")],
        [InlineKeyboardButton(fsub_text, callback_data="set_fsub")],
        [InlineKeyboardButton("❓ How to Download", callback_data="set_download")]
    ]
    if user_id == Config.ADMIN_ID:
        buttons.append([InlineKeyboardButton("🔑 Set Owner DB", callback_data="set_owner_db")])
        buttons.append([InlineKeyboardButton("⚠️ Reset Files DB", callback_data="reset_db_prompt")])
    return InlineKeyboardMarkup(buttons)

def go_back_button(user_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]])

def format_bytes(size):
    if not isinstance(size, (int, float)): return "N/A"
    power = 1024; n = 0; power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power and n < len(power_labels) - 1:
        size /= power; n += 1
    return f"{size:.2f} {power_labels[n]}"

async def get_file_raw_link(message):
    return f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.id}"

def encode_link(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().strip("=")

def decode_link(encoded_text: str) -> str:
    padding = 4 - (len(encoded_text) % 4)
    encoded_text += "=" * padding
    return base64.urlsafe_b64decode(encoded_text).decode()

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', s)]
