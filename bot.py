# app.py
import os, re, requests, asyncio
from flask import Flask, render_template_string, request, redirect, url_for, Response
from pymongo import MongoClient
from bson.objectid import ObjectId
from functools import wraps
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, TypeHandler

# --- এনভায়রনমেন্ট ভেরিয়েবল এবং কনফিগারেশন ---
load_dotenv()

app = Flask(__name__)

# Environment variables
MONGO_URI = os.getenv("MONGO_URI")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") # বট টোকেন
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", 0))
# Render-এ ডিপ্লয় করার পর আপনার ওয়েব সার্ভিসের URL দিন
WEBHOOK_URL = os.getenv("WEBSITE_URL") 

# Check for essential variables
if not all([MONGO_URI, TMDB_API_KEY, TOKEN, ADMIN_USERNAME, ADMIN_PASSWORD, ALLOWED_CHANNEL_ID, WEBHOOK_URL]):
    print("Error: One or more required environment variables are missing.")
    # exit(1) # সার্ভার চালু রাখতে exit() বন্ধ রাখা হলো

# --- Database Connection ---
try:
    client = MongoClient(MONGO_URI)
    db = client["movie_db"]
    movies = db["movies"]
    settings = db["settings"]
    print("Successfully connected to MongoDB!")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}")
    # exit(1)

# --- Telegram Bot Instance ---
# Webhook পদ্ধতিতে বটকে এভাবে ইনিশিয়ালাইজ করতে হয়
telegram_bot_instance = Application.builder().token(TOKEN).build()
bot_object = Bot(token=TOKEN) # ফাইল স্ট্রিম/ডাউনলোডের জন্য আলাদা বট অবজেক্ট

