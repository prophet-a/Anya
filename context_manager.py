import json
import os
import re
from datetime import datetime, timedelta
from collections import defaultdict

class ContextManager:
    """
    Manages conversation context and long-term memory for the chatbot
    """
    def __init__(self, max_messages=200, memory_file="memory.json", session_timeout_seconds=300):
        self.max_messages = max_messages
        self.memory_file = memory_file
        self.conversations = defaultdict(list)
        self.memory = self._load_memory()
        # Зберігання активних сесій розмови в групових чатах
        self.active_sessions = {}
        # Час в секундах, після якого сесія розмови в групі вважається закінченою
        self.session_timeout = session_timeout_seconds
    
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
                "last_interaction": None
            }
        
        # Update the last interaction time
        self.memory[chat_id]["last_interaction"] = datetime.now().isoformat()
        
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