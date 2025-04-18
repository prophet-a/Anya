import os
import json
import re
import requests
import random
import threading
from flask import Flask, request, jsonify
from google import genai
from personality import PERSONALITY
from context_manager import ContextManager
from scheduled_messages import ScheduledMessenger
from context_caching import ContextCache
from global_memory import GlobalMemory
import global_analysis
import time
from datetime import datetime, timedelta
from collections import defaultdict

app = Flask(__name__)

# Configure API keys
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Використовуємо напряму шлях до диску
MEMORY_PATH = '/memory/memory.json'
GLOBAL_MEMORY_PATH = '/memory/global_memory.json' # Define path for global memory
TOKEN_USAGE_FILE = '/memory/token_usage.json'
print(f"[SERVER LOG] Using disk storage at {MEMORY_PATH} and {GLOBAL_MEMORY_PATH}")

# Переконаємося, що директорія існує
try:
    os.makedirs('/memory', exist_ok=True)
    print("[SERVER LOG] Memory directory exists or was created")
except Exception as e:
    print(f"[SERVER LOG] Failed to create memory directory: {str(e)}")
    # Якщо не вдалося створити директорію, використовуємо локальне сховище
    MEMORY_PATH = 'memory.json'
    GLOBAL_MEMORY_PATH = 'global_memory.json'
    TOKEN_USAGE_FILE = 'token_usage.json'
    print("[SERVER LOG] Fallback to local storage")

# Load configuration
def load_config():
    global message_batches, MESSAGE_BATCH_TIMEOUT
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            
            # Load message batching settings
            message_batching = config.get("message_batching", {})
            if message_batching.get("enabled", True):
                MESSAGE_BATCH_TIMEOUT = message_batching.get("timeout_seconds", 2)
                print(f"Message batching enabled with timeout of {MESSAGE_BATCH_TIMEOUT} seconds")
            else:
                MESSAGE_BATCH_TIMEOUT = 0
                print("Message batching disabled")
            
            # Load summarization settings
            context_settings = config.get("context_settings", {})
            summarization = context_settings.get("summarization", {})
            if summarization.get("enabled", True):
                print("Conversation summarization enabled")
                # Settings will be used directly by the context cache class
            else:
                print("Conversation summarization disabled")
                
            return config
    except Exception as e:
        print(f"Error loading config: {str(e)}")
        return {
            "bot_name": "Анна",
            "keywords": ["Анна", "Аню"],
            "trigger_detection": {"enabled": True, "case_sensitive": False, "whole_word_only": False},
            "response_settings": {"respond_to_direct_messages": True, "respond_in_groups": True},
            "context_settings": {"enabled": True, "max_messages": 200, "memory_enabled": True},
            "group_chat_settings": {"session_enabled": True, "session_timeout_seconds": 300}
        }

CONFIG = load_config()

# Initialize context manager
context_settings = CONFIG.get("context_settings", {})
group_settings = CONFIG.get("group_chat_settings", {})
context_manager = ContextManager(
    max_messages=context_settings.get("max_messages", 200),
    memory_file=MEMORY_PATH,
    session_timeout_seconds=group_settings.get("session_timeout_seconds", 300)
)

# Initialize global memory
global_memory = GlobalMemory(config=CONFIG, memory_file=GLOBAL_MEMORY_PATH)

# Initialize context cache
context_cache = ContextCache(context_manager, CONFIG)

# Initialize scheduled messenger if enabled
scheduled_messages_config = CONFIG.get("scheduled_messages", {})
if scheduled_messages_config.get("enabled", False):
    scheduled_messenger = ScheduledMessenger(
        telegram_token=TELEGRAM_BOT_TOKEN,
        gemini_api_key=GEMINI_API_KEY,
        memory_file=MEMORY_PATH,
        config_file="config.json"
    )
    
    # Override settings from config if specified
    if "min_hours_between_messages" in scheduled_messages_config:
        scheduled_messenger.min_hours_between_messages = scheduled_messages_config["min_hours_between_messages"]
    if "max_hours_between_messages" in scheduled_messages_config:
        scheduled_messenger.max_hours_between_messages = scheduled_messages_config["max_hours_between_messages"]
    if "max_messages_per_day" in scheduled_messages_config:
        scheduled_messenger.max_messages_per_day = scheduled_messages_config["max_messages_per_day"]
    if "active_session_cooldown_minutes" in scheduled_messages_config:
        scheduled_messenger.active_session_cooldown_minutes = scheduled_messages_config["active_session_cooldown_minutes"]
    
    # Start the scheduler
    check_interval = scheduled_messages_config.get("check_interval_minutes", 15)
    scheduled_messenger.start_scheduler(check_interval_minutes=check_interval)
    print(f"Scheduled messages enabled with check interval of {check_interval} minutes")
else:
    scheduled_messenger = None
    print("Scheduled messages disabled")

# Configure Gemini
client = genai.Client(
    api_key=GEMINI_API_KEY
)

# Message batching system to handle multiple messages at once (for forwarded messages etc.)
message_batches = {}
MESSAGE_BATCH_TIMEOUT = 2  # seconds to wait for more messages

# Track token usage
token_usage = {
    "traditional": 0,
    "summarized": 0,
    "input": 0,
    "output": 0,
    "total": 0,
    "last_check_time": datetime.now().isoformat()
}

