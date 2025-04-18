import os
import json
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai
from personality import PERSONALITY

app = Flask(__name__)

# Configure API keys
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

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
    response = model.generate_content(prompt)
    return response.text

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    data = request.get_json()
    
    # Check if this is a message update
    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        user_input = data['message']['text']
        
        try:
            # Generate and send response
            response_text = generate_response(user_input)
            send_message(chat_id, response_text)
        except Exception as e:
            send_message(chat_id, f"Sorry, I encountered an error: {str(e)}")
    
    return jsonify({"status": "ok"})

@app.route('/')
def index():
    """Simple health check endpoint"""
    return "Bot is running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))) 