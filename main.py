import os
import json
import re
import requests
from flask import Flask, request, jsonify
from google import genai
from personality import PERSONALITY
from context_manager import ContextManager
from scheduled_messages import ScheduledMessenger

app = Flask(__name__)

# Configure API keys
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Load configuration
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
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
    memory_file=context_settings.get("memory_file", "memory.json"),
    session_timeout_seconds=group_settings.get("session_timeout_seconds", 300)
)

# Initialize scheduled messenger if enabled
scheduled_messages_config = CONFIG.get("scheduled_messages", {})
if scheduled_messages_config.get("enabled", False):
    scheduled_messenger = ScheduledMessenger(
        telegram_token=TELEGRAM_BOT_TOKEN,
        gemini_api_key=GEMINI_API_KEY,
        memory_file=context_settings.get("memory_file", "memory.json"),
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

def generate_response(user_input, chat_id):
    """Generate response using Gemini API with context"""
    # Get context and memory
    conversation_context = ""
    memory_context = ""
    
    if context_settings.get("enabled", True):
        conversation_context = context_manager.get_conversation_context(chat_id)
    
    if context_settings.get("memory_enabled", True):
        memory = context_manager.get_memory(chat_id)
        if memory:
            user_info = memory.get("user_info", {})
            topics = memory.get("topics_discussed", [])
            facts = memory.get("important_facts", [])
            
            memory_context = "Important information from memory:\n"
            
            if user_info:
                memory_context += "User information:\n"
                for key, value in user_info.items():
                    memory_context += f"- {key}: {value}\n"
            
            if topics:
                memory_context += "\nTopics previously discussed:\n"
                for topic in topics:
                    memory_context += f"- {topic}\n"
            
            if facts:
                memory_context += "\nImportant facts to remember:\n"
                for fact in facts:
                    memory_context += f"- {fact}\n"
    
    # Build complete prompt
    prompt = PERSONALITY + "\n\n"
    
    if memory_context:
        prompt += memory_context + "\n\n"
    
    if conversation_context:
        prompt += conversation_context + "\n\n"
    
    prompt += "User message:\n" + user_input
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
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
        return "Як юзати: /memory <дія> [дані]\nМожеш обрати: info, add, clear"
    
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
        return f"❌ Шо за '{action}'? Не знаю такого. Спробуй info, add або clear"

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

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    data = request.get_json()
    
    # Check if this is a message update
    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        user_id = data['message'].get('from', {}).get('id')
        username = data['message'].get('from', {}).get('username', '')
        user_input = data['message']['text']
        
        # Check if message is in a group
        is_group = data['message']['chat']['type'] in ['group', 'supergroup']
        
        # Update scheduled message activity tracking if enabled
        if scheduled_messenger:
            # Register this chat for potential scheduled messages
            chat_type = data['message']['chat']['type']
            scheduled_messenger.register_chat(chat_id, chat_type)
            # Update activity timestamp to prevent scheduled messages during active conversation
            scheduled_messenger.update_chat_activity(chat_id)
        
        # Add user message to context
        context_manager.add_message(chat_id, user_id, username, user_input, is_bot=False, is_group=is_group)
        
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
            
            # Group chat session handling
            if is_group and group_settings.get("session_enabled", True):
                # Check if this is a message that should trigger a response (contains keyword)
                keyword_match = should_respond(user_input)
                
                # If message matches a keyword, start a new session or update existing one
                if keyword_match:
                    # Check if there's already an active session
                    if not context_manager.is_session_active(chat_id):
                        # Start a new session
                        context_manager.start_session(chat_id, user_id, username)
                    else:
                        # Update existing session
                        context_manager.update_session(chat_id, user_id, username)
                    
                    # Generate and send response
                    response_text = generate_response(user_input, chat_id)
                    
                    # Add a small delay if configured
                    delay = CONFIG["response_settings"].get("response_delay_seconds", 0)
                    if delay > 0:
                        import time
                        time.sleep(delay)
                    
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
                        response_text = "Ну все, наговорились) Бувай-бувай 👋 Якшо шо — пінгуй"
                        send_message(chat_id, response_text)
                        context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
                        return jsonify({"status": "ok"})
                    
                    # Auto reply to session participants if enabled
                    if group_settings.get("auto_reply_to_session_participants", True):
                        # Generate and send response
                        response_text = generate_response(user_input, chat_id)
                        
                        # Add a small delay if configured
                        delay = CONFIG["response_settings"].get("response_delay_seconds", 0)
                        if delay > 0:
                            import time
                            time.sleep(delay)
                        
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
                if should_respond(user_input):
                    # Generate and send response
                    response_text = generate_response(user_input, chat_id)
                    
                    # Add a small delay if configured
                    delay = CONFIG["response_settings"].get("response_delay_seconds", 0)
                    if delay > 0:
                        import time
                        time.sleep(delay)
                    
                    send_message(chat_id, response_text)
                    
                    # Add bot response to context
                    context_manager.add_message(chat_id, None, CONFIG["bot_name"], response_text, is_bot=True, is_group=is_group)
        
        except Exception as e:
            send_message(chat_id, f"Ой, щось поламалось(( Тех.підтримка вже розбирається: {str(e)}")
    
    return jsonify({"status": "ok"})

@app.route('/')
def index():
    """Simple health check endpoint"""
    return "Bot is running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))) 