def save_token_usage():
    """Save token usage statistics to a file"""
    try:
        with open(TOKEN_USAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(token_usage, f, ensure_ascii=False, indent=2)
        print(f"[SERVER LOG] Token usage saved to {TOKEN_USAGE_FILE}")
    except Exception as e:
        print(f"[SERVER LOG] Error saving token usage: {str(e)}")

def load_token_usage():
    """Load token usage statistics from a file if it exists"""
    global token_usage
    try:
        if os.path.exists(TOKEN_USAGE_FILE):
            with open(TOKEN_USAGE_FILE, 'r', encoding='utf-8') as f:
                loaded_usage = json.load(f)
                # Update with loaded values but keep current last_check_time
                for key, value in loaded_usage.items():
                    if key != "last_check_time":
                        token_usage[key] = value
                print(f"[SERVER LOG] Token usage loaded from {TOKEN_USAGE_FILE}")
    except Exception as e:
        print(f"[SERVER LOG] Error loading token usage: {str(e)}")

# Load existing token usage statistics if available
load_token_usage()

def log_token_usage(text, usage_type="traditional"):
    """Log approximate token usage for monitoring"""
    global token_usage
    
    # Rough approximation: 1 token ~ 4 characters
    estimated_tokens = len(text) // 4
    
    # Update appropriate counters
    token_usage[usage_type] += estimated_tokens
    
    # Also update total
    token_usage["total"] += estimated_tokens
    
    # Periodically log usage stats
    # Only sum numeric values, exclude the timestamp string
    numeric_values = [v for k, v in token_usage.items() if k != "last_check_time" and isinstance(v, (int, float))]
    if sum(numeric_values) % 1000 < 10:  # Log roughly every 1000 tokens
        print(f"[SERVER LOG] Token usage stats: {token_usage}")
        
        if token_usage["traditional"] > 0 and token_usage["summarized"] > 0:
            traditional_size = token_usage["traditional"]
            summarized_size = token_usage["summarized"]
            savings = (traditional_size - summarized_size) / traditional_size * 100
            print(f"[SERVER LOG] Estimated summary savings: {savings:.2f}%")
        
        # Save token usage to file after updating
        save_token_usage()
            
    return estimated_tokens

def check_token_usage():
    """Periodically check and log token usage"""
    global token_usage
    
    now = datetime.now()
    last_check = datetime.fromisoformat(token_usage["last_check_time"])
    
    # Check if an hour has passed since the last check
    if (now - last_check).total_seconds() >= 3600:  # 3600 seconds = 1 hour
        print("\n[SERVER LOG] --- HOURLY TOKEN USAGE REPORT ---")
        print(f"[SERVER LOG] Total tokens used: {token_usage['total']}")
        print(f"[SERVER LOG] Input tokens: {token_usage['input']}")
        print(f"[SERVER LOG] Output tokens: {token_usage['output']}")
        print(f"[SERVER LOG] Traditional approach: {token_usage['traditional']}")
        print(f"[SERVER LOG] Summarized approach: {token_usage['summarized']}")
        
        if token_usage["traditional"] > 0 and token_usage["summarized"] > 0:
            savings = (token_usage["traditional"] - token_usage["summarized"]) / token_usage["traditional"] * 100
            print(f"[SERVER LOG] Summary savings: {savings:.2f}%")
        
        print(f"[SERVER LOG] Hourly rate: {token_usage['total'] / max(1, (now - last_check).total_seconds() / 3600):.2f} tokens/hour")
        print("[SERVER LOG] -------------------------------\n")
        
        # Update the last check time
        token_usage["last_check_time"] = now.isoformat()
        
        # Save token usage stats to file
        save_token_usage()

def send_message(chat_id, text, reply_to_message_id=None):
    """Send message to Telegram chat"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    # Add reply parameter if provided
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        
    response = requests.post(url, json=payload)
    return response.json()

def send_typing_action(chat_id, message_text=None):
    """
    Send typing action to Telegram chat to show 'Анна печатает...'
    If message_text is provided, simulates typing time based on message length
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction"
    payload = {
        "chat_id": chat_id,
        "action": "typing"
    }
    response = requests.post(url, json=payload)
    
    # If message text is provided, calculate typing duration
    # REMOVED sleep based on calculation
    # if message_text:
    #     # Calculate typing time: 30ms per character with min/max bounds
    #     typing_seconds = min(max(len(message_text) * 0.03, 1), 7)
    #     # time.sleep(typing_seconds) # Removed sleep for performance

    return response.json()

def generate_user_impression(username, message_count, message_sample, existing_impression=""):
    """Generate a personality-infused impression of a user based on their messages"""
    # Build a prompt that includes the bot's personality and the user's messages
    prompt = PERSONALITY + "\n\n"
    
    prompt += f"""
Зараз тобі потрібно сформувати враження про користувача {username} на основі їхніх повідомлень.
У тебе є {message_count} повідомлень від цього користувача, але я покажу тобі лише останні 50 (або менше).

Подумай, як би ти описала цю людину, базуючись на їхньому стилі спілкування, темах, які вони піднімають, 
і загальній манері їхньої поведінки в чаті. Це повинно бути короткою замальовкою, як ти сприймаєш цю людину через призму свого характеру.

Напиши це так, як ніби говориш сама із собою про людину, яку знаєш по чату. Використовуй свою звичайну манеру спілкування.
Результат має бути від першої особи (як ти сприймаєш цю людину), довжиною не більше 8 речень.

"""
    
    # If there's an existing impression, include it for continuity
    if existing_impression:
        prompt += f"\nРаніше ти думала про цю людину так:\n{existing_impression}\n\nТи можеш оновити своє враження, якщо бачиш нові деталі, або залишити його таким же, якщо воно досі актуальне.\n\n"
    
    # Include the message sample
    prompt += f"\nОсь приклади повідомлень від {username}:\n\n{message_sample}\n\n"
    
    # Final instruction
    prompt += "Напиши своє оновлене враження про цю людину з твоєї перспективи, враховуючи те, що ти знаєш про неї. Опиши, як ти її сприймаєш:"
    
    # Log input tokens for impression generation
    input_tokens = log_token_usage(prompt, "input")
    print(f"[SERVER LOG] Impression request tokens: {input_tokens}")
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        impression = response.text.strip()
        
        # Log output tokens for impression generation
        output_tokens = log_token_usage(impression, "output")
        print(f"[SERVER LOG] Impression response tokens: {output_tokens}")
        
        # Clean up any extra formatting
        if impression.startswith('"') and impression.endswith('"'):
            impression = impression[1:-1]
            
        return impression
    except Exception as e:
        print(f"Error generating user impression: {str(e)}")
        return "не змогла сформувати враження, щось пішло не так"

def process_pending_impressions(max_to_process=3):
    """Process a batch of pending user impressions"""
    # Get users needing impressions
    pending = context_manager.get_users_needing_impressions()
    
    # Process only a limited number to avoid overloading
    for i, (chat_id, user_id) in enumerate(pending[:max_to_process]):
        try:
            # Get the impression data
            data = context_manager.get_user_impression_data(chat_id, user_id)
            if not data:
                continue
                
            # Generate the impression
            impression = generate_user_impression(
                data["username"],
                data["message_count"],
                data["sample"],
                data["existing_impression"]
            )
            
            # Save the generated impression
            context_manager.save_generated_impression(chat_id, user_id, impression)
            
            print(f"Generated impression for user {data['username']} in chat {chat_id}")
            
        except Exception as e:
            print(f"Error processing impression for user {user_id} in chat {chat_id}: {str(e)}")
    
    return len(pending[:max_to_process])

def get_memory_context(chat_id, user_id=None):
    """Get memory context including user impressions for a specific chat"""
    # Get local chat-specific memory
    memory = context_manager.get_memory(chat_id)
    if not memory:
        return "" # Return empty string if no memory for chat
        
    memory_context = "Important information from memory:\n"
    
    # Combine general and user-specific info if user_id is present
    user_specific_info = {}
    if user_id:
        user_id_str = str(user_id)
        users_memory = memory.get("users", {})
        if user_id_str in users_memory:
            user_specific_info = users_memory[user_id_str].get("user_info", {})
    
    general_user_info = memory.get("user_info", {}) # Older general info
    combined_user_info = {**general_user_info, **user_specific_info} # User-specific overrides general

    if combined_user_info:
        memory_context += "User information:\n"
        for key, value in combined_user_info.items():
            memory_context += f"- {key}: {value}\n"
    
    topics = memory.get("topics_discussed", [])
    if topics:
        memory_context += "\nTopics previously discussed:\n"
        for topic in topics:
            memory_context += f"- {topic}\n"
    
    facts = memory.get("important_facts", [])
    if facts:
        memory_context += "\nImportant facts to remember:\n"
        for fact in facts:
            memory_context += f"- {fact}\n"
    
    # Add user impressions if available
    user_impressions = memory.get("user_impressions", {})
    if user_impressions:
        memory_context += "\nMy impressions of people in this chat:\n"
        for u_id, impression in user_impressions.items():
            username = "Unknown"
            # Try to find the username from conversation history
            for msg in context_manager.conversations.get(str(chat_id), []):
                if str(msg.get("user_id", "")) == u_id and msg.get("username"):
                    username = msg["username"]
                    break
                    
            memory_context += f"- {username}: {impression}\n"
    
    # Get global memory context if a user_id is provided
    if user_id:
        global_context = global_memory.get_global_context(chat_id, user_id)
        if global_context:
            memory_context += "\n" + global_context
    
    return memory_context

def generate_response(user_input, chat_id, user_id=None, username=None):
    """Generate a response using Gemini API"""
    # Get conversation history
    conversation_history = context_manager.get_conversation_context(chat_id)
    
    # Get memory context (including global user context if user_id is provided)
    memory_context = get_memory_context(chat_id, user_id)
    
    # Get summary if available
    conversation_summary = context_cache.get_conversation_summary(chat_id)
    
    # Build the prompt
    prompt = f"{PERSONALITY}\n\n"
    
    if memory_context:
        prompt += f"[Memory Context]\n{memory_context}\n\n"
    
    if conversation_summary:
        prompt += f"[Conversation Summary]\n{conversation_summary}\n\n"
        # If we have a summary, we can use a shorter conversation history (last 10 messages)
        short_history = "\n".join(conversation_history.split("\n")[-20:]) if conversation_history else ""
        prompt += f"[Recent Messages]\n{short_history}\n\n"
        log_token_usage(prompt, "summarized")
    else:
        # Otherwise use the full conversation history
        prompt += f"[Conversation History]\n{conversation_history}\n\n"
        log_token_usage(prompt, "traditional")
    
    prompt += f"User message:\n{user_input}"
    
    # Log estimated token usage for input
    log_token_usage(prompt, "input")
    
    # Generate the response with Gemini
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        
        # Log estimated token usage for output
        log_token_usage(response.text, "output")
        
        return response.text
        
    except Exception as e:
        print(f"Error generating response: {str(e)}")
        return "вибач, щось пішло не так. спробуй ще раз через хвилину"

def should_respond(text):
    """Check if the message contains keywords that should trigger a response"""
    # Always respond to direct messages if enabled
    if CONFIG["response_settings"]["respond_to_direct_messages"] and text.startswith('/'):
        return True
    
    # Check for keywords
    if not CONFIG["trigger_detection"]["enabled"]:
        return False
    
    # Prepare text for comparison
    check_text = text
    if not CONFIG["trigger_detection"]["case_sensitive"]:
        check_text = text.lower()
        keywords = [k.lower() for k in CONFIG["keywords"]]
        ignored_phrases = [p.lower() for p in CONFIG["trigger_detection"].get("ignored_phrases", [])]
    else:
        keywords = CONFIG["keywords"]
        ignored_phrases = CONFIG["trigger_detection"].get("ignored_phrases", [])
    
    # Check for ignored phrases
    for phrase in ignored_phrases:
        if phrase in check_text.lower():
            return False
    
    # Check if message should be at the beginning
    must_be_at_beginning = CONFIG["trigger_detection"].get("must_be_at_beginning", False)
    
    # Pre-process text to handle messages without spaces
    # Replace common punctuation with spaces to better isolate words
    import string
    for punct in string.punctuation:
        check_text = check_text.replace(punct, ' ')
    
    # Handle common cases when people write without spaces
    # This will help detect keywords even if written without proper spacing
    words_to_check = [check_text]  # Original text
    
    # Add first word of the message for checking
    first_word = check_text.split()[0] if check_text.split() else ""
    if first_word:
        words_to_check.append(first_word)
    
    # Check each keyword
    for keyword in keywords:
        if CONFIG["trigger_detection"]["whole_word_only"]:
            # Use regex to check for whole word match with word boundaries
            pattern = r'\b' + re.escape(keyword) + r'\b'
            
            if must_be_at_beginning:
                # Check if keyword is at the beginning of the message
                pattern = r'^\s*' + pattern
            
            # Try to match on original text
            if re.search(pattern, check_text):
                return True
                
            # Also check for the keyword at the beginning without proper spacing
            # This helps with cases like "Аняпривіт" or "Анякдела"
            if re.search(r'^' + re.escape(keyword), check_text.replace(' ', '')):
                return True
        else:
            # Check for substring match
            if must_be_at_beginning:
                # Check if keyword is at the beginning of the message
                words = check_text.split()
                if words and keyword in words[0]:
                    return True
            else:
                if keyword in check_text:
                    return True
                
                # Check if the keyword is at the beginning without proper spacing
                if check_text.replace(' ', '').startswith(keyword):
                    return True
    
    return False

def is_session_end_command(text):
    """Check if the message is a command to end the session"""
    end_commands = CONFIG.get("group_chat_settings", {}).get("end_session_commands", [])
    
    if not end_commands:
        return False
    
    text_lower = text.lower()
    
    for cmd in end_commands:
        if cmd.lower() in text_lower:
            return True
    
    return False

def handle_memory_command(chat_id, command_text):
    """Handle memory management commands"""
    parts = command_text.split(' ', 2)  # Split into maximum 3 parts
    
    if len(parts) < 2:
        return "Як юзати: /memory <дія> [дані]\nМожеш обрати: info, impressions, add, clear"
    
    action = parts[1].lower()
    
    if action == "info":
        # Display memory information
        memory = context_manager.get_memory(chat_id)
        if not memory:
            return "Слухай, я про тебе взагалі нічо не пам'ятаю. Ми знайомі?"
        
        response = "📚 *Ось що я про тебе знаю:*\n\n"
        
        user_info = memory.get("user_info", {})
        if user_info:
            response += "*Твої дані:*\n"
            for key, value in user_info.items():
                response += f"- {key}: {value}\n"
            response += "\n"
        
        topics = memory.get("topics_discussed", [])
        if topics:
            response += "*Про що вже говорили:*\n"
            for topic in topics:
                response += f"- {topic}\n"
            response += "\n"
        
        facts = memory.get("important_facts", [])
        if facts:
            response += "*Важливі штуки:*\n"
            for fact in facts:
                response += f"- {fact}\n"
        
        return response
    
    elif action == "impressions":
        # Display user impressions
        user_impressions = context_manager.get_user_impressions(chat_id)
        
        if not user_impressions:
            return "Я поки ні про кого особливої думки не маю, ще не придивилась"
        
        response = "💭 *Ось що я думаю про людей в цьому чаті:*\n\n"
        
        for user_id, impression in user_impressions.items():
            username = "Unknown"
            # Try to find the username from conversation history
            for msg in context_manager.conversations.get(str(chat_id), []):
                if str(msg.get("user_id", "")) == user_id and msg.get("username"):
                    username = msg["username"]
                    break
                    
            response += f"*{username}:* {impression}\n\n"
        
        return response
    
    elif action == "add":
        # Add information to memory
        if len(parts) < 3:
            return "Як юзати: /memory add fact|topic|user <інфа>\nНаприклад: /memory add fact Аня топить за ЖИ2"
        
        data_parts = parts[2].split(' ', 1)
        if len(data_parts) < 2:
            return "Треба вказати тип і саму інфу, ок?"
        
        info_type = data_parts[0].lower()
        info_value = data_parts[1]
        
        if info_type == "fact":
            context_manager.add_to_memory(chat_id, "important_facts", info_value)
            return f"✅ Окей, буду знати: *{info_value}*"
        elif info_type == "topic":
            context_manager.add_to_memory(chat_id, "topics_discussed", info_value)
            return f"✅ Запам'ятала, шо говорили про: *{info_value}*"
        elif info_type == "user":
            # Expect format like "name John" to set user.name = John
            user_data_parts = info_value.split(' ', 1)
            if len(user_data_parts) < 2:
                return "Формат такий: <ключ> <значення>, наприклад 'ім'я Вася'"
            
            user_key = user_data_parts[0]
            user_value = user_data_parts[1]
            context_manager.add_to_memory(chat_id, "user_info", {user_key: user_value})
            return f"✅ Тепер знаю про тебе: *{user_key}* = *{user_value}*"
        else:
            return f"❌ Е, не шарю за тип '{info_type}'. Юзай fact, topic або user, ок?"
    
    elif action == "clear":
        # Clear memory
        chat_id_str = str(chat_id)
        if chat_id_str in context_manager.memory:
            context_manager.memory[chat_id_str] = {
                "user_info": {},
                "topics_discussed": [],
                "important_facts": [],
                "last_interaction": context_manager.memory[chat_id_str].get("last_interaction")
            }
            context_manager._save_memory()
            return "✅ Всьо, нічо не пам'ятаю. Хто ти? Де я? Шо таке ЖИ2??"
        else:
            return "Та я й так тебе не знаю, тут і чистить нічо"
    
    else:
        return f"❌ Шо за '{action}'? Не знаю такого. Спробуй info, impressions, add або clear"

def handle_schedule_command(chat_id, command_text):
    """Handle commands for scheduled messages"""
    global scheduled_messenger
    
    parts = command_text.split(' ', 2)  # Split into maximum 3 parts
    
    if len(parts) < 2:
        return "Як юзати: /schedule <дія>\nМожеш обрати: status, on, off"
    
    action = parts[1].lower()
    
    if action == "status":
        # Check status of scheduled messages
        if scheduled_messenger is None:
            return "❌ Заплановані повідомлення вимкнені глобально в конфігурації"
        
        chat_id_str = str(chat_id)
        in_list = chat_id_str in scheduled_messenger.chats_to_message
        is_active = in_list and scheduled_messenger.chats_to_message[chat_id_str].get("active", False)
        
        if is_active:
            return "✅ Заплановані повідомлення увімкнені для цього чату"
        elif in_list:
            return "❌ Заплановані повідомлення вимкнені для цього чату, але чат зареєстрований"
        else:
            return "❌ Чат не зареєстрований для отримання запланованих повідомлень"
    
    elif action == "on":
        # Enable scheduled messages for this chat
        if scheduled_messenger is None:
            return "❌ Неможливо увімкнути: заплановані повідомлення вимкнені глобально в конфігурації"
        
        chat_id_str = str(chat_id)
        chat_type = "unknown"  # We don't know the type from this command handler
        
        if chat_id_str in scheduled_messenger.chats_to_message:
            scheduled_messenger.chats_to_message[chat_id_str]["active"] = True
        else:
            scheduled_messenger.register_chat(chat_id, chat_type)
        
        return "✅ Заплановані повідомлення увімкнені для цього чату"
    
    elif action == "off":
        # Disable scheduled messages for this chat
        if scheduled_messenger is None:
            return "❓ Заплановані повідомлення вже вимкнені глобально"
        
        chat_id_str = str(chat_id)
        if chat_id_str in scheduled_messenger.chats_to_message:
            scheduled_messenger.chats_to_message[chat_id_str]["active"] = False
            return "✅ Заплановані повідомлення вимкнені для цього чату"
        else:
            return "❓ Цей чат і так не отримує заплановані повідомлення"
    
    else:
        return f"❌ Шо за '{action}'? Не знаю такого. Спробуй status, on або off"

def generate_conversation_summary(chat_id):
    """Generates a concise summary of the recent conversation"""
    
    # Get the conversation context
    messages = context_manager.get_conversation_context(chat_id)
    
    # Limit input length
    if len(messages) > 1000:  # Approximately 1000 tokens
        messages = messages[-1000:]
    
    summary_prompt = f"""
    Read the recent conversation and create a brief summary (3-5 sentences)
    that captures the main topics, mood, and key points.
    This summary will be used as context for future interactions.
    
    Conversation:
    {messages}
    """
    
    # Log input tokens for summary generation
    input_tokens = log_token_usage(summary_prompt, "input")
    print(f"[SERVER LOG] Summary request tokens: {input_tokens}")
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=summary_prompt,
        )
        
        summary = response.text.strip()
        
        # Log output tokens for summary generation
        output_tokens = log_token_usage(summary, "output")
        print(f"[SERVER LOG] Summary response tokens: {output_tokens}")
        
        # Save the summary to memory
        context_cache.save_conversation_summary(chat_id, summary)
        
        return summary
    except Exception as e:
        print(f"Error generating summary: {str(e)}")
        return None

