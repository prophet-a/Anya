import os
import json
import re
import requests
from flask import Flask, request, jsonify
from google import genai
from personality import PERSONALITY

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
            "response_settings": {"respond_to_direct_messages": True, "respond_in_groups": True}
        }

CONFIG = load_config()

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
    requests.post(url, json=payload)

def generate_response(user_input):
    """Generate response using Gemini API"""
    prompt = PERSONALITY + "\n\nUser message:\n" + user_input
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"Error generating response: {str(e)}")
        return "Вибачте, виникла помилка при генерації відповіді."

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

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    data = request.get_json()
    
    # Check if this is a message update
    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        user_input = data['message']['text']
        
        # Check if message is in a group
        is_group = data['message']['chat']['type'] in ['group', 'supergroup']
        
        # Determine if bot should respond
        if (is_group and not CONFIG["response_settings"]["respond_in_groups"]):
            return jsonify({"status": "ok"})
        
        try:
            # Check for predefined commands
            commands = CONFIG["response_settings"].get("commands", {})
            command_found = False
            
            for cmd, response in commands.items():
                if user_input.startswith(cmd):
                    send_message(chat_id, response)
                    command_found = True
                    break
            
            # If not a command, check if should respond using keyword detection
            if not command_found and should_respond(user_input):
                # Generate and send response
                response_text = generate_response(user_input)
                
                # Add a small delay if configured
                delay = CONFIG["response_settings"].get("response_delay_seconds", 0)
                if delay > 0:
                    import time
                    time.sleep(delay)
                
                send_message(chat_id, response_text)
        except Exception as e:
            send_message(chat_id, f"Вибачте, виникла помилка: {str(e)}")
    
    return jsonify({"status": "ok"})

@app.route('/')
def index():
    """Simple health check endpoint"""
    return "Bot is running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))) 