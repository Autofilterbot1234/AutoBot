# bot_webhook.py
import os, re, requests, asyncio
from flask import Flask, request, Response
from pymongo import MongoClient
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, TypeHandler

# --- এনভায়রনমেন্ট ভেরিয়েবল এবং কনফিগারেশন ---
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", 0))
# Render-এ ডিপ্লয় করার পর আপনার ওয়েব সার্ভিসের URL দিন
WEBHOOK_URL = os.getenv("WEBSITE_URL") 

# Check for essential variables
if not all([TOKEN, MONGO_URI, TMDB_API_KEY, ALLOWED_CHANNEL_ID, WEBHOOK_URL]):
    error_msg = "Error: One or more required environment variables are missing."
    print(error_msg)
    # exit(1) # exit করার পরিবর্তে, শুধু প্রিন্ট করা ভালো
else:
    print("All environment variables loaded successfully.")

# --- গ্লোবাল ভেরিয়েবল এবং কানেকশন ---
try:
    client = MongoClient(MONGO_URI)
    db = client["movie_db"]
    movies_collection = db["movies"]
    print("Successfully connected to MongoDB!")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}")
    # exit(1)

# Flask অ্যাপ ইনস্ট্যান্স তৈরি
app = Flask(__name__)

# --- Helper Functions (TMDb থেকে ডেটা আনা) ---
def get_tmdb_details(filename):
    clean_name = re.sub(r'[\.\[\]\(\)]', ' ', filename)
    match = re.search(r'^(.*?)\s*(\d{4})', clean_name, re.IGNORECASE)
    if not match:
        print(f"Could not parse title and year from: {filename}")
        return None, None
    title, year = match.group(1).strip(), match.group(2).strip()
    search_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={requests.utils.quote(title)}&primary_release_year={year}"
    try:
        res = requests.get(search_url, timeout=10).json()
        if not res.get("results"):
            search_url_no_year = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={requests.utils.quote(title)}"
            res = requests.get(search_url_no_year, timeout=10).json()
            if not res.get("results"): return None, None
        tmdb_id = res["results"][0]["id"]
        detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}"
        details = requests.get(detail_url, timeout=10).json()
        return details, details.get("title", title)
    except requests.RequestException as e:
        print(f"Error fetching from TMDb: {e}")
        return None, None

# --- টেলিগ্রাম মেসেজ হ্যান্ডলার ---
async def handle_movie_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or update.channel_post.chat.id != ALLOWED_CHANNEL_ID:
        return
        
    message = update.channel_post
    file = message.video or message.document
    
    if not file or not file.file_name: return

    if movies_collection.find_one({"original_filename": file.file_name}):
        print(f"Skipping '{file.file_name}' as it already exists.")
        return

    processing_message = await context.bot.send_message(
        chat_id=message.chat.id, 
        text=f"⚙️ Processing `{file.file_name}`...",
        parse_mode='Markdown'
    )
    
    details, parsed_title = get_tmdb_details(file.file_name)
    
    if not details:
        await processing_message.edit_text(text=f"❌ Could not find details for `{file.file_name}`.", parse_mode='Markdown')
        return
        
    file_id = file.file_id
    # এখানে WEBHOOK_URL ব্যবহার না করে আপনার app.py ফাইলের ডোমেইন ব্যবহার করতে হবে।
    # অথবা, যদি বট এবং ওয়েবসাইট একই সার্ভারে থাকে তাহলে os.getenv("WEBSITE_URL") ব্যবহার করা যাবে।
    main_website_url = os.getenv("WEBSITE_URL") # আপনার মূল ওয়েবসাইটের URL
    stream_link = f"{main_website_url}/stream/{file_id}"
    download_link = f"{main_website_url}/download/{file_id}"

    movie_data = {
        "title": details.get("title", parsed_title), "type": "movie",
        "tmdb_id": details.get("id"), "overview": details.get("overview"),
        "poster": f"https://image.tmdb.org/t/p/w500{details.get('poster_path')}" if details.get('poster_path') else "",
        "release_date": details.get("release_date"), "vote_average": details.get("vote_average"),
        "genres": [g['name'] for g in details.get("genres", [])], "watch_link": stream_link,
        "links": [{"quality": "Source", "url": download_link, "size": f"{file.file_size / (1024*1024):.2f} MB"}],
        "is_trending": False, "is_coming_soon": False, "poster_badge": "",
        "original_filename": file.file_name
    }
    
    try:
        result = movies_collection.insert_one(movie_data)
        movie_url = f"{main_website_url}/movie/{result.inserted_id}"
        success_text = (f"✅ **Added!**\n🎬 **Title:** {movie_data['title']}\n"
                        f"🌐 **View:** [Click Here]({movie_url})")
        await processing_message.edit_text(text=success_text, parse_mode='Markdown', disable_web_page_preview=True)
        print(f"Successfully added '{movie_data['title']}' to the database.")
    except Exception as e:
        await processing_message.edit_text(text=f"❌ DB Error: {e}")
        print(f"Database insertion failed: {e}")

# --- Flask Webhook রুট ---
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook_handler():
    """টেলিগ্রাম থেকে আসা প্রতিটি আপডেট এখানে হ্যান্ডেল করা হবে"""
    update_data = request.get_json()
    update = Update.de_json(data=update_data, bot=telegram_bot_instance.bot)
    await telegram_bot_instance.process_update(update)
    return Response(status=200)

@app.route("/set_webhook", methods=['GET'])
async def set_webhook():
    """এই URL-এ ভিজিট করলে টেলিগ্রামের কাছে Webhook সেট করা হবে"""
    webhook_set = await telegram_bot_instance.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}")
    if webhook_set:
        return "Webhook set successfully!"
    else:
        return "Webhook setup failed!"

@app.route("/")
def index():
    """সার্ভারটি যে চালু আছে তা বোঝার জন্য একটি হোমপেজ"""
    return "Bot server is running!"

# --- অ্যাপ্লিকেশন স্টার্টআপ ---
if __name__ == "__main__":
    # টেলিগ্রাম অ্যাপ্লিকেশন ইনস্ট্যান্স তৈরি
    telegram_bot_instance = (
        Application.builder().token(TOKEN).build()
    )

    # হ্যান্ডলার যোগ করা
    telegram_bot_instance.add_handler(
        TypeHandler(Update, handle_movie_upload)
    )

    # Webhook সেট করার জন্য একটি অস্থায়ী কাজ
    # প্রথমবার ডিপ্লয় করার পর /set_webhook URL-এ একবার ভিজিট করতে হবে
    # অথবা নিচের লাইনটি uncomment করতে পারেন যা প্রতিবার সার্ভার চালু হলে webhook সেট করবে
    # তবে এটি সেরা অভ্যাস নয়।
    
    # loop = asyncio.get_event_loop()
    # loop.run_until_complete(telegram_bot_instance.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}"))
    
    # Gunicorn ব্যবহার করে প্রোডাকশনে চালানোর জন্য এই ফাইলটি ব্যবহৃত হবে
    # লোকালভাবে টেস্ট করার জন্য নিচের লাইনটি ব্যবহার করুন
    # app.run(debug=True, port=5000)