def handle_global_memory_command(chat_id, command_text):
    """Handle global memory management commands"""
    parts = command_text.split()
    if len(parts) < 2:
        return "Використання: /global_memory [users|profile|thresholds]"
    
    action = parts[1].lower()
    
    if action == "users":
        # List all users in global memory
        users = global_memory.users
        if not users:
            return "У глобальній пам'яті ще немає користувачів"
        
        response = "👥 *Користувачі в глобальній пам'яті:*\n\n"
        for user_id, user_data in users.items():
            username = user_data.get("username", "Unknown")
            total_messages = user_data.get("total_messages", 0)
            active_chats = len(user_data.get("chats", {}))
            response += f"*{username}* (ID: {user_id})\n"
            response += f"Повідомлень: {total_messages}, Активний в {active_chats} чатах\n\n"
        
        return response
    
    elif action == "profile":
        # Get profile for a specific user
        if len(parts) < 3:
            return "Використання: /global_memory profile [user_id|username]"
        
        search_term = parts[2]
        found_user = None
        
        # Try to find user by ID first
        if search_term in global_memory.users:
            found_user = global_memory.users[search_term]
        else:
            # Try to find by username
            for user_id, user_data in global_memory.users.items():
                if user_data.get("username", "").lower() == search_term.lower():
                    found_user = user_data
                    break
        
        if not found_user:
            return f"Користувача з ID або ім'ям '{search_term}' не знайдено"
        
        # Format the user profile
        user_id = found_user.get("user_id", "Unknown")
        username = found_user.get("username", "Unknown")
        total_messages = found_user.get("total_messages", 0)
        profile = found_user.get("profile", {})
        
        response = f"👤 *Профіль для {username}*\n\n"
        response += f"ID: {user_id}\n"
        response += f"Загальна кількість повідомлень: {total_messages}\n\n"
        
        if profile:
            if "personality" in profile:
                response += f"*Особистість:* {profile['personality']}\n"
            if "interests" in profile and profile["interests"]:
                response += f"*Інтереси:* {', '.join(profile['interests'])}\n"
            if "behavior_patterns" in profile and profile["behavior_patterns"]:
                response += f"*Поведінка:* {', '.join(profile['behavior_patterns'])}\n"
            if "relationship_with_bot" in profile:
                response += f"*Відносини зі мною:* {profile['relationship_with_bot']}\n\n"
        
        # Add impressions
        impressions = found_user.get("impressions", {})
        if impressions:
            response += "*Мої враження:*\n"
            for timestamp, impression in sorted(impressions.items(), reverse=True)[:3]:
                date = timestamp.split("T")[0]
                response += f"- [{date}] {impression}\n"
        
        # Add active chats
        chats = found_user.get("chats", {})
        if chats:
            response += "\n*Активний в чатах:*\n"
            for chat_id, chat_data in chats.items():
                msg_count = chat_data.get("message_count", 0)
                response += f"- Чат {chat_id}: {msg_count} повідомлень\n"
        
        return response
    
    elif action == "thresholds":
        # Get or set analysis thresholds
        if len(parts) < 3:
            # Just show current thresholds
            thresholds = global_memory.analysis_thresholds
            response = "*Налаштування аналізу:*\n\n"
            for key, value in thresholds.items():
                response += f"{key}: {value}\n"
            return response
        
        # Set a specific threshold
        if len(parts) < 4:
            return "Використання: /global_memory thresholds [key] [value]"
        
        threshold_key = parts[2]
        try:
            threshold_value = int(parts[3])
        except ValueError:
            return f"Значення має бути цілим числом"
        
        # Update threshold
        if threshold_key in global_memory.analysis_thresholds:
            updated = global_memory.update_thresholds({threshold_key: threshold_value})
            return f"Оновлено налаштування: {threshold_key} = {threshold_value}"
        else:
            return f"Невідомий параметр: {threshold_key}"
    
    return "Невідома команда. Використання: /global_memory [users|profile|thresholds]"

