import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.db import get_user
from features.poster import get_poster

logger = logging.getLogger(__name__)

# Maximum number of files to include in a single post before splitting.
FILES_PER_POST = 20

async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings: return InlineKeyboardMarkup([])
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    fsub_text = "⚙️ Manage FSub" if user_settings.get('fsub_channel') else "📢 Set FSub"
    buttons = [
        [InlineKeyboardButton("➕ Manage Auto Post", callback_data="manage_post_ch")],
        [InlineKeyboardButton("🗃️ Manage Index DB", callback_data="manage_db_ch")],
        [
            InlineKeyboardButton(shortener_text, callback_data="shortener_menu"),
            InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")
        ],
        [
            InlineKeyboardButton("🔗 Set Filename Link", callback_data="set_filename_link"),
            InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")
        ],
        [
            InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"),
            InlineKeyboardButton("📂 My Files", callback_data="my_files_1")
        ],
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
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

async def get_file_raw_link(message):
    return f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.id}"

def clean_filename(name: str):
    """A more robust function to clean filenames for poster searching."""
    if not name: return "Untitled", None
    
    cleaned_name = re.sub(r'\.\w+$', '', name)
    cleaned_name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', cleaned_name)
    cleaned_name = re.sub(r'[\._-]', ' ', cleaned_name)
    
    year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_name)
    year = year_match.group(0) if year_match else None
    if year:
        cleaned_name = cleaned_name.replace(year, '')
        
    tags_to_remove = ['1080p', '720p', '480p', '2160p', '4k', 'HD', 'FHD', 'UHD', 'BluRay', 'WEBRip', 'WEB-DL', 'HDRip', 'x264', 'x265', 'HEVC', 'AAC', 'Dual Audio', 'Hindi', 'English', 'Esubs', 'Dubbed', r'S\d+E\d+', r'S\d+', r'Season\s?\d+', r'Part\s?\d+', r'E\d+', r'EP\d+']
    for tag in tags_to_remove:
        cleaned_name = re.sub(r'\b' + tag + r'\b', '', cleaned_name, flags=re.I)
    
    final_title = re.sub(r'\s+', ' ', cleaned_name).strip()
    
    if not final_title:
        final_title = re.sub(r'\.\w+$', '', name).replace(".", " ")

    return final_title, year

def encode_link(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().strip("=")

def decode_link(encoded_text: str) -> str:
    padding = 4 - (len(encoded_text) % 4)
    encoded_text += "=" * padding
    return base64.urlsafe_b64decode(encoded_text).decode()

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', s)]

async def create_post(client, user_id, messages):
    """
    Creates post(s) with smart formatting and automatic splitting.
    Returns a list of tuples, where each tuple is a post: (poster, caption, footer).
    """
    user = await get_user(user_id)
    if not user: return []

    # --- Step 1: Basic setup and sorting ---
    messages.sort(key=lambda m: natural_sort_key(getattr(m, m.media.value).file_name))
    title, year = clean_filename(getattr(messages[0], messages[0].media.value).file_name)
    base_caption_header = f"🎬 **{title} {f'({year})' if year else ''}**"
    
    post_poster = await get_poster(title, year) if user.get('show_poster', True) else None
    
    footer_buttons_data = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons_data]) if footer_buttons_data else None

    # --- Step 2: Smart Formatting (Single vs. Multi-file) ---
    if len(messages) == 1:
        msg = messages[0]
        media = getattr(msg, msg.media.value)
        file_size_str = format_bytes(media.file_size)
        file_label = re.sub(r'\[@.*?\]', '', media.file_name).strip().replace('.', ' ')
        web_server_link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        
        caption_body = f"📁 `{file_label}` ({file_size_str})\n\n[🔗 Click Here to Get File]({web_server_link})"
        final_caption = f"{base_caption_header}\n\n{caption_body}"
        
        # Return as a list with one post
        return [(post_poster, final_caption, footer_keyboard)]

    # --- Step 3: Multi-file posts with automatic splitting ---
    else:
        posts = []
        total_files = len(messages)
        # Calculate how many parts the post needs to be split into
        num_posts = (total_files + FILES_PER_POST - 1) // FILES_PER_POST

        for i in range(num_posts):
            start_index = i * FILES_PER_POST
            end_index = start_index + FILES_PER_POST
            chunk = messages[start_index:end_index]
            
            caption_header = base_caption_header
            if num_posts > 1:
                caption_header += f" (Part {i+1}/{num_posts})"
            
            links = []
            for msg in chunk:
                media = getattr(msg, msg.media.value)
                link_label = re.sub(r'\[@.*?\]', '', media.file_name).strip().replace('.', ' ')
                web_server_link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
                links.append(f"📁 `{link_label}` - [Click Here]({web_server_link})")
            
            final_caption = f"{caption_header}\n\n" + "\n".join(links)
            
            # Use the same poster and footer for all parts
            posts.append((post_poster, final_caption, footer_keyboard))
            
        return posts