# --- Website Authentication ---
def check_auth(username, password):
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    return Response('Login Required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# === Context Processor for Ads ===
@app.context_processor
def inject_ads():
    ad_codes = settings.find_one()
    return dict(ad_settings=(ad_codes or {}))

# ------------------------------------------------------------------
# <<< START OF ALL HTML TEMPLATES >>>
# (এখানে আপনার আগের সব HTML টেমপ্লেট স্ট্রিং থাকবে)
# ------------------------------------------------------------------
index_html = """ ... """ # আপনার সম্পূর্ণ index_html এখানে পেস্ট করুন
detail_html = """ ... """ # আপনার সম্পূর্ণ detail_html এখানে পেস্ট করুন
watch_html = """ ... """  # আপনার সম্পূর্ণ watch_html এখানে পেস্ট করুন
admin_html = """ ... """  # আপনার সম্পূর্ণ admin_html এখানে পেস্ট করুন
edit_html = """ ... """   # আপনার সম্পূর্ণ edit_html এখানে পেস্ট করুন
# ------------------------------------------------------------------
# <<< END OF ALL HTML TEMPLATES >>>
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# <<< START OF WEBSITE ROUTES (home, movie_detail, admin, etc.) >>>
# (এখানে আপনার আগের সব ওয়েবসাইট রুট থাকবে)
# ------------------------------------------------------------------
@app.route('/')
def home():
    # ... আপনার আগের home() ফাংশনের কোড ...
    # islice ফিক্সসহ যে ভার্সনটি দেওয়া হয়েছিল, সেটি ব্যবহার করুন
    query = request.args.get('q')
    context = {}
    if query:
        movies_list = list(movies.find({"title": {"$regex": query, "$options": "i"}}).sort('_id', -1))
        context = { "movies": movies_list, "query": f'Results for "{query}"', "is_full_page_list": True }
    else:
        limit = 18
        all_recent = list(movies.find({"is_coming_soon": {"$ne": True}}).sort('_id', -1).limit(limit))
        hero_movies = [movie for movie in all_recent if movie.get('poster')][:5]
        context = {
            "trending_movies": list(movies.find({"is_trending": True, "is_coming_soon": {"$ne": True}}).sort('_id', -1).limit(limit)),
            "latest_movies": list(movies.find({"type": "movie", "is_coming_soon": {"$ne": True}}).sort('_id', -1).limit(limit)),
            "latest_series": list(movies.find({"type": "series", "is_coming_soon": {"$ne": True}}).sort('_id', -1).limit(limit)),
            "coming_soon_movies": list(movies.find({"is_coming_soon": True}).sort('_id', -1).limit(limit)),
            "recently_added": all_recent, "hero_movies": hero_movies,
            "is_full_page_list": False, "query": ""
        }
    for key, value in context.items():
        if isinstance(value, list):
            for item in value:
                if '_id' in item: item['_id'] = str(item['_id'])
    return render_template_string(index_html, **context)

# ... বাকি সব ওয়েবসাইট রুট (movie_detail, admin, edit_movie, delete_movie, save_ads, ইত্যাদি) ...
# ... এখানে পেস্ট করুন ...

# --- Telegram File Serving Routes ---
@app.route('/stream/<file_id>')
def stream_file(file_id):
    try:
        tg_file = bot_object.get_file(file_id)
        req = requests.get(tg_file.file_path, stream=True)
        return Response(req.iter_content(chunk_size=1024*1024), mimetype=req.headers['Content-Type'])
    except Exception as e:
        return f"Streaming error: {e}", 404

@app.route('/download/<file_id>')
def download_file(file_id):
    # ... আপনার আগের download_file ফাংশনের কোড ...
    pass # Placeholder
# ------------------------------------------------------------------
# <<< END OF WEBSITE ROUTES >>>
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# <<< START OF TELEGRAM BOT WEBHOOK LOGIC >>>
# ------------------------------------------------------------------

# --- Helper Functions (TMDb থেকে ডেটা আনা) ---
def get_tmdb_details(filename):
    clean_name = re.sub(r'[\.\[\]\(\)]', ' ', filename)
    match = re.search(r'^(.*?)\s*(\d{4})', clean_name, re.IGNORECASE)
    if not match: return None, None
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

    if movies.find_one({"original_filename": file.file_name}):
        print(f"Skipping '{file.file_name}' as it already exists.")
        return

    processing_message = await context.bot.send_message(chat_id=message.chat.id, text=f"⚙️ Processing `{file.file_name}`...", parse_mode='Markdown')
    
    details, parsed_title = get_tmdb_details(file.file_name)
    
    if not details:
        await processing_message.edit_text(text=f"❌ Could not find details for `{file.file_name}`.", parse_mode='Markdown')
        return
        
    file_id = file.file_id
    stream_link = f"{WEBHOOK_URL}/stream/{file_id}"
    download_link = f"{WEBHOOK_URL}/download/{file_id}"

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
        result = movies.insert_one(movie_data)
        movie_url = f"{WEBHOOK_URL}/movie/{result.inserted_id}"
        success_text = (f"✅ **Added!**\n🎬 **Title:** {movie_data['title']}\n"
                        f"🌐 **View:** [Click Here]({movie_url})")
        await processing_message.edit_text(text=success_text, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        await processing_message.edit_text(text=f"❌ DB Error: {e}")

# --- Flask Webhook রুট ---
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook_handler():
    update = Update.de_json(request.get_json(force=True), telegram_bot_instance.bot)
    await telegram_bot_instance.process_update(update)
    return "ok"

# --- Webhook সেট করার রুট ---
@app.route("/set_webhook", methods=['GET'])
async def set_webhook():
    webhook_set = await telegram_bot_instance.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}")
    if webhook_set:
        return "Webhook set successfully!"
    else:
        return "Webhook setup failed!"

# অ্যাপ্লিকেশন স্টার্টআপে হ্যান্ডলার যোগ করা
telegram_bot_instance.add_handler(TypeHandler(Update, handle_movie_upload))

# ------------------------------------------------------------------
# <<< END OF TELEGRAM BOT WEBHOOK LOGIC >>>
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Gunicorn এই ফাইল চালালে এই অংশটি এক্সিকিউট হবে না।
    # এটি শুধু লোকালভাবে টেস্ট করার জন্য।
    # Webhook সেট করতে নিচের লাইনটি লোকালভাবে একবার চালাতে পারেন, অথবা সার্ভারে /set_webhook ভিজিট করুন
    # asyncio.run(telegram_bot_instance.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}"))
    app.run(debug=True, port=5001)