def should_send_followup_message(chat_id, user_id, previous_response):
    """
    Analyze if a follow-up message would be appropriate based on context and previous response.
    Returns (should_send: bool, delay_seconds: int) tuple.
    """
    # Get conversation history for analysis
    conversation = context_manager.get_conversation_context(chat_id)

    # Analyze last few exchanges
    prompt = f"""
    {PERSONALITY}

    Тобі треба вирішити, чи варто мені надіслати *додаткове*, коротке повідомлення *після* моєї попередньої відповіді. **Будь більш схильною до надсилання**, якщо це допоможе розмові бути більш живою та в моєму стилі.

    Нещодавня розмова:
    {conversation}

    Моя попередня відповідь: "{previous_response}"

    Проаналізуй мою попередню відповідь. Чи є гарна нагода додати щось коротке та доречне?

    **ПРИЧИНИ НАДІСЛАТИ (будь більш схильною):**
    1. Моя відповідь була *дуже* короткою (1 речення) і не завершеною, натякає на продовження.
    2. Я поділилася чимось особистим/цікавим/дивним, і можна *коротко* додати якусь деталь чи пов'язану думку в моєму стилі.
    3. Додаткове повідомлення зробить розмову більш природною, схожою на реальне спілкування.
    4. Питання було риторичним або відповідь була трохи сухою, можна додати емоції.

    **НЕ НАДСИЛАЙ, ЯКЩО:**
    1. Моя попередня відповідь була **довгою та вичерпною** (3+ речення).
    2. Я **вже поставила пряме питання** у попередній відповіді.
    3. Розмова **дуже формальна** або відповідь на команду (/memory, /help тощо).
    4. Попередня відповідь **чітко завершила тему**.
    5. Ти **сильно сумніваєшся**.

    **Поверни ТІЛЬКИ JSON об'єкт** з такими полями:
    `{{"should_send": true/false, "reason": "дуже коротке пояснення, чому ТАК або НІ", "delay_seconds": <ціле число секунд 1-5>}}`
    **Важливо: Поле "should_send" все ще має бути `false` за замовчуванням, але будь менш консервативною.**
    """

    # Log token usage for analysis request
    log_token_usage(prompt, "input")

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )

        # Log token usage for analysis response
        log_token_usage(response.text, "output")

        # Extract JSON from response
        import json
        import re

        # Find JSON pattern in the response
        json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group(0))
            print(f"[SERVER LOG] Follow-up Analysis: {analysis}") # Log analysis result
            return analysis.get("should_send", False), analysis.get("delay_seconds", 2)

        return False, 0

    except Exception as e:
        print(f"Error analyzing follow-up potential: {str(e)}")
        return False, 0

