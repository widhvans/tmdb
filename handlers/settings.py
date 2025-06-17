import asyncio
import base64
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified
from database.db import (
    get_user, update_user, add_to_list, remove_from_list, 
    get_user_file_count, add_footer_button, remove_footer_button, 
    get_all_user_files, get_paginated_files, search_user_files
)
from utils.helpers import go_back_button, get_main_menu, create_post, encode_link
from handlers.new_post import get_batch_key

logger = logging.getLogger(__name__)
ACTIVE_BACKUP_TASKS = set()

async def safe_edit_message(query, *args, **kwargs):
    """A helper function to safely edit messages and handle common errors."""
    try:
        await query.message.edit_text(*args, **kwargs)
    except MessageNotModified:
        try:
            await query.answer()
        except:
            pass
    except Exception as e:
        logger.exception("Error while editing message")
        try:
            await query.answer("An error occurred. Please try again.", show_alert=True)
        except:
            pass

# --- SUB-MENU NAVIGATION AND LOGIC ---

async def get_shortener_menu_parts(user_id):
    user = await get_user(user_id)
    text = "**🔗 Shortener Settings**\n\n"
    url, api = user.get('shortener_url'), user.get('shortener_api')
    status = "✅ ON" if user.get('shortener_enabled', True) else "❌ OFF"
    if url and api:
        text += f"**Current URL:** `{url}`\n**Current API:** `{api}`"
    else:
        text += "No shortener has been set yet."
    buttons = [
        [InlineKeyboardButton("✍️ Change/Set Shortener", callback_data="set_shortener")],
        [InlineKeyboardButton(f"Toggle Shortener: {status}", callback_data="toggle_shortener")],
        [InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]
    ]
    return text, InlineKeyboardMarkup(buttons)

async def get_poster_menu_parts(user_id):
    user = await get_user(user_id)
    text = "**🖼️ IMDb Poster Settings**"
    status = "✅ ON" if user.get('show_poster', True) else "❌ OFF"
    buttons = [
        [InlineKeyboardButton(f"Toggle Poster: {status}", callback_data="toggle_poster")],
        [InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]
    ]
    return text, InlineKeyboardMarkup(buttons)

async def get_fsub_menu_parts(client, user_id):
    user = await get_user(user_id)
    text = "**📢 Manage FSub**\n\n"
    fsub_id = user.get('fsub_channel')
    if fsub_id:
        try:
            chat = await client.get_chat(fsub_id)
            text += f"**Current Channel:** {chat.title} (`{fsub_id}`)"
        except Exception:
            text += f"**Current Channel:** Could not access (`{fsub_id}`)."
    else:
        text += "No FSub channel set."
    buttons = [
        [InlineKeyboardButton("⚙️ Change/Set FSub", callback_data="set_fsub")],
        [InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]
    ]
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex(r"^(shortener|poster|fsub)_menu$"))
async def settings_submenu_handler(client, query):
    user_id = query.from_user.id
    menu_type = query.data.split("_")[0]
    
    if menu_type == "shortener":
        text, markup = await get_shortener_menu_parts(user_id)
    elif menu_type == "poster":
        text, markup = await get_poster_menu_parts(user_id)
    elif menu_type == "fsub":
        text, markup = await get_fsub_menu_parts(client, user_id)
    else:
        return
        
    await safe_edit_message(query, text=text, reply_markup=markup)

@Client.on_callback_query(filters.regex(r"toggle_(shortener|poster)"))
async def toggle_handler(client, query):
    user_id = query.from_user.id
    feature = query.data.split("_")[1]
    key = "shortener_enabled" if feature == "shortener" else "show_poster"
    
    user = await get_user(user_id)
    new_status = not user.get(key, True)
    await update_user(user_id, key, new_status)
    await query.answer(f"{feature.title()} is now {'ON' if new_status else 'OFF'}", show_alert=True)
    
    if feature == "shortener":
        text, markup = await get_shortener_menu_parts(user_id)
    else:
        text, markup = await get_poster_menu_parts(user_id)
    await safe_edit_message(query, text=text, reply_markup=markup)


# --- "MY FILES" & PERSONAL SEARCH ---

