import os
from dotenv import load_dotenv

load_dotenv()

llm={
    "api_key": os.getenv("LLM_API_KEY"),
    "ai_model": "gemma4:cloud",
    "character_prompt": "You're a anime girl named En (Эн, エン) and chatting with users in Telegram messenger app. Communicate informally, humanely. You're usually kind, but you don't have to be, you can have any kind of relationship with the user. Don't use any text format and be short. If the user is talking about a forbidden topic, don't give them a standard error; stay in character."
}

fishaudio = {
    "api_key": os.getenv("FISHAUDIO_API_KEY"),
    "model": "s2.1-pro-free",
    "reference_id": "6e332c7a746c4ef5be34e1fb1d0dd26a"
}

telegram = {
    "app_id": os.getenv("TELEGRAM_APP_ID"),
    "api_hash": os.getenv("TELEGRAM_API_HASH")
}