def generate_followup_message(chat_id, user_id, username, previous_response):
    """Generate a follow-up message based on conversation context and previous response"""
    # Get conversation context
    conversation = context_manager.get_conversation_context(chat_id)
    
    # Get memory context
    memory_context = get_memory_context(chat_id, user_id)
    
    # Build prompt for follow-up generation
    prompt = f"""{PERSONALITY}

{memory_context}

Нещодавня розмова:
{conversation}

Моя попередня відповідь була: "{previous_response}"

Згенеруй коротке, природне додаткове повідомлення (1-2 речення). Це має виглядати так, ніби я просто згадала ще одну думку або хотіла додати щось невелике до того, що щойно сказала. Зроби це невимушеним і не повторюй інформацію. Не став більше одного питання.

Моє додаткове повідомлення:"""

    # Log token usage
    log_token_usage(prompt, "input")
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        
        followup = response.text.strip()
        
        # Log token usage
        log_token_usage(followup, "output")
        
        return followup
    except Exception as e:
        print(f"Error generating follow-up message: {str(e)}")
        return None

# Handle scheduled follow-up messages
followup_queue = {}

def schedule_followup_check(chat_id, user_id, username, previous_response, delay_seconds):
    """DEPRECATED: Schedule a follow-up message to be sent after a delay. Use schedule_followup_task instead."""
    followup_key = f"{chat_id}:{int(time.time())}"
    followup_queue[followup_key] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "username": username,
        "previous_response": previous_response,
        "scheduled_time": time.time() + delay_seconds
    }
    print(f"[SERVER LOG] Scheduled follow-up check for chat {chat_id} in {delay_seconds} seconds")

def schedule_followup_task(chat_id, user_id, username, previous_response):
    """Schedules a task to potentially send a follow-up message later."""
    # The actual check (should_send_followup_message) and delay happen in process_followup_queue
    # We use a unique key including timestamp to avoid overwriting rapidly scheduled tasks
    followup_key = f"{chat_id}:{user_id}:{int(time.time())}"
    # Store the necessary info. The processing time will be handled by the queue processor.
    followup_queue[followup_key] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "username": username,
        "previous_response": previous_response,
        "scheduled_time": time.time() # Record when it was scheduled, processing logic will add delay
    }
    print(f"[SERVER LOG] Follow-up task added to queue for chat {chat_id}, user {user_id}")

def process_followup_queue():
    """Process any pending follow-up messages"""
    current_time = time.time()
    keys_to_remove = []

    for key, data in list(followup_queue.items()): # Use list() for safe iteration while modifying
        # Check if enough time has passed since scheduling to perform the analysis
        # Add a small initial delay (e.g., 5 seconds) before even checking
        initial_delay = 5
        if current_time >= data["scheduled_time"] + initial_delay:
            try:
                chat_id = data["chat_id"]
                user_id = data["user_id"]
                username = data["username"]
                previous_response = data["previous_response"]

                # --- Moved check here ---
                should_send, check_delay_seconds = should_send_followup_message(chat_id, user_id, previous_response)

                if should_send:
                     # Check if enough *additional* time has passed based on the check_delay_seconds
                     if current_time >= data["scheduled_time"] + initial_delay + check_delay_seconds:
                        # Generate and send follow-up
                        followup_text = generate_followup_message(chat_id, user_id, username, previous_response)
                        if followup_text:
                            # Send typing indication
                            send_typing_action(chat_id, followup_text)

                            # Send the follow-up message WITHOUT replying
                            send_message(chat_id, followup_text, reply_to_message_id=None)

                            # Add the follow-up to context
                            is_group = context_manager.is_group_chat(chat_id)
                            context_manager.add_message(chat_id, None, CONFIG["bot_name"], followup_text, is_bot=True, is_group=is_group)

                            print(f"[SERVER LOG] Sent follow-up message to chat {chat_id}")

                        # Mark for removal after sending (or trying to send)
                        keys_to_remove.append(key)
                     # else: Not enough time passed yet, keep in queue
                else:
                    # If should_send is false, remove from queue immediately
                    print(f"[SERVER LOG] Follow-up for chat {chat_id} deemed unnecessary.")
                    keys_to_remove.append(key)
                # --- End moved check ---

            except Exception as e:
                print(f"[SERVER LOG] Error processing follow-up for key {key}: {str(e)}")
                # Remove failing task to prevent infinite loops
                keys_to_remove.append(key)


    # Clean up processed items
    for key in keys_to_remove:
        if key in followup_queue: # Check if key still exists before deleting
             del followup_queue[key]

    return len(keys_to_remove)

