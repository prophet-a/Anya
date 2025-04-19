import os
import json
import re
import requests
import random
from flask import Flask, request, jsonify
from google import genai
from personality import PERSONALITY
from context_manager import ContextManager
from scheduled_messages import ScheduledMessenger
from context_caching import ContextCache
import time
from datetime import datetime, timedelta
from collections import defaultdict

app = Flask(__name__)

# Configure API keys
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Debug: Print all environment variables
print("[SERVER LOG] All environment variables:")
for key, value in os.environ.items():
    print(f"[SERVER LOG] {key}: {value}")

# Set disk path for persistent storage on Render
MEMORY_PATH = os.environ.get('RENDER_DISK_PATH', '')
if MEMORY_PATH:
    MEMORY_PATH = os.path.join(MEMORY_PATH, 'memory.json')
    TOKEN_USAGE_FILE = os.path.join(os.environ.get('RENDER_DISK_PATH', ''), 'token_usage.json')
    print(f"[SERVER LOG] Using disk storage at {MEMORY_PATH}")
else:
    MEMORY_PATH = 'memory.json'
    TOKEN_USAGE_FILE = "token_usage.json"
    print("[SERVER LOG] No disk path found, using local storage")

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
client = genai.Client(api_key=GEMINI_API_KEY)

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
    if sum(token_usage.values()) % 1000 < 10:  # Log roughly every 1000 tokens
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

