import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.db import get_user
from features.poster import get_poster
from thefuzz import fuzz

logger = logging.getLogger(__name__)

FILES_PER_POST = 20

def calculate_title_similarity(title1: str, title2: str) -> float:
    """Calculates the similarity between two titles using fuzzy matching."""
    return fuzz.token_sort_ratio(title1, title2) / 100.0

def extract_base_name_and_year(name: str):
    """
    The core engine. Extracts the true base name of a show/movie,
    separating it from episode details, year, and quality tags.
    """
    if not name: return "Untitled", None

    # Initial cleanup
    cleaned_name = re.sub(r'\.\w+$', '', name) # Remove extension
    cleaned_name = re.sub(r'\[.*?\]', '', cleaned_name) # Remove content in brackets
    cleaned_name = re.sub(r'[\._-]', ' ', cleaned_name) # Replace delimiters

    # Extract year first, as it can be a strong delimiter
    year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_name)
    year = year_match.group(0) if year_match else None
    if year:
        cleaned_name = cleaned_name.replace(year, '')

    # Define delimiters that separate the base name from the rest
    series_delimiters = [
        r'S\d{1,2}E\d{1,3}', r'S\d{1,2}', r'Season\s?\d{1,2}',
        r'E\d{1,3}', r'EP\d{1,3}', 'Part\s?\d{1,2}'
    ]
    
    base_name = cleaned_name
    for delimiter in series_delimiters:
        match = re.search(delimiter, cleaned_name, re.I)
        if match:
            # The base name is everything before the first series delimiter found
            base_name = cleaned_name[:match.start()]
            break

    # Final cleanup of the extracted base name
    tags = ['10bit', '6ch', '5 1', 'ds4k', 'sony', 'dd', 'unrated', 'dc', 'hev', 'esub', 'dual', 'au', 'hin', '1080p', '720p', '480p', '2160p', '4k', 'HD', 'FHD', 'UHD', 'BluRay', 'WEBRip', 'WEB-DL', 'HDRip', 'x264', 'x265', 'HEVC', 'AAC', 'Dual Audio', 'Hindi', 'English', 'Esubs', 'Dubbed', 'COMPLETE', 'WEB-SERIES']
    for tag in tags:
        base_name = re.sub(r'\b' + re.escape(tag) + r'\b', '', base_name, flags=re.I)
    
    final_base_name = re.sub(r'\s+', ' ', base_name).strip()

    return (final_base_name, year) if final_base_name else ("Untitled", year)


async def create_post(client, user_id, messages):
    user = await get_user(user_id)
    if not user: return []

    primary_base_name, year = extract_base_name_and_year(getattr(messages[0].media, messages[0].media.value).file_name)
    
    def similarity_sorter(msg):
        return natural_sort_key(getattr(msg.media, msg.media.value).file_name)

    messages.sort(key=similarity_sorter)
    
    base_caption_header = f"🎬 **{primary_base_name} {f'({year})' if year else ''}**"
    post_poster = await get_poster(primary_base_name, year) if user.get('show_poster', True) else None
    
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    if len(messages) == 1:
        media = messages[0].media
        file_label = re.sub(r'\[@.*?\]', '', media.file_name).strip()
        link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        caption_body = f"📁 `{file_label}` ({format_bytes(media.file_size)})\n\n[🔗 Click Here to Get File]({link})"
        return [(post_poster, f"{base_caption_header}\n\n{caption_body}", footer_keyboard)]
    else:
        posts, total = [], len(messages)
        num_posts = (total + FILES_PER_POST - 1) // FILES_PER_POST
        for i in range(num_posts):
            chunk = messages[i*FILES_PER_POST:(i+1)*FILES_PER_POST]
            header = f"{base_caption_header} (Part {i+1}/{num_posts})" if num_posts > 1 else base_caption_header
            links = [f"📁 `{re.sub(r'\[@.*?\]', '', m.media.file_name).strip()}` - [Click Here](http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{m.media.file_unique_id})" for m in chunk]
            final_caption = f"{header}\n\n" + "\n\n".join(links)
            posts.append((post_poster, final_caption, footer_keyboard))
        return posts


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