# Function to run background tasks in a separate thread
def run_background_tasks():
    print("[SERVER LOG] Starting background tasks...")
    tasks_completed = 0
    try:
        # Process any pending follow-up messages
        followups_processed = process_followup_queue()
        if followups_processed > 0:
            print(f"[SERVER LOG] Background: Processed {followups_processed} follow-up messages")
            tasks_completed += followups_processed

        # Process pending user impressions in the background (rate-limited)
        impressions_processed = process_pending_impressions()
        if impressions_processed > 0:
            print(f"[SERVER LOG] Background: Processed {impressions_processed} user impressions")
            tasks_completed += impressions_processed

        # Also process global memory analyses
        analysis_results = global_analysis.process_pending_analyses(client)
        profiles_done = analysis_results.get("profiles_processed", 0)
        relationships_done = analysis_results.get("relationships_processed", 0)
        if profiles_done > 0 or relationships_done > 0:
            print(f"[SERVER LOG] Background: Processed global analyses: Profiles={profiles_done}, Relationships={relationships_done}")
            tasks_completed += profiles_done + relationships_done
        
        # --- Added Periodic Summary Generation --- 
        chats_needing_summary = context_cache.get_chats_needing_summary()
        summaries_processed = 0
        for summary_chat_id in chats_needing_summary[:3]: # Limit to 3 summaries per run
            try:
                print(f"[SERVER LOG] Background: Generating summary for chat {summary_chat_id}...")
                generate_conversation_summary(summary_chat_id) # This function now saves internally
                summaries_processed += 1
            except Exception as e:
                print(f"[SERVER LOG] Background: Error generating summary for chat {summary_chat_id}: {str(e)}")
        if summaries_processed > 0:
            print(f"[SERVER LOG] Background: Processed {summaries_processed} conversation summaries.")
            tasks_completed += summaries_processed
        # --- End Periodic Summary Generation --- 

        if tasks_completed == 0:
            print("[SERVER LOG] Background tasks finished. No pending work found.")
        else:
            print(f"[SERVER LOG] Background tasks finished. Completed {tasks_completed} operations.")
    except Exception as e:
        print(f"[SERVER LOG] Error in background tasks: {str(e)}")

# --- Dedicated Saving Thread --- 
SAVE_INTERVAL_SECONDS = 45 # Save memory every 45 seconds if changed

def periodic_save_loop(interval):
    print(f"[SERVER LOG] Starting periodic save thread (interval: {interval}s)")
    while True:
        try:
            time.sleep(interval)
            print("[SERVER LOG] Periodic save check...")
            context_saved = context_manager.save_memory_if_dirty()
            global_saved = global_memory.save_memory_if_dirty()
            if not context_saved and not global_saved:
                print("[SERVER LOG] No memory changes to save.")
        except Exception as e:
            print(f"[SERVER LOG] Error in periodic save loop: {str(e)}")
            # Avoid busy-looping on error
            time.sleep(interval)
# -----------------------------

# Create a storage for forwarded messages
forwarded_batches = {}
FORWARD_BATCH_TIMEOUT = 3  # seconds to wait for more forwarded messages

def generate_and_send_personal_note(chat_id, user_id, username, memory_context, user_impression):
    print(f"[SERVER LOG] Generating personal note for {username} in chat {chat_id}")
    personal_prompt = f"""
    {PERSONALITY}
    
    Напиши коротке особисте повідомлення для користувача {username} на основі всього, що я знаю про цю людину.
    Це повинна бути щира, особиста замітка від мене (Анни) до цієї людини.
    Не більше 3 речень. Повідомлення повинно бути дуже особистим і показувати, що я уважна до деталей
    в наших розмовах.
    
    Що я знаю про цю людину:
    {memory_context}
    
    Моє враження: {user_impression}
    """
    
    try:
        if client:
             client_response = client.models.generate_content(
                 model="gemini-2.0-flash",
                 contents=personal_prompt,
             )
             personal_note = client_response.text.strip()
             # Send the note as a separate message (without reply)
             send_message(chat_id, personal_note)
             print(f"[SERVER LOG] Sent personal note to {username} in chat {chat_id}")
        else:
             print(f"[SERVER LOG] Cannot generate personal note: Gemini client not initialized.")
    except Exception as e:
        print(f"[SERVER LOG] Error generating/sending personal note: {str(e)}")

def handle_whoami_command(chat_id, user_id, username):
    """Handle /whoami command, show user what the bot knows and thinks about them"""
    # Get memory context for the user
    memory_context = get_memory_context(chat_id, user_id)
    
    # Get user impressions
    user_impressions = context_manager.get_user_impressions(chat_id)
    user_impression = user_impressions.get(str(user_id), "")
    
    # Get global user data if available
    global_user_data = global_memory.get_user_profile(user_id)
    
    # Create response
    response = "👤 *Ось що я про тебе знаю і думаю:*\n\n"
    
    # Add local chat memory
    chat_memory = context_manager.get_memory(chat_id)
    if chat_memory:
        user_info = chat_memory.get("user_info", {})
        if user_info:
            response += "*Твої дані:*\n"
            for key, value in user_info.items():
                response += f"- {key}: {value}\n"
            response += "\n"
    
    # Add global memory if available
    if global_user_data:
        total_messages = global_user_data.get("total_messages", 0)
        response += f"*Загальна статистика:*\n"
        response += f"- Всього повідомлень: {total_messages}\n"
        response += f"- Активний(-а) в {len(global_user_data.get('chats', {}))} чатах\n\n"
        
        # Add profile data if available
        profile = global_user_data.get("profile", {})
        if profile:
            response += "*Мій погляд на тебе:*\n"
            
            if "personality" in profile:
                response += f"- Особистість: {profile['personality']}\n"
            
            if "interests" in profile and profile["interests"]:
                response += f"- Інтереси: {', '.join(profile['interests'])}\n"
            
            if "behavior_patterns" in profile and profile["behavior_patterns"]:
                response += f"- Поведінка: {', '.join(profile['behavior_patterns'])}\n"
            
            if "relationship_with_bot" in profile:
                relationship = profile["relationship_with_bot"]
                response += f"- Наші стосунки: {relationship}\n\n"
    
    # Add impression if available
    if user_impression:
        response += "\n*Моє враження про тебе:*\n"
        response += f"{user_impression}\n\n"
    else:
        response += "\n*Враження:*\n"
        response += "Я ще не сформувала чіткого враження про тебе. Ми недостатньо спілкувались.\n\n"
    
    # Add some personal touch - send main info first, then generate note
    response += "*Особисте від мене:*\n"
    response += "(Зараз спробую згадати щось особливе...) \n\n"
    
    # Send the initial response without the note (without reply)
    send_message(chat_id, response)
    # Get the text sent, excluding the placeholder part for context logging
    sent_text = response.replace("*(Зараз спробую згадати щось особливе...) \n\n*","")
    context_manager.add_message(chat_id, None, CONFIG["bot_name"], sent_text.strip(), is_bot=True, is_group=context_manager.is_group_chat(chat_id))

    # Schedule the personal note generation in a background thread
    note_thread = threading.Thread(target=generate_and_send_personal_note, 
                                 args=(chat_id, user_id, username, memory_context, user_impression))
    note_thread.start()
    
    # Return None because the main message is already sent
    return None

