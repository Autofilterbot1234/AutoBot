# telegram_bot.py
import os, re, requests, asyncio
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from dotenv import load_dotenv

# .env ফাইল থেকে ভেরিয়েবল লোড করুন
load_dotenv()

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", 0))
MONGO_URI = os.getenv("MONGO_URI")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
WEBSITE_URL = os.getenv("WEBSITE_URL") 

# Check for essential variables
if not all([TELEGRAM_BOT_TOKEN, ALLOWED_CHANNEL_ID, MONGO_URI, TMDB_API_KEY, WEBSITE_URL]):
    print("Error: One or more required environment variables are missing.")
    print("Required: TELEGRAM_BOT_TOKEN, ALLOWED_CHANNEL_ID, MONGO_URI, TMDB_API_KEY, WEBSITE_URL")
    exit(1)

# Database Connection
try:
    client = MongoClient(MONGO_URI)
    db = client["movie_db"]
    movies = db["movies"]
    print("Bot successfully connected to MongoDB!")
except Exception as e:
    print(f"Bot failed to connect to MongoDB: {e}")
    exit(1)

def get_tmdb_details(filename):
    """
    Parses a filename to extract movie title and year, then fetches details from TMDb.
    Example filename: 'The.Matrix.1999.1080p.BluRay.x264.mkv' -> title 'The Matrix', year '1999'
    """
    clean_name = re.sub(r'[\.\[\]\(\)]', ' ', filename)
    match = re.search(r'^(.*?)\s*(\d{4})', clean_name, re.IGNORECASE)
    
    if not match:
        print(f"Could not parse title and year from: {filename}")
        return None, None
    
    title = match.group(1).strip()
    year = match.group(2).strip()
    
    # First search with year for better accuracy
    search_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={requests.utils.quote(title)}&primary_release_year={year}"
    
    try:
        res = requests.get(search_url, timeout=10).json()
        if not res.get("results"):
            # If not found, search without year
            print(f"Could not find '{title}' with year {year}. Searching without year...")
            search_url_no_year = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={requests.utils.quote(title)}"
            res = requests.get(search_url_no_year, timeout=10).json()
            if not res.get("results"):
                print(f"No TMDb results for: {title}")
                return None, None
        
        tmdb_id = res["results"][0]["id"]
        detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}"
        details = requests.get(detail_url, timeout=10).json()
        
        return details, details.get("title", title)
        
    except requests.RequestException as e:
        print(f"Error fetching from TMDb: {e}")
        return None, None

async def handle_movie_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles new video/document uploads in the specified channel."""
    if not update.channel_post or update.channel_post.chat.id != ALLOWED_CHANNEL_ID:
        return
        
    message = update.channel_post
    file = message.video or message.document
    
    if not file or not file.file_name:
        return

    # Check if movie already exists to avoid duplicates
    if movies.find_one({"original_filename": file.file_name}):
        print(f"Skipping '{file.file_name}' as it already exists in the database.")
        return

    processing_message = await context.bot.send_message(
        chat_id=message.chat.id, 
        text=f"⚙️ Processing `{file.file_name}`...",
        parse_mode='Markdown'
    )
    
    details, parsed_title = get_tmdb_details(file.file_name)
    
    if not details:
        await processing_message.edit_text(text=f"❌ Could not find details for `{file.file_name}` on TMDb.", parse_mode='Markdown')
        return
        
    file_id = file.file_id
    stream_link = f"{WEBSITE_URL}/stream/{file_id}"
    download_link = f"{WEBSITE_URL}/download/{file_id}"

    movie_data = {
        "title": details.get("title", parsed_title),
        "type": "movie",
        "tmdb_id": details.get("id"),
        "overview": details.get("overview"),
        "poster": f"https://image.tmdb.org/t/p/w500{details.get('poster_path')}" if details.get('poster_path') else "",
        "release_date": details.get("release_date"),
        "vote_average": details.get("vote_average"),
        "genres": [g['name'] for g in details.get("genres", [])],
        "watch_link": stream_link,
        "links": [{"quality": "Source", "url": download_link, "size": f"{file.file_size / (1024*1024):.2f} MB"}],
        "is_trending": False,
        "is_coming_soon": False,
        "poster_badge": "",
        "original_filename": file.file_name # To prevent duplicates
    }
    
    try:
        result = movies.insert_one(movie_data)
        movie_url = f"{WEBSITE_URL}/movie/{result.inserted_id}"
        success_text = (
            f"✅ **Successfully Added!**\n\n"
            f"🎬 **Title:** {movie_data['title']}\n"
            f"🌐 **View on Website:** [Click Here]({movie_url})"
        )
        await processing_message.edit_text(text=success_text, parse_mode='Markdown', disable_web_page_preview=True)
        print(f"Successfully added '{movie_data['title']}' to the database.")
    except Exception as e:
        await processing_message.edit_text(text=f"❌ Failed to add to database: {e}")
        print(f"Database insertion failed: {e}")

async def main():
    print("Starting bot...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(MessageHandler(
        (filters.VIDEO | filters.Document.ALL) & filters.ChatType.CHANNEL,
        handle_movie_upload
    ))
    
    print(f"Bot is listening for new files in channel ID: {ALLOWED_CHANNEL_ID}...")
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
