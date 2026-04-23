from flask import Flask
import threading
import asyncio
import os
from media_bot import main as bot_main

app = Flask(__name__)

@app.route('/')
def health():
    return "Bot is running!", 200

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_main()

if __name__ == "__main__":
    thread = threading.Thread(target=run_bot)
    thread.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