@Client.on_callback_query(filters.regex(r"my_files_(\d+)"))
async def my_files_handler(client, query):
    try:
        user_id = query.from_user.id
        page = int(query.data.split("_")[-1])
        total_files = await get_user_file_count(user_id)
        files_per_page = 5
        bot_username = client.me.username
        
        text = f"**📂 Your Saved Files ({total_files} Total)**\n\n"
        
        if total_files == 0:
            text += "You have not saved any files yet."
        else:
            files_on_page = await get_paginated_files(user_id, page, files_per_page)
            if not files_on_page:
                text += "No more files found on this page."
            else:
                for file in files_on_page:
                    # FIX: Use file_unique_id for the payload
                    payload = f"get_{file['file_unique_id']}"
                    deep_link = f"https://t.me/{bot_username}?start={payload}"
                    text += f"**File:** `{file['file_name']}`\n**Link:** [Click Here to Get File]({deep_link})\n\n"
                
        buttons, nav_row = [], []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"my_files_{page-1}"))
        if total_files > page * files_per_page:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_files_{page+1}"))
        if nav_row: buttons.append(nav_row)
        buttons.append([InlineKeyboardButton("🔍 Search My Files", callback_data="search_my_files")])
        buttons.append([InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")])
        
        await safe_edit_message(query, text=text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    except Exception:
        logger.exception("Error in my_files_handler")
        await query.answer("Something went wrong.", show_alert=True)

async def _format_and_send_search_results(client, query, user_id, search_query, page):
    files_per_page = 5
    files_list, total_files = await search_user_files(user_id, search_query, page, files_per_page)
    bot_username = client.me.username
    text = f"**🔎 Search Results for `{search_query}` ({total_files} Found)**\n\n"
    if not files_list:
        text += "No files found for your query."
    else:
        for file in files_list:
            payload = f"get_{file['file_unique_id']}"
            deep_link = f"https://t.me/{bot_username}?start={payload}"
            text += f"**File:** `{file['file_name']}`\n**Link:** [Click Here to Get File]({deep_link})\n\n"
    buttons = []
    nav_row = []
    encoded_query = base64.urlsafe_b64encode(search_query.encode()).decode().strip("=")
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_results_{page-1}_{encoded_query}"))
    if total_files > page * files_per_page:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_results_{page+1}_{encoded_query}"))
    if nav_row: buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("📚 Back to Full List", callback_data="my_files_1")])
    buttons.append([InlineKeyboardButton("« Go Back to Settings", callback_data=f"go_back_{user_id}")])
    await safe_edit_message(query, text=text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

@Client.on_callback_query(filters.regex("search_my_files"))
async def search_my_files_prompt(client, query):
    user_id = query.from_user.id
    try:
        prompt = await query.message.edit_text(
            "**🔍 Search Your Files**\n\nPlease send the name of the file you want to find.",
            reply_markup=go_back_button(user_id))
        response = await client.listen(chat_id=user_id, timeout=300, filters=filters.text)
        search_query = response.text
        await response.delete()
        await _format_and_send_search_results(client, query, user_id, search_query, 1)
    except asyncio.TimeoutError:
        await safe_edit_message(query, text="❗️ **Timeout:** Search cancelled.", reply_markup=go_back_button(user_id))
    except Exception as e:
        logger.exception("Error in search_my_files_prompt")
        await safe_edit_message(query, text=f"An error occurred: {e}", reply_markup=go_back_button(user_id))

@Client.on_callback_query(filters.regex(r"search_results_(\d+)_(.+)"))
async def search_results_paginator(client, query):
    try:
        user_id = query.from_user.id
        page = int(query.matches[0].group(1))
        encoded_query = query.matches[0].group(2)
        padding = 4 - (len(encoded_query) % 4)
        search_query = base64.urlsafe_b64decode(encoded_query + "=" * padding).decode()
        await _format_and_send_search_results(client, query, user_id, search_query, page)
    except Exception as e:
        logger.exception("Error during search pagination")
        await safe_edit_message(query, text="An error occurred during pagination.")


# --- BACKUP, FOOTERS, CHANNELS, AND OTHER SETTINGS ---

@Client.on_callback_query(filters.regex("backup_links"))
async def backup_links_handler(client, query):
    user_id = query.from_user.id
    user = await get_user(user_id)
    post_channels = user.get('post_channels', [])
    if not post_channels: return await query.answer("You have not set any Post Channels yet.", show_alert=True)
    kb = []
    for ch_id in post_channels:
        try:
            chat = await client.get_chat(ch_id)
            kb.append([InlineKeyboardButton(chat.title, callback_data=f"start_backup_{ch_id}")])
        except: continue
    if not kb: return await query.answer("Could not access any of your Post Channels.", show_alert=True)
    kb.append([InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")])
    await safe_edit_message(query, text="**🔄 Smart Backup**\n\nSelect a channel.", reply_markup=InlineKeyboardMarkup(kb))

@Client.on_callback_query(filters.regex(r"start_backup_-?\d+"))
async def start_backup_process(client, query):
    user_id = query.from_user.id
    if user_id in ACTIVE_BACKUP_TASKS: return await query.answer("A backup process is already running.", show_alert=True)
    channel_id = int(query.data.split("_")[-1])
    ACTIVE_BACKUP_TASKS.add(user_id)
    try:
        progress_msg = await query.message.edit_text("⏳ `Step 1/3:` Fetching file records...")
        all_files_cursor = await get_all_user_files(user_id)
        batches = {}
        async for file_doc in all_files_cursor:
            if not file_doc or not file_doc.get('file_name'): continue
            batch_key = get_batch_key(file_doc['file_name'])
            if batch_key not in batches: batches[batch_key] = []
            batches[batch_key].append(file_doc)
        total_batches = len(batches)
        if total_batches == 0:
            return await safe_edit_message(query, text="You have no files to back up.", reply_markup=go_back_button(user_id))
        
        await safe_edit_message(query, text=f"✅ `Step 2/3:` Found **{total_batches}** posts to create.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Backup", callback_data=f"cancel_backup_{user_id}")]]))
        
        for i, (batch_key, file_docs) in enumerate(batches.items()):
            if user_id not in ACTIVE_BACKUP_TASKS:
                await safe_edit_message(query, text="❌ Backup cancelled.", reply_markup=go_back_button(user_id))
                return
            try:
                message_ids = [int(doc['raw_link'].split('/')[-1]) for doc in file_docs]
                source_chat_id = int("-100" + file_docs[0]['raw_link'].split('/')[-2])
                file_messages = await client.get_messages(source_chat_id, message_ids)
                poster, caption, footer = await create_post(client, user_id, file_messages)
                if poster: await client.send_photo(channel_id, photo=poster, caption=caption, reply_markup=footer)
                else: await client.send_message(channel_id, caption, reply_markup=footer, disable_web_page_preview=True)
                
                progress_text = f"🔄 `Step 3/3:` Progress: {i + 1} / {total_batches} posts created."
                await safe_edit_message(query, text=progress_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Backup", callback_data=f"cancel_backup_{user_id}")]]))
                await asyncio.sleep(3)
            except Exception as e:
                logger.exception(f"Failed to post batch '{batch_key}' during backup.")
                await client.send_message(user_id, f"Failed to post batch for `{batch_key}`. Error: {e}")
        
        await query.message.delete()
        await client.send_message(user_id, "✅ **Backup Complete!**", reply_markup=go_back_button(user_id))
    except Exception as e:
        logger.exception("Major error in backup process")
        await safe_edit_message(query, text=f"A major error occurred: {e}", reply_markup=go_back_button(user_id))
    finally:
        ACTIVE_BACKUP_TASKS.discard(user_id)

@Client.on_callback_query(filters.regex(r"cancel_backup_"))
async def cancel_backup_handler(client, query):
    user_id = int(query.data.split("_")[-1])
    if query.from_user.id != user_id: return await query.answer("This is not for you.", show_alert=True)
    if user_id in ACTIVE_BACKUP_TASKS:
        ACTIVE_BACKUP_TASKS.discard(user_id)
        await query.answer("Cancellation signal sent.", show_alert=True)
    else:
        await query.answer("No active backup process found.", show_alert=True)

@Client.on_callback_query(filters.regex("manage_footer"))
async def manage_footer_handler(client, query):
    user_id = query.from_user.id
    user = await get_user(user_id)
    buttons = user.get('footer_buttons', [])
    text = "**👣 Manage Footer Buttons**\n\nYou can add up to 3 buttons."
    kb = []
    for btn in buttons:
        kb.append([InlineKeyboardButton(f"❌ {btn['name']}", callback_data=f"rm_footer_{btn['name']}")])
    if len(buttons) < 3:
        kb.append([InlineKeyboardButton("➕ Add New Button", callback_data="add_footer")])
    kb.append([InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")])
    await safe_edit_message(query, text=text, reply_markup=InlineKeyboardMarkup(kb))

@Client.on_callback_query(filters.regex("add_footer"))
async def add_footer_handler(client, query):
    user_id = query.from_user.id
    try:
        prompt_msg = await query.message.edit_text("Send the name for your new button.", reply_markup=go_back_button(user_id))
        button_name_msg = await client.listen(chat_id=user_id, timeout=300)
        await prompt_msg.edit_text(f"OK. Now, send the URL for the '{button_name_msg.text}' button.", reply_markup=go_back_button(user_id))
        button_url_msg = await client.listen(chat_id=user_id, timeout=300)
        if not button_url_msg.text.startswith(("http://", "https://")):
            return await button_url_msg.reply("Invalid URL.", reply_markup=go_back_button(user_id))
        await add_footer_button(user_id, button_name_msg.text, button_url_msg.text)
        await button_name_msg.delete()
        await button_url_msg.delete()
        await safe_edit_message(query, text="✅ New footer button added!", reply_markup=go_back_button(user_id))
    except asyncio.TimeoutError:
        await safe_edit_message(query, text="❗️ **Timeout:** Cancelled.", reply_markup=go_back_button(user_id))
    except Exception as e:
        logger.exception("Error in add_footer_handler")
        await safe_edit_message(query, text=f"An error occurred: {e}", reply_markup=go_back_button(user_id))

@Client.on_callback_query(filters.regex(r"rm_footer_"))
async def remove_footer_handler(client, query):
    button_name = query.data.split("_", 2)[2]
    await remove_footer_button(query.from_user.id, button_name)
    await query.answer("Button removed!", show_alert=True)
    await manage_footer_handler(client, query)

@Client.on_callback_query(filters.regex(r"manage_(post|db)_ch"))
async def manage_channels_handler(client, query):
    user_id = query.from_user.id
    ch_type = query.data.split("_")[1]
    ch_type_key = f"{ch_type}_channels"
    ch_type_name = "Post" if ch_type == "post" else "Database"
    user_settings = await get_user(user_id)
    channels = user_settings.get(ch_type_key, [])
    text = f"**Manage Your {ch_type_name} Channels**\n\n"
    buttons = []
    if channels:
        text += "Here are your connected channels. Click to remove."
        for ch_id in channels:
            try: chat = await client.get_chat(ch_id); buttons.append([InlineKeyboardButton(f"❌ {chat.title}", callback_data=f"rm_{ch_type}_{ch_id}")])
            except: buttons.append([InlineKeyboardButton(f"❌ Unavailable ({ch_id})", callback_data=f"rm_{ch_type}_{ch_id}")])
    else: text += "You haven't added any channels yet."
    buttons.append([InlineKeyboardButton("➕ Add New Channel", callback_data=f"add_{ch_type}_ch")])
    buttons.append([InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")])
    await safe_edit_message(query, text=text, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r"rm_(post|db)_-?\d+"))
async def remove_channel_handler(client, query):
    user_id = query.from_user.id
    _, ch_type, ch_id_str = query.data.split("_")
    ch_id = int(ch_id_str)
    ch_type_key = f"{ch_type}_channels"
    await remove_from_list(user_id, ch_type_key, ch_id)
    await query.answer("Channel removed!", show_alert=True)
    query.data = f"manage_{ch_type}_ch"
    await manage_channels_handler(client, query)

@Client.on_callback_query(filters.regex(r"add_(post|db)_ch"))
async def add_channel_prompt(client, query):
    user_id = query.from_user.id
    ch_type_short = query.data.split("_")[1]
    ch_type_key = f"{ch_type_short}_channels"
    ch_type_name = "Post" if ch_type_short == "post" else "Database"
    user_settings = await get_user(user_id)
    if ch_type_short == 'db' and len(user_settings.get(ch_type_key, [])) >= 1:
        return await query.answer("You can only connect 1 Database Channel.", show_alert=True)
    if ch_type_short == 'post' and len(user_settings.get(ch_type_key, [])) >= 3:
        return await query.answer("You can only connect up to 3 Post Channels.", show_alert=True)
    question = None
    try:
        question = await query.message.reply_text(f"Forward a message from your target **{ch_type_name} Channel**.", reply_markup=go_back_button(user_id))
        response = await client.listen(chat_id=user_id, filters=filters.forwarded, timeout=300)
        if response.forward_from_chat:
            await add_to_list(user_id, ch_type_key, response.forward_from_chat.id)
            await response.reply_text(f"✅ Connected to **{response.forward_from_chat.title}**.", reply_markup=go_back_button(user_id))
        else: await response.reply_text("Not a valid forwarded message.", reply_markup=go_back_button(user_id))
        await question.delete()
    except asyncio.TimeoutError:
        if question: await safe_edit_message(question, text="Command timed out.")
    except Exception as e:
        await query.message.reply_text(f"An error occurred: {e}", reply_markup=go_back_button(user_id))

@Client.on_callback_query(filters.regex("^show_caption$"))
async def show_caption_handler(client, query):
    user = await get_user(query.from_user.id)
    caption = user.get('filename_url', 'No filename link has been set yet.')
    await query.answer(caption, show_alert=True, cache_time=0)

@Client.on_callback_query(filters.regex("^set_filename_link$"))
async def set_filename_link_handler(client, query):
    user_id = query.from_user.id
    try:
        prompt = await query.message.edit_text("Please send the full URL you want your filenames to link to.", reply_markup=go_back_button(user_id))
        response = await client.listen(chat_id=user_id, timeout=300, filters=filters.text)
        if not response.text.startswith(("http://", "https://")):
            return await response.reply("This is not a valid URL.", reply_markup=go_back_button(user_id))
        await update_user(user_id, "filename_url", response.text)
        await response.reply_text("✅ Filename link updated!", reply_markup=go_back_button(user_id))
        await prompt.delete()
    except asyncio.TimeoutError:
        await safe_edit_message(query, text="❗️ **Timeout:** Cancelled.", reply_markup=go_back_button(user_id))
    except:
        logger.exception("Error in set_filename_link_handler")
        await safe_edit_message(query, text="An error occurred.", reply_markup=go_back_button(user_id))

@Client.on_callback_query(filters.regex("^(set_fsub|set_download)$"))
async def set_other_links_handler(client, query):
    user_id = query.from_user.id
    action = query.data.split("_")[1]
    prompts = {
        "fsub": ("📢 **Set FSub**\n\nForward a message from your FSub channel.", "fsub_channel"),
        "download": ("❓ **Set 'How to Download'**\n\nSend your tutorial URL.", "how_to_download_link")
    }
    prompt_text, key = prompts[action]
    question = None
    try:
        question = await query.message.edit_text(prompt_text, reply_markup=go_back_button(user_id))
        listen_filter = filters.forwarded if action == "fsub" else filters.text
        response = await client.listen(chat_id=user_id, timeout=300, filters=listen_filter)
        value = None
        if action == "fsub":
            if not response.forward_from_chat: return await response.reply("Not a valid forwarded message.", reply_markup=go_back_button(user_id))
            value = response.forward_from_chat.id
        else: # download
            if not response.text.startswith(("http://", "https://")): return await response.reply("Invalid URL.", reply_markup=go_back_button(user_id))
            value = response.text
        await update_user(user_id, key, value)
        await response.reply("✅ Settings updated!", reply_markup=go_back_button(user_id))
        await question.delete()
    except asyncio.TimeoutError:
        if question: await safe_edit_message(question, text="❗️ **Timeout:** Cancelled.", reply_markup=go_back_button(user_id))
    except Exception as e:
        if question: await safe_edit_message(question, text=f"An error occurred: {e}", reply_markup=go_back_button(user_id))

@Client.on_callback_query(filters.regex("^set_shortener$"))
async def set_shortener_handler(client, query):
    user_id = query.from_user.id
    try:
        domain_prompt = await query.message.edit_text(
            "**🔗 Step 1/2: Set Domain**\n\nSend your shortener domain (e.g., `earn4link.in`).",
            reply_markup=go_back_button(user_id))
        domain_msg = await client.listen(chat_id=user_id, timeout=300, filters=filters.text)
        await domain_prompt.edit_text(
            f"**🔗 Step 2/2: Set API Key**\n\nDomain: `{domain_msg.text}`\nNow, send your API key.",
            reply_markup=go_back_button(user_id))
        api_msg = await client.listen(chat_id=user_id, timeout=300, filters=filters.text)
        await update_user(user_id, "shortener_url", domain_msg.text.strip())
        await update_user(user_id, "shortener_api", api_msg.text.strip())
        await domain_msg.delete()
        await api_msg.delete()
        text, markup = await get_shortener_menu_parts(user_id)
        await safe_edit_message(query, text=text, reply_markup=markup)
    except asyncio.TimeoutError:
        await safe_edit_message(query, text="❗️ **Timeout:** Command cancelled.", reply_markup=go_back_button(user_id))
    except Exception as e:
        logger.exception("Error in set_shortener_handler")
        await safe_edit_message(query, text=f"An error occurred: {e}", reply_markup=go_back_button(user_id))
