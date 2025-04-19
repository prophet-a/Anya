import json
import random
import time
import threading
import os
from datetime import datetime, timedelta
import requests
from google import genai
# from google.api_core.client_options import HttpOptions
from personality import PERSONALITY

class ScheduledMessenger:
    """
    Handles sending periodic messages from the bot based on personality and memory
    """
    def __init__(self, telegram_token, gemini_api_key, memory_file="memory.json", config_file="config.json"):
        self.telegram_token = telegram_token
        self.memory_file = memory_file
        self.config_file = config_file
        self.chats_to_message = {}  # Will store chat_ids with timestamps of last activity
        self.last_sent_times = {}  # When messages were last sent to each chat
        self.random_messages = [
            "шо тут було, поки мене не було?",
            "хто сьогодні дивився новини? я от не можу вже",
            "курс як там? ростe?",
            "мені здається, чи міський голуб це просто невдалий дрон",
            "до речі, хто дивився останній сезон?",
            "чи ми хоч раз бачили немовля голуба? думайте самі",
            "шо за день сьогодні... сидиш, чілиш",
            "може є якісь пропозиції, що подивитись",
            "шо робите такого?",
            "чуєш, а люди дійсно такі чи я одна помічаю",
            "написала і стерла... ноо",
            "як думаєте, вода пам'ятає",
            "а, чекайте, ше ж ніхто не проснувся)",
            "якби ви були деревом, яким би були",
            "думаю, головне вчасно перестати читати коменти",
            "сиджу думаю, а може то все змова...",
            "от звідки ми знаємо, що ми не в матриці",
            "іноді я думаю, шо в інших чатах теж сидять не люди",
            "гегаловська діалектика каже, що...",
            "сьогодні буде щось цікаве",
            "мені тут подзвонили. сказали дивні речі...",
            "схоже, ми всі забули про одну стару штуку",
            "часом відчуваю, ніби нас слухають...",
            "дві години ночі, а я сиджу думаю про...кхм, нічо"
        ]
        
        # Initialize Gemini
        self.client = genai.Client(
            api_key=gemini_api_key,
            # http_options=HttpOptions(api_version=\"v1\")
        )
        
        # Load config
        self.config = self._load_config()
        
        # Scheduling settings
        self.min_hours_between_messages = 4  # Minimum hours between messages
        self.max_hours_between_messages = 12  # Maximum hours between messages
        self.max_messages_per_day = 3  # Maximum messages per day
        self.active_session_cooldown_minutes = 30  # How long to wait after active conversation
    
    def _load_config(self):
        """Load configuration from file"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {str(e)}")
            return {}
    
    def _load_memory(self):
        """Load memory from file"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading memory: {str(e)}")
        return {}
    
    def register_chat(self, chat_id, chat_type="private"):
        """Register a chat to receive scheduled messages"""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.chats_to_message:
            self.chats_to_message[chat_id_str] = {
                "last_activity": datetime.now().isoformat(),
                "chat_type": chat_type,
                "active": True
            }
    
    def update_chat_activity(self, chat_id):
        """Update the last activity time for a chat"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.chats_to_message:
            self.chats_to_message[chat_id_str]["last_activity"] = datetime.now().isoformat()
    
    def should_send_message(self, chat_id):
        """Determine if it's appropriate to send a message to this chat now"""
        chat_id_str = str(chat_id)
        
        if chat_id_str not in self.chats_to_message:
            return False
        
        now = datetime.now()
        
        # Check if chat had recent activity
        last_activity = datetime.fromisoformat(self.chats_to_message[chat_id_str]["last_activity"])
        if now - last_activity < timedelta(minutes=self.active_session_cooldown_minutes):
            # Don't send if there was recent activity (active conversation)
            return False
            
        # Check if we've sent a message recently
        if chat_id_str in self.last_sent_times:
            last_sent = self.last_sent_times[chat_id_str]
            min_next_time = last_sent + timedelta(hours=self.min_hours_between_messages)
            if now < min_next_time:
                # Too soon since last message
                return False
                
            # Check if we've reached the daily limit
            day_start = datetime(now.year, now.month, now.day, 0, 0, 0)
            count_today = sum(1 for t in self.last_sent_times.values() 
                             if t >= day_start and t <= now)
            if count_today >= self.max_messages_per_day:
                return False
        
        # Random chance based on time elapsed since min time
        # This creates random natural timing
        if chat_id_str in self.last_sent_times:
            last_sent = self.last_sent_times[chat_id_str]
            hours_since_min = (now - (last_sent + timedelta(hours=self.min_hours_between_messages))).total_seconds() / 3600
            
            if hours_since_min <= 0:
                return False
                
            # Calculate probability that increases with time
            max_delay = self.max_hours_between_messages - self.min_hours_between_messages
            probability = min(hours_since_min / max_delay, 1.0) * 0.3  # 30% max chance per check
            
            return random.random() < probability
        else:
            # First message has 20% chance on first check
            return random.random() < 0.2
    
    def _get_memory_context(self, chat_id):
        """Get memory context for a specific chat"""
        memory = self._load_memory().get(str(chat_id), {})
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
                
        return memory_context
    
    def generate_random_message(self, chat_id):
        """Generate a personalized random message based on bot's personality and memory"""
        # Always use AI to generate messages
        memory_context = self._get_memory_context(chat_id)
        
        # Build prompt for a proactive message
        prompt = PERSONALITY + "\n\n"
        
        if memory_context:
            prompt += memory_context + "\n\n"
        
        prompt += """
        Generate a short, casual message to send unprompted to the chat. 
        This should be something you might say to start a conversation or share a random thought.
        Keep it short, casual, and aligned with your personality. 
        Say something interesting, mysterious, or thought-provoking that might spark a conversation.
        Do not ask how the person is doing or use generic greetings.
        """
        
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash-preview-04-17",
                contents=prompt,
            )
            message = response.text.strip()
            
            # Clean up the message - remove quotes if present
            if message.startswith('"') and message.endswith('"'):
                message = message[1:-1]
                
            return message
        except Exception as e:
            print(f"Error generating random message: {str(e)}")
            # Fallback to random messages only in case of an error
            return random.choice(self.random_messages)
    
    def send_message(self, chat_id, text):
        """Send message to Telegram chat"""
        # First send typing action to show "Анна печатает..."
        typing_url = f"https://api.telegram.org/bot{self.telegram_token}/sendChatAction"
        typing_payload = {
            "chat_id": chat_id,
            "action": "typing"
        }
        try:
            requests.post(typing_url, json=typing_payload)
            
            # Calculate typing time based on message length
            # 30ms per character with min/max bounds
            typing_seconds = min(max(len(text) * 0.03, 1), 7)
            time.sleep(typing_seconds)
        except Exception as e:
            print(f"Error sending typing action: {str(e)}")
        
        # Now send the actual message
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(url, json=payload)
            result = response.json()
            
            if result.get("ok"):
                # Update last sent time
                self.last_sent_times[str(chat_id)] = datetime.now()
                return True
            else:
                print(f"Failed to send message: {result}")
                return False
        except Exception as e:
            print(f"Error sending message: {str(e)}")
            return False
    
    def update_active_chats(self):
        """Update which chats should receive messages based on activity"""
        memory = self._load_memory()
        
        for chat_id, chat_data in memory.items():
            # Check if chat has had an interaction in the past 30 days
            if "last_interaction" in chat_data:
                try:
                    last_interaction = datetime.fromisoformat(chat_data["last_interaction"])
                    if datetime.now() - last_interaction <= timedelta(days=30):
                        # Chat is active enough to receive messages
                        self.register_chat(chat_id)
                except Exception as e:
                    print(f"Error parsing date for chat {chat_id}: {str(e)}")
    
    def check_and_send_scheduled_messages(self):
        """Check if it's time to send a message to any chat and send if appropriate"""
        for chat_id in list(self.chats_to_message.keys()):
            if self.should_send_message(chat_id):
                message = self.generate_random_message(chat_id)
                success = self.send_message(chat_id, message)
                if success:
                    print(f"Sent scheduled message to {chat_id}: {message}")
    
    def start_scheduler(self, check_interval_minutes=15):
        """Start the scheduler thread to periodically check and send messages"""
        def scheduler_loop():
            while True:
                try:
                    # Refresh list of active chats
                    self.update_active_chats()
                    
                    # Check and send messages
                    self.check_and_send_scheduled_messages()
                    
                    # Sleep until next check
                    time.sleep(check_interval_minutes * 60)
                except Exception as e:
                    print(f"Error in scheduler loop: {str(e)}")
                    time.sleep(60)  # Sleep a minute on error
        
        scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        scheduler_thread.start()
        return scheduler_thread