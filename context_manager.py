import json
import os
import re
from datetime import datetime, timedelta
from collections import defaultdict

class ContextManager:
    """
    Manages conversation context and long-term memory for the chatbot
    """
    def __init__(self, max_messages=250, memory_file="memory.json", session_timeout_seconds=300):
        self.max_messages = max_messages
        self.memory_file = memory_file
        self.conversations = defaultdict(list)
        self.memory = self._load_memory()
        # Зберігання активних сесій розмови в групових чатах
        self.active_sessions = {}
        # Час в секундах, після якого сесія розмови в групі вважається закінченою
        self.session_timeout = session_timeout_seconds
        # Додатковий словник для зберігання персоналізованого вводження про користувачів
        self.user_impressions = {}
        # Останнє оновлення вражень про користувачів (останні 250 повідомлень)
        self.last_impression_update = {}
    
    def _load_memory(self):
        """Load memory from file if it exists"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading memory: {str(e)}")
        return {}
    
    def _save_memory(self):
        """Save memory to file"""
        try:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving memory: {str(e)}")
    
    def add_message(self, chat_id, user_id, username, message, is_bot=False, is_group=False):
        """Add a message to the conversation context"""
        chat_id_str = str(chat_id)  # Convert to string to ensure compatibility as dict key
        
        # Create message entry
        message_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "username": username,
            "content": message,
            "is_bot": is_bot
        }
        
        # Add to conversation history
        self.conversations[chat_id_str].append(message_entry)
        
        # Trim conversation if needed
        if len(self.conversations[chat_id_str]) > self.max_messages:
            self.conversations[chat_id_str] = self.conversations[chat_id_str][-self.max_messages:]
        
        # Update memory for this chat
        self._update_memory(chat_id_str)
        
        # Auto-detect and save important information
        if not is_bot:
            self._auto_detect_important_info(chat_id_str, message)
            
            # Check if we have enough messages from this user to generate/update an impression
            self._maybe_update_user_impression(chat_id_str, user_id, username)
        
        return message_entry
    
    def is_session_active(self, chat_id, user_id=None):
        """
        Перевіряє, чи є активна сесія розмови в поточному чаті.
        Якщо user_id вказано, перевіряє чи бере користувач участь в активній сесії.
        """
        chat_id_str = str(chat_id)
        
        # Якщо сесії немає, то вона не активна
        if chat_id_str not in self.active_sessions:
            return False
        
        session = self.active_sessions[chat_id_str]
        
        # Перевіряємо час останньої активності
        last_activity = datetime.fromisoformat(session["last_activity"])
        if datetime.now() - last_activity > timedelta(seconds=self.session_timeout):
            # Сесія закінчилась через timeout
            del self.active_sessions[chat_id_str]
            return False
        
        # Якщо user_id вказано, перевіряємо чи є користувач в учасниках сесії
        if user_id is not None:
            return str(user_id) in session["participants"]
        
        # Сесія активна
        return True
    
    def start_session(self, chat_id, user_id, username):
        """
        Починає нову сесію розмови в груповому чаті.
        """
        chat_id_str = str(chat_id)
        user_id_str = str(user_id)
        
        self.active_sessions[chat_id_str] = {
            "last_activity": datetime.now().isoformat(),
            "participants": {user_id_str: username},
            "starter": user_id_str
        }
        
        return True
    
    def update_session(self, chat_id, user_id, username):
        """
        Оновлює активність в сесії та додає користувача як учасника.
        """
        if not self.is_session_active(chat_id):
            return False
        
        chat_id_str = str(chat_id)
        user_id_str = str(user_id)
        
        # Оновлюємо час останньої активності
        self.active_sessions[chat_id_str]["last_activity"] = datetime.now().isoformat()
        
        # Додаємо користувача до учасників, якщо він ще не в списку
        self.active_sessions[chat_id_str]["participants"][user_id_str] = username
        
        return True
    
    def end_session(self, chat_id):
        """
        Примусово завершує сесію розмови.
        """
        chat_id_str = str(chat_id)
        if chat_id_str in self.active_sessions:
            del self.active_sessions[chat_id_str]
            return True
        return False
    
    def _update_memory(self, chat_id):
        """Extract important information from conversations to update memory"""
        if chat_id not in self.memory:
            self.memory[chat_id] = {
                "user_info": {},
                "topics_discussed": [],
                "important_facts": [],
                "user_impressions": {},
                "last_interaction": None
            }
        
        # Update the last interaction time
        self.memory[chat_id]["last_interaction"] = datetime.now().isoformat()
        
        # Make sure user_impressions exists in memory
        if "user_impressions" not in self.memory[chat_id]:
            self.memory[chat_id]["user_impressions"] = {}
            
        # Save memory to file
        self._save_memory()
    
    def _auto_detect_important_info(self, chat_id, message):
        """
        Automatically detect important information from a message
        This is a simple rule-based detection that can be improved with ML models
        """
        # Initialize patterns for different types of information
        # Personal information patterns (name, age, location, etc.)
        personal_info_patterns = [
            # Name patterns
            (r"(?:мене|я) (?:звати|звуть|кличуть|називають|називаюсь) (\w+)", "ім'я"),
            (r"(?:моє|мене) (?:ім'я|звати|звуть) (\w+)", "ім'я"),
            # Age patterns
            (r"(?:мені|я маю|мій вік) (\d+) (?:рок[иіу]в|літ|років)", "вік"),
            # Location patterns
            (r"(?:я|ми) (?:живу|живемо|мешкаю|з) (?:в|у) ([\w\s]+)", "місце"),
            # Interest patterns
            (r"(?:я|мені) (?:подобається|люблю|обожнюю) ([\w\s]+)", "інтереси"),
            (r"(?:моє|мені) (?:хобі|захоплення) (?:це|—|-)? ?([\w\s]+)", "хобі")
        ]
        
        # Topic detection patterns
        topic_patterns = [
            r"(?:давай|можемо|хочу|цікавить|поговоримо|розкажи) про ([\w\s]+)",
            r"(?:мене цікавить тема|тема|питання щодо) ([\w\s]+)",
            r"(?:що ти думаєш про|як щодо|твоя думка про) ([\w\s]+)"
        ]
        
        # Important fact patterns
        fact_patterns = [
            r"(?:важливо|запам'ятай|не забудь|нагадую|важливий факт)[,:] (.*)",
            r"(?:запам'ятай|збережи|занотуй)[,:] (.*)",
            r"(?:я хочу щоб ти знала|тобі варто знати)[,:] (.*)"
        ]
        
        # Check for personal information
        for pattern, info_type in personal_info_patterns:
            matches = re.search(pattern, message.lower())
            if matches:
                value = matches.group(1).strip()
                if value and len(value) > 1:  # Ensure we have meaningful content
                    self.add_to_memory(chat_id, "user_info", {info_type: value})
        
        # Check for topics
        for pattern in topic_patterns:
            matches = re.search(pattern, message.lower())
            if matches:
                topic = matches.group(1).strip()
                if topic and len(topic) > 2:  # Ensure we have meaningful content
                    self.add_to_memory(chat_id, "topics_discussed", topic)
        
        # Check for important facts
        for pattern in fact_patterns:
            matches = re.search(pattern, message)
            if matches:
                fact = matches.group(1).strip()
                if fact and len(fact) > 3:  # Ensure we have meaningful content
                    self.add_to_memory(chat_id, "important_facts", fact)
    
    def get_conversation_context(self, chat_id):
        """Get formatted conversation history for the given chat"""
        chat_id_str = str(chat_id)
        
        if chat_id_str not in self.conversations or not self.conversations[chat_id_str]:
            return ""
        
        formatted_context = "Previous conversation:\n\n"
        for msg in self.conversations[chat_id_str]:
            speaker = "Bot" if msg["is_bot"] else f"User ({msg['username'] if msg['username'] else 'Unknown'})"
            formatted_context += f"{speaker}: {msg['content']}\n"
        
        return formatted_context
    
    def get_memory(self, chat_id):
        """Get memory for a specific chat"""
        chat_id_str = str(chat_id)
        return self.memory.get(chat_id_str, {})
    
    def add_to_memory(self, chat_id, category, value):
        """Manually add an important fact to memory"""
        chat_id_str = str(chat_id)
        
        if chat_id_str not in self.memory:
            self.memory[chat_id_str] = {
                "user_info": {},
                "topics_discussed": [],
                "important_facts": [],
                "user_impressions": {},
                "last_interaction": datetime.now().isoformat()
            }
        
        if category == "user_info":
            self.memory[chat_id_str]["user_info"].update(value)
        elif category == "topics_discussed":
            if value not in self.memory[chat_id_str]["topics_discussed"]:
                self.memory[chat_id_str]["topics_discussed"].append(value)
        elif category == "important_facts":
            if value not in self.memory[chat_id_str]["important_facts"]:
                self.memory[chat_id_str]["important_facts"].append(value)
        
        self._save_memory()
    
    def _maybe_update_user_impression(self, chat_id, user_id, username):
        """
        Check if we should generate or update an impression about a specific user
        based on their recent messages
        """
        if not user_id:  # Skip if no user ID (e.g., for bot messages)
            return
            
        chat_id_str = str(chat_id)
        user_id_str = str(user_id)
        
        # Get all messages from this user in this chat
        user_messages = [
            msg for msg in self.conversations[chat_id_str] 
            if not msg["is_bot"] and str(msg.get("user_id", "")) == user_id_str
        ]
        
        # If fewer than 10 messages, not enough to form an impression yet
        if len(user_messages) < 10:
            return
            
        # Check if we've already generated an impression recently
        user_key = f"{chat_id_str}:{user_id_str}"
        last_update = self.last_impression_update.get(user_key, None)
        
        # Generate a new impression if:
        # 1. We've never generated one before, or
        # 2. We have at least 30 new messages since the last update
        if (last_update is None or 
            len(user_messages) - last_update >= 30):
            
            self._generate_user_impression(chat_id_str, user_id_str, username, user_messages)
            # Update the counter for when we last generated an impression
            self.last_impression_update[user_key] = len(user_messages)
    
    def _generate_user_impression(self, chat_id, user_id, username, messages):
        """
        Generate a personality-infused impression about a user based on their messages
        and save it to memory
        """
        # Get the existing impression if any
        existing_impression = self.memory[chat_id].get("user_impressions", {}).get(user_id, "")
        
        # Prepare data to generate the impression
        message_texts = [msg["content"] for msg in messages[-50:]]  # Use most recent 50 messages max
        message_sample = "\n".join(message_texts)
        
        # Get message count
        message_count = len(messages)
        
        # Build a prompt that will be used later with Gemini API
        # We're just storing the data here for now, the actual generation
        # will happen when needed via the main.py
        impression_data = {
            "username": username,
            "message_count": message_count,
            "sample": message_sample,
            "existing_impression": existing_impression,
            "needs_generation": True,
            "last_updated": datetime.now().isoformat()
        }
        
        # Store the data for later processing
        self.user_impressions[f"{chat_id}:{user_id}"] = impression_data
    
    def get_user_impression_data(self, chat_id, user_id):
        """
        Get the data needed to generate an impression for a specific user
        Returns None if there's no data or impression needed
        """
        user_key = f"{chat_id}:{user_id}"
        return self.user_impressions.get(user_key, None)
    
    def save_generated_impression(self, chat_id, user_id, impression):
        """
        Save a generated impression to memory
        """
        chat_id_str = str(chat_id)
        user_id_str = str(user_id)
        
        # Ensure we have a user_impressions dictionary in memory
        if "user_impressions" not in self.memory[chat_id_str]:
            self.memory[chat_id_str]["user_impressions"] = {}
            
        # Save the impression
        self.memory[chat_id_str]["user_impressions"][user_id_str] = impression
        
        # Mark as no longer needing generation
        user_key = f"{chat_id_str}:{user_id_str}"
        if user_key in self.user_impressions:
            self.user_impressions[user_key]["needs_generation"] = False
            
        # Save to disk
        self._save_memory()
        
    def get_user_impressions(self, chat_id):
        """
        Get all stored impressions for users in a chat
        """
        chat_id_str = str(chat_id)
        return self.memory.get(chat_id_str, {}).get("user_impressions", {})
        
    def get_users_needing_impressions(self):
        """
        Return a list of chat_id, user_id pairs that need impression generation
        """
        needs_impression = []
        
        for user_key, data in self.user_impressions.items():
            if data.get("needs_generation", False):
                chat_id, user_id = user_key.split(":")
                needs_impression.append((chat_id, user_id))
                
        return needs_impression 