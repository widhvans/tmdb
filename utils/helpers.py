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

def create_clean_label(filename: str) -> str:
    """
    Creates a clean, readable label for display in posts.
    It removes junk but keeps important episode/version info.
    """
    if not filename: return ""
    # Remove file extension
    label = re.sub(r'\.\w+$', '', filename)
    # Replace common delimiters with a single space
    label = re.sub(r'[\._-]', ' ', label)
    # Remove content in brackets, as it's usually promotional
    label = re.sub(r'\[.*?\]', '', label)
    # Remove any stray backticks that break markdown
    label = label.replace('`', '')
    # Collapse multiple spaces into one
    label = re.sub(r'\s+', ' ', label).strip()
    return label

def extract_base_name_and_year(name: str):
    """
    Extracts the true base name of a show/movie for matching and poster searches.
    """
    if not name: return "Untitled", None

    # Use the clean label function for initial cleaning
    cleaned_name = create_clean_label(name)

    year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_name)
    year = year_match.group(0) if year_match else None
    if year:
        cleaned_name = cleaned_name.replace(year, '')

    # Define delimiters that separate the base name from the rest
    series_delimiters = [
        r'S\d{1,2}', r'Season\s?\d{1,2}', r'Part\s?\d{1,2}'
    ]
    
    base_name = cleaned_name
    for delimiter in series_delimiters:
        match = re.search(delimiter, cleaned_name, re.I)
        if match:
            # The base name is everything before the first series delimiter found
            base_name = cleaned_name[:match.start()]
            break
    
    # Final cleanup of the extracted base name
    final_base_name = re.sub(r'\s+', ' ', base_name).strip()
    # Remove episode-specific titles that may be left over
    final_base_name = re.split(r' E\d{1,3}| EP\d{1,3}', final_base_name, flags=re.I)[0].strip()

    return (final_base_name, year) if final_base_name else ("Untitled", year)


async def create_post(client, user_id, messages):
    user = await get_user(user_id)
    if not user: return []

    primary_base_name, year = extract_base_name_and_year(getattr(messages[0].media, messages[0].media.value).file_name)
    
    # Sort files naturally by their full filename for logical order (E01, E02 etc.)
    messages.sort(key=lambda m: natural_sort_key(getattr(m.media, m.media.value).file_name))
    
    base_caption_header = f"🎬 **{primary_base_name} {f'({year})' if year else ''}**"
    post_poster = await get_poster(primary_base_name, year) if user.get('show_poster', True) else None
    
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    if len(messages) == 1:
        media = messages[0].media
        # Use the new clean label function
        file_label = create_clean_label(media.file_name)
        link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        caption_body = f"📁 `{file_label}` ({format_bytes(media.file_size)})\n\n[🔗 Click Here to Get File]({link})"
        return [(post_poster, f"{base_caption_header}\n\n{caption_body}", footer_keyboard)]
    else:
        posts, total = [], len(messages)
        num_posts = (total + FILES_PER_POST - 1) // FILES_PER_POST
        for i in range(num_posts):
            chunk = messages[i*FILES_PER_POST:(i+1)*FILES_PER_POST]
            header = f"{base_caption_header} (Part {i+1}/{num_posts})" if num_posts > 1 else base_caption_header
            links = []
            for m in chunk:
                media = getattr(m, m.media.value)
                # Use the new clean label function here as well
                label = create_clean_label(media.file_name)
                link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
                links.append(f"📁 `{label}` - [Click Here]({link})")
            
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