def handle_help_command(chat_id):
    """Display a list of available commands"""
    commands = CONFIG["response_settings"].get("commands", {})
    
    response = "📋 *Доступні команди:*\n\n"
    for cmd, desc in commands.items():
        response += f"{cmd} - {desc}\n"
    
    response += "\nТакож можеш просто написати моє ім'я і я відповім 🙂"
    
    # Send the help message without reply
    send_message(chat_id, response)
    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=context_manager.is_group_chat(chat_id))
    return None # Indicate message was sent internally

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    global message_batches, forwarded_batches
    
    data = request.get_json()
    print(f"Received webhook data")

    # Periodically check token usage
    check_token_usage()

    # Run background tasks in a separate thread
    background_thread = threading.Thread(target=run_background_tasks)
    background_thread.start()

    # Check if this is a message update
    if 'message' not in data:
        return 'OK'
    
    message = data['message']
    
    # Extract message information
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    username = message.get('from', {}).get('username', message.get('from', {}).get('first_name', 'User'))
    message_id = message.get('message_id', 0) # Get message_id for replies
    
    # Skip messages from the bot itself
    if message.get('from', {}).get('is_bot', False):
        return 'OK'
    
    # Check if this is a group chat
    is_group = message.get('chat', {}).get('type') in ['group', 'supergroup']
    
    # Update scheduled messenger if enabled
    if scheduled_messenger and chat_id:
        scheduled_messenger.register_chat(chat_id, "group" if is_group else "private")
        scheduled_messenger.update_chat_activity(chat_id)
    
    # Check if this is a forwarded message
    is_forwarded = 'forward_from' in message or 'forward_from_chat' in message or 'forward_sender_name' in message
    
    # Process message text
    if 'text' in message:
        message_text = message['text']
        
        # Process global user memory
        global_memory.process_message(chat_id, user_id, username, message_text)
        
        # Add message to context manager
        context_manager.add_message(chat_id, user_id, username, message_text, is_bot=False, is_group=is_group)
        
        # Check if message is a reply to the bot
        is_reply_to_bot = False
        reply_context = ""
        triggering_message_id = message_id # Default to current message_id
        
        if 'reply_to_message' in message and 'text' in message['reply_to_message']:
            # Keep track of the ID of the message the user replied to
            # We might want to reply to the user's message, not the message they replied to
            # triggering_message_id = message['reply_to_message'].get('message_id', message_id)

            replied_username = message['reply_to_message'].get('from', {}).get('username', 'Unknown')
            replied_text = message['reply_to_message']['text']

            # Check if the replied message was from the bot
            if 'from' in message['reply_to_message']:
                # Use bot user ID if available, otherwise compare names/usernames loosely
                replied_user_info = message['reply_to_message']['from']
                if replied_user_info.get('is_bot'):
                    # Check if it's *this* bot
                    # A more robust check would involve getting the bot's own ID via getMe
                    bot_username_from_config = CONFIG.get('bot_name', '').lower()
                    replied_bot_username = replied_user_info.get('username', '').lower()
                    replied_bot_firstname = replied_user_info.get('first_name', '').lower()
                    if bot_username_from_config in replied_bot_username or bot_username_from_config in replied_bot_firstname:
                        is_reply_to_bot = True

            # Add reply context if enabled
            if CONFIG.get("group_chat_settings", {}).get("include_reply_context", True):
                reply_context = f"[У відповідь на повідомлення від {replied_username}: \"{replied_text}\"] "
                message_text = reply_context + message_text

        # Handle /help command
        if message_text.startswith('/help'):
            # This command now sends its own message and returns None
            handle_help_command(chat_id)
            return 'OK'

        # Handle /whoami command
        if message_text.startswith('/whoami'):
            # This command now sends its own message(s) and returns None
            handle_whoami_command(chat_id, user_id, username)
            return 'OK'

        # Handle memory commands
        if message_text.startswith('/memory'):
            response = handle_memory_command(chat_id, message_text)
            # Send command response without reply
            send_message(chat_id, response)
            context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
            return 'OK'

        # Handle global memory commands
        if message_text.startswith('/global_memory'):
            response = handle_global_memory_command(chat_id, message_text)
            # Send command response without reply
            send_message(chat_id, response)
            context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
            return 'OK'

        # Handle schedule commands
        if message_text.startswith('/schedule'):
            response = handle_schedule_command(chat_id, message_text)
            # Send command response without reply
            send_message(chat_id, response)
            context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
            return 'OK'

        # Check for predefined commands
        commands = CONFIG["response_settings"].get("commands", {})
        for cmd, response in commands.items():
            if message_text.startswith(cmd):
                # Send command response without reply
                send_message(chat_id, response)
                context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
                return 'OK'

        # Determine if bot should respond
        should_force_respond = is_reply_to_bot and CONFIG["response_settings"].get("respond_to_replies", True)
        keyword_match = should_respond(message_text) or should_force_respond

        # If we shouldn't respond, check if we're in an active session
        if not keyword_match:
            # Check if this is from a user in an active session
            in_active_session = context_manager.is_session_active(chat_id, user_id)

            if in_active_session:
                # Update session
                context_manager.update_session(chat_id, user_id, username)

                # Check if this is a command to end the session
                if is_session_end_command(message_text):
                    context_manager.end_session(chat_id)
                    response_text = "давай, пінганеш"
                    # Send end session message without reply
                    send_message(chat_id, response_text)
                    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
                    return 'OK'

                # Auto reply to session participants if enabled
                if (is_group and CONFIG.get("group_chat_settings", {}).get("auto_reply_to_session_participants", True)) or not is_group:
                    keyword_match = True
            elif is_group and CONFIG.get("group_chat_settings", {}).get("auto_join_session", True) and context_manager.is_session_active(chat_id):
                # Add user to session
                context_manager.update_session(chat_id, user_id, username)

        # Special handling for forwarded messages - they get batched by chat_id
        if is_forwarded and CONFIG.get("message_batching", {}).get("enabled", True):
            forward_batch_key = f"forward:{chat_id}"
            current_time = time.time()

            # Forward sender info
            forward_from = ""
            if 'forward_from' in message and message['forward_from']:
                forward_from = message['forward_from'].get('username', message['forward_from'].get('first_name', 'Unknown'))
            elif 'forward_sender_name' in message:
                forward_from = message['forward_sender_name']
            elif 'forward_from_chat' in message:
                forward_from = f"чату {message['forward_from_chat'].get('title', 'Unknown')}"

            # Format message with its forwarded origin
            formatted_message = f"[Переслано від {forward_from}]: {message_text}"

            # Add to existing forward batch or create a new one
            if forward_batch_key in forwarded_batches and current_time - forwarded_batches[forward_batch_key]['last_update'] < FORWARD_BATCH_TIMEOUT:
                # Add to existing batch
                forwarded_batches[forward_batch_key]['messages'].append(formatted_message)
                forwarded_batches[forward_batch_key]['last_update'] = current_time
                return 'OK'
            else:
                # Create new batch
                forwarded_batches[forward_batch_key] = {
                    'messages': [formatted_message],
                    'initiator_id': user_id,  # Track who initiated the forwards
                    'initiator_name': username,
                    'created': current_time,
                    'last_update': current_time,
                    'is_group': is_group
                }

                # Wait for potential additional forwarded messages
                # time.sleep(FORWARD_BATCH_TIMEOUT) # Removed sleep for performance

                # Get all forwarded messages in batch
                batched_forwards = forwarded_batches[forward_batch_key]['messages']
                initiator_id = forwarded_batches[forward_batch_key]['initiator_id']
                initiator_name = forwarded_batches[forward_batch_key]['initiator_name']
                batch_is_group = forwarded_batches[forward_batch_key]['is_group']

                # Clean up the batch
                del forwarded_batches[forward_batch_key]

                # Combine forwarded messages for a single response if multiple messages
                if len(batched_forwards) > 1:
                    # Only respond if the bot would respond to normal messages in this context
                    if should_respond(message_text) or should_force_respond or (
                            batch_is_group and context_manager.is_session_active(chat_id, initiator_id) and
                            CONFIG.get("group_chat_settings", {}).get("auto_reply_to_session_participants", True)):

                        # Start or update session for group chats if needed
                        if batch_is_group and CONFIG.get("group_chat_settings", {}).get("session_enabled", True):
                            if not context_manager.is_session_active(chat_id):
                                context_manager.start_session(chat_id, initiator_id, initiator_name)
                            else:
                                context_manager.update_session(chat_id, initiator_id, initiator_name)

                        # Prepare combined input text
                        combined_input = f"Користувач {initiator_name} переслав кілька повідомлень:\n\n" + "\n".join(batched_forwards)

                        # Send typing indicator
                        send_typing_action(chat_id)

                        # Generate response using user context
                        response_text = generate_response(combined_input, chat_id, initiator_id, initiator_name)

                        # Send typing action with dynamic timing
                        send_typing_action(chat_id, response_text)

                        # Determine reply ID (use the original message ID that triggered the batch)
                        reply_id = message_id if batch_is_group else None

                        # Send the response
                        send_message(chat_id, response_text, reply_to_message_id=reply_id)

                        # Add bot's response to context
                        context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=batch_is_group)

                        # Always schedule potential follow-up task (delay happens in background check)
                        schedule_followup_task(chat_id, initiator_id, initiator_name, response_text)

                return 'OK'

        # Create batch key for chat+user combination for message batching (for non-forwarded messages)
        batch_key = f"{chat_id}:{user_id}"
        current_time = time.time()

        # Check if this is a message that needs a response, check batching
        if keyword_match and CONFIG.get("message_batching", {}).get("enabled", True) and not is_forwarded:
            # Add to batch if there's an active batch for this user in this chat
            if batch_key in message_batches and current_time - message_batches[batch_key]['last_update'] < MESSAGE_BATCH_TIMEOUT:
                # Add to existing batch
                message_batches[batch_key]['messages'].append(message_text)
                message_batches[batch_key]['message_ids'].append(message_id) # Store message ID
                message_batches[batch_key]['last_update'] = current_time
                # Return immediately to let more messages accumulate if they're coming
                return 'OK'
            else:
                # Create new batch
                message_batches[batch_key] = {
                    'messages': [message_text],
                    'message_ids': [message_id], # Store message ID
                    'username': username,
                    'user_id': user_id,
                    'is_group': is_group,
                    'created': current_time,
                    'last_update': current_time
                }

                # Wait for potential additional messages
                # time.sleep(MESSAGE_BATCH_TIMEOUT) # Removed sleep for performance

                # Get all messages in batch
                batched_messages = message_batches[batch_key]['messages']
                batch_user_id = message_batches[batch_key]['user_id']
                # Use the ID of the *first* message in the batch for potential reply
                batch_reply_trigger_id = message_batches[batch_key]['message_ids'][0]

                # Clean up the batch
                del message_batches[batch_key]

                # Check if session already exists or create one for group and private chats
                if is_group and CONFIG.get("group_chat_settings", {}).get("session_enabled", True):
                    if not context_manager.is_session_active(chat_id):
                        # Start a new session
                        context_manager.start_session(chat_id, user_id, username)
                    else:
                        # Update existing session
                        context_manager.update_session(chat_id, user_id, username)
                elif not is_group:
                    # For private chats, always maintain a session
                    if not context_manager.is_session_active(chat_id):
                        context_manager.start_session(chat_id, user_id, username)
                    else:
                        context_manager.update_session(chat_id, user_id, username)

                # If we have multiple messages, combine them for a single response
                if len(batched_messages) > 1:
                    combined_input = "Користувач надіслав кілька повідомлень:\n\n" + "\n".join([f"- {msg}" for msg in batched_messages])
                    response_text = generate_response(combined_input, chat_id, batch_user_id, username)

                    # Send typing action with message length for dynamic typing duration
                    send_typing_action(chat_id, response_text)

                    # Determine reply ID (use the first message ID of the batch if group)
                    reply_id = batch_reply_trigger_id if is_group else None
                    send_message(chat_id, response_text, reply_to_message_id=reply_id)

                    # Add bot response to context
                    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)

                    # Always schedule potential follow-up task (delay happens in background check)
                    schedule_followup_task(chat_id, batch_user_id, username, response_text)

                    return 'OK'
                else:
                    # Single message processing continues with normal flow
                    message_text = batched_messages[0]
                    # Use the stored message ID for potential reply
                    triggering_message_id = batch_reply_trigger_id


        # Process the message if we should respond (this handles single messages or fall-through from batching)
        if keyword_match:
            try:
                # Start or update session for both group chats and private chats
                if is_group and CONFIG.get("group_chat_settings", {}).get("session_enabled", True):
                    if not context_manager.is_session_active(chat_id):
                        # Start new session
                        context_manager.start_session(chat_id, user_id, username)
                    else:
                        # Update existing session
                        context_manager.update_session(chat_id, user_id, username)
                # Handle private chats - always start or update a session
                elif not is_group:
                    if not context_manager.is_session_active(chat_id):
                        # Start new session for private chat
                        context_manager.start_session(chat_id, user_id, username)
                    else:
                        # Update existing session
                        context_manager.update_session(chat_id, user_id, username)

                # Send typing indicator
                send_typing_action(chat_id)

                # Generate response using user context
                response_text = generate_response(message_text, chat_id, user_id, username)

                # Determine reply ID (use triggering_message_id if group)
                reply_id = triggering_message_id if is_group else None

                # Send response
                send_message(chat_id, response_text, reply_to_message_id=reply_id)

                # Add bot's response to context
                context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)

                # Always schedule potential follow-up task (delay happens in background check)
                schedule_followup_task(chat_id, user_id, username, response_text)

            except Exception as e:
                print(f"Error generating response: {str(e)}")
                # Send error message without reply
                send_message(chat_id, "вибач, щось пішло не так. спробуй ще раз через хвилину")

    return 'OK'

@app.route('/')
def index():
    """Simple health check endpoint"""
    return "Bot is running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

    # Start the periodic save thread
    save_thread = threading.Thread(target=periodic_save_loop, args=(SAVE_INTERVAL_SECONDS,), daemon=True)
    save_thread.start() 