def send_message(chat_id, text):
    """Send message to Telegram chat"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
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
    if message_text:
        # Calculate typing time: 30ms per character with min/max bounds
        typing_seconds = min(max(len(message_text) * 0.03, 1), 7)
        time.sleep(typing_seconds)
        
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
            contents=prompt
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

def get_memory_context(chat_id):
    """Get memory context including user impressions for a specific chat"""
    memory = context_manager.get_memory(chat_id)
    if not memory:
        return ""
        
    memory_context = "Important information from memory:\n"
    
    user_info = memory.get("user_info", {})
    if user_info:
        memory_context += "User information:\n"
        for key, value in user_info.items():
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
        for user_id, impression in user_impressions.items():
            username = "Unknown"
            # Try to find the username from conversation history
            for msg in context_manager.conversations.get(str(chat_id), []):
                if str(msg.get("user_id", "")) == user_id and msg.get("username"):
                    username = msg["username"]
                    break
                    
            memory_context += f"- {username}: {impression}\n"
    
    return memory_context

def generate_response(user_input, chat_id):
    """Generate response using Gemini API with conversation summarization"""
    # Check for token usage first
    check_token_usage()
    
    # Check if summarization is enabled
    if context_cache.enabled and context_cache.summarization_enabled:
        # Check if we need to create or update summary
        needs_summary = context_cache.should_create_summary(chat_id)
        if needs_summary:
            summary = generate_conversation_summary(chat_id)
            if summary:
                # Log token usage for summary generation
                log_token_usage(summary, "summarized")
    
    # Get conversation context
    conversation_context = ""
    if context_settings.get("enabled", True):
        conversation_context = context_manager.get_conversation_context(chat_id)
    
    # Get summary if enabled
    summary = None
    if context_cache.enabled and context_cache.summarization_enabled:
        summary = context_cache.get_conversation_summary(chat_id)
    
    # Get memory context
    memory_context = ""
    if context_settings.get("memory_enabled", True):
        memory_context = get_memory_context(chat_id)
    
    # Build complete prompt
    full_traditional_prompt = PERSONALITY + "\n\n"
    actual_prompt = PERSONALITY + "\n\n"
    
    if memory_context:
        full_traditional_prompt += memory_context + "\n\n"
        actual_prompt += memory_context + "\n\n"
    
    # Always add the full conversation context to our traditional measurement
    if conversation_context:
        full_traditional_prompt += conversation_context + "\n\n"
    
    # For the actual prompt, use summary if available and limit history
    if summary:
        actual_prompt += f"Previous conversation summary:\n{summary}\n\n"
        
        # Limit history to last 20 messages if we have a summary
        if conversation_context:
            conversation_lines = conversation_context.split('\n')
            if len(conversation_lines) > 20:
                conversation_context_short = "Recent messages:\n" + "\n".join(conversation_lines[-20:])
                actual_prompt += conversation_context_short + "\n\n"
            else:
                actual_prompt += conversation_context + "\n\n"
    else:
        # No summary available, use full context
        if conversation_context:
            actual_prompt += conversation_context + "\n\n"
    
    # Add user message to both prompts
    full_traditional_prompt += "User message:\n" + user_input
    actual_prompt += "User message:\n" + user_input
    
    # Log token usage for both approaches
    log_token_usage(full_traditional_prompt, "traditional")
    
    if summary:
        log_token_usage(actual_prompt, "summarized")
    
    # Log input tokens specifically (to server log only)
    input_tokens = log_token_usage(actual_prompt, "input")
    print(f"[SERVER LOG] Request tokens: {input_tokens}")
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=actual_prompt
        )
        
        # Log output tokens (to server log only)
        output_tokens = log_token_usage(response.text, "output")
        print(f"[SERVER LOG] Response tokens: {output_tokens}")
        
        return response.text
    except Exception as e:
        print(f"Error generating response: {str(e)}")
        return "Ой, щось мій мозок глючить... Давай ще раз спробуємо?"

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
            contents=summary_prompt
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

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    global message_batches
    
    # Check token usage regularly
    check_token_usage()
    
    data = request.get_json()
    
    # Debugging: Log the incoming webhook data
    print(f"Webhook received: {data}")
    
    # Get the update_id
    update_id = data.get('update_id')
    
    # Check if this is a message update
    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        user_id = data['message'].get('from', {}).get('id')
        username = data['message'].get('from', {}).get('username', '')
        user_input = data['message']['text']
        message_id = data['message'].get('message_id', 0)
        
        # Check if message is in a group
        is_group = data['message']['chat']['type'] in ['group', 'supergroup']
        
        # Check if this is a reply to another message
        reply_context = ""
        is_reply_to_bot = False
        
        if 'reply_to_message' in data['message'] and 'text' in data['message']['reply_to_message']:
            replied_username = data['message']['reply_to_message'].get('from', {}).get('username', 'Unknown')
            replied_text = data['message']['reply_to_message']['text']
            
            # Check if the replied message was from the bot
            if 'from' in data['message']['reply_to_message']:
                bot_id = data['message']['reply_to_message']['from'].get('username')
                is_reply_to_bot = bot_id and CONFIG['bot_name'].lower() in bot_id.lower()
            
            # Add reply context if enabled
            if group_settings.get("include_reply_context", True):
                # Format the replied message to add to the user input
                reply_context = f"[У відповідь на повідомлення від {replied_username}: \"{replied_text}\"] "
                # Modify user input to include context of the message being replied to
                user_input = reply_context + user_input
        
        # Update scheduled message activity tracking if enabled
        if scheduled_messenger:
            # Register this chat for potential scheduled messages
            chat_type = data['message']['chat']['type']
            scheduled_messenger.register_chat(chat_id, chat_type)
            # Update activity timestamp to prevent scheduled messages during active conversation
            scheduled_messenger.update_chat_activity(chat_id)
        
        # Add user message to context
        context_manager.add_message(chat_id, user_id, username, user_input, is_bot=False, is_group=is_group)
        
        # Check if bot should force respond to a reply
        should_force_respond = is_reply_to_bot and CONFIG["response_settings"].get("respond_to_replies", True)
        
        # Determine if bot should respond
        if (is_group and not CONFIG["response_settings"]["respond_in_groups"]):
            return jsonify({"status": "ok"})
        
        try:
            # Handle memory commands
            if user_input.startswith('/memory'):
                response = handle_memory_command(chat_id, user_input)
                send_message(chat_id, response)
                context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
                return jsonify({"status": "ok"})
            
            # Handle scheduled message commands
            if user_input.startswith('/schedule'):
                response = handle_schedule_command(chat_id, user_input)
                send_message(chat_id, response)
                context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
                return jsonify({"status": "ok"})
            
            # Check for predefined commands
            commands = CONFIG["response_settings"].get("commands", {})
            command_found = False
            
            for cmd, response in commands.items():
                if user_input.startswith(cmd):
                    send_message(chat_id, response)
                    # Add bot response to context
                    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response, is_bot=True, is_group=is_group)
                    command_found = True
                    break
            
            if command_found:
                return jsonify({"status": "ok"})
            
            # Check if message contains a keyword or if it's a forced response
            keyword_match = should_respond(user_input) or should_force_respond
            
            # Create batch key for chat+user combination
            batch_key = f"{chat_id}:{user_id}"
            current_time = time.time()
            
            # If this is a message with a keyword, check batching
            if keyword_match and MESSAGE_BATCH_TIMEOUT > 0:  # Only batch if enabled
                # Add to batch if there's an active batch for this user in this chat
                if batch_key in message_batches and current_time - message_batches[batch_key]['last_update'] < MESSAGE_BATCH_TIMEOUT:
                    # Add to existing batch
                    message_batches[batch_key]['messages'].append(user_input)
                    message_batches[batch_key]['message_ids'].append(message_id)
                    message_batches[batch_key]['last_update'] = current_time
                    # Return immediately to let more messages accumulate if they're coming
                    return jsonify({"status": "ok", "action": "batched"})
                else:
                    # Create new batch
                    message_batches[batch_key] = {
                        'messages': [user_input],
                        'message_ids': [message_id],
                        'username': username,
                        'is_group': is_group,
                        'created': current_time,
                        'last_update': current_time
                    }
                    
                    # Wait for potential additional messages
                    time.sleep(MESSAGE_BATCH_TIMEOUT)
                    
                    # Get all messages in batch
                    batched_messages = message_batches[batch_key]['messages']
                    
                    # Clean up the batch
                    del message_batches[batch_key]
                    
                    # Check if session already exists or create one for group chats
                    if is_group and group_settings.get("session_enabled", True):
                        if not context_manager.is_session_active(chat_id):
                            # Start a new session
                            context_manager.start_session(chat_id, user_id, username)
                        else:
                            # Update existing session
                            context_manager.update_session(chat_id, user_id, username)
                    
                    # If we have multiple messages, combine them for a single response
                    if len(batched_messages) > 1:
                        combined_input = "Користувач переслав кілька повідомлень:\n\n" + "\n".join([f"- {msg}" for msg in batched_messages])
                        response_text = generate_response(combined_input, chat_id)
                        
                        # Send typing action with message length for dynamic typing duration
                        send_typing_action(chat_id, response_text)
                        
                        send_message(chat_id, response_text)
                        
                        # Add bot response to context
                        context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
                        
                        return jsonify({"status": "ok", "action": "batch_processed"})
                    else:
                        # Single message processing continues with normal flow
                        user_input = batched_messages[0]
            
            # Group chat session handling
            if is_group and group_settings.get("session_enabled", True):
                # If message matches a keyword or is a reply to bot, start a new session or update existing one
                if keyword_match:
                    # Check if there's already an active session
                    if not context_manager.is_session_active(chat_id):
                        # Start a new session
                        context_manager.start_session(chat_id, user_id, username)
                    else:
                        # Update existing session
                        context_manager.update_session(chat_id, user_id, username)
                    
                    # Generate response
                    response_text = generate_response(user_input, chat_id)
                    
                    # Send typing action with message length for dynamic typing duration
                    send_typing_action(chat_id, response_text)
                    
                    send_message(chat_id, response_text)
                    
                    # Add bot response to context
                    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
                    
                    return jsonify({"status": "ok"})
                
                # Check if this is from a user in an active session
                elif context_manager.is_session_active(chat_id, user_id):
                    # Update session
                    context_manager.update_session(chat_id, user_id, username)
                    
                    # Check if this is a command to end the session
                    if is_session_end_command(user_input):
                        context_manager.end_session(chat_id)
                        response_text = "давай, пінганеш"
                        send_message(chat_id, response_text)
                        context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
                        return jsonify({"status": "ok"})
                    
                    # Auto reply to session participants if enabled
                    if group_settings.get("auto_reply_to_session_participants", True):
                        # Generate response
                        response_text = generate_response(user_input, chat_id)
                        
                        # Send typing action with message length for dynamic typing duration
                        send_typing_action(chat_id, response_text)
                        
                        send_message(chat_id, response_text)
                        
                        # Add bot response to context
                        context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
                        
                        return jsonify({"status": "ok"})
                
                # If auto join is enabled, add user to session if others are talking to the bot
                elif group_settings.get("auto_join_session", True) and context_manager.is_session_active(chat_id):
                    # Add user to session
                    context_manager.update_session(chat_id, user_id, username)
                    
                    # We don't respond here, just added them to the session
                    return jsonify({"status": "ok"})
            
            # If not a group chat or sessions disabled, check if should respond
            if not is_group or not group_settings.get("session_enabled", True):
                if keyword_match:
                    # Generate response
                    response_text = generate_response(user_input, chat_id)
                    
                    # Send typing action with message length for dynamic typing duration
                    send_typing_action(chat_id, response_text)
                    
                    send_message(chat_id, response_text)
                    
                    # Add bot response to context
                    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
            
            # Process any pending impressions (max 2 per request to avoid timeouts)
            try:
                process_pending_impressions(max_to_process=2)
            except Exception as e:
                print(f"Error processing impressions: {str(e)}")
        
        except Exception as e:
            send_message(chat_id, f"Ой, щось поламалось(( Тех.підтримка вже розбирається: {str(e)}")
    
    return jsonify({"status": "ok"})

@app.route('/')
def index():
    """Simple health check endpoint"""
    return "Bot is running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))) 