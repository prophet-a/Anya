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
        # Use /memory directory for storage
        self.memory_dir = "/memory"
        self.memory_file = os.path.join(self.memory_dir, memory_file)
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
        # Flag to track if memory needs saving
        self._dirty = False
        # Track if the memory has been loaded successfully
        self._memory_loaded = os.path.exists(self.memory_file)
    
    def _load_memory(self):
        """Load memory from file if it exists"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                    # Restore relevant parts if they exist in the file
                    self.active_sessions = loaded_data.get("active_sessions", {})
                    self.user_impressions = loaded_data.get("user_impressions_data", {})
                    self.last_impression_update = loaded_data.get("last_impression_update", {})
                    print(f"[ContextManager] Successfully loaded memory from {self.memory_file}")
                    return loaded_data.get("memory", {})
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from memory file {self.memory_file}: {str(e)}. Starting with empty memory.")
            except Exception as e:
                print(f"Error loading memory from {self.memory_file}: {str(e)}. Starting with empty memory.")
        else:
            print(f"[ContextManager] Memory file {self.memory_file} not found. Starting with empty memory.")
        # Return default empty structure if loading fails or file doesn't exist
        return {}
    
    def save_memory_if_dirty(self):
        """Save memory to file only if changes have been made"""
        if not self._dirty:
            return False
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)

            # Prepare data to save - include memory and other relevant state
            data_to_save = {
                "memory": self.memory,
                "active_sessions": self.active_sessions,
                "user_impressions_data": self.user_impressions,
                "last_impression_update": self.last_impression_update,
                "last_saved": datetime.now().isoformat()
            }

            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            # Reset dirty flag after successful save
            self._dirty = False
            print(f"[ContextManager] Memory saved to {self.memory_file}")
            return True
        except Exception as e:
            print(f"Error saving memory to {self.memory_file}: {str(e)}")
            return False
    
    # Keep internal _save_memory for explicit calls if needed elsewhere,
    # but it's primarily replaced by save_memory_if_dirty for background tasks
    def _save_memory(self):
        """Internal method to force save memory, used cautiously."""
        self._dirty = True # Ensure it saves if called directly
        return self.save_memory_if_dirty()
    
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
        
        # Update memory structures for this chat
        self._update_memory(chat_id_str) # This now only updates in-memory dicts and sets _dirty flag

        # Auto-detect and save important information
        if not is_bot:
            self._auto_detect_important_info(chat_id_str, user_id, message) # Pass user_id here
            
            # Check if we have enough messages from this user to generate/update an impression
            self._maybe_update_user_impression(chat_id_str, user_id, username)
        
        # Mark memory as dirty - saving will happen later periodically
        self._dirty = True
        
        return message_entry
    
    def is_group_chat(self, chat_id):
        """Checks if a given chat_id corresponds to a group chat based on stored history"""
        # This is an approximation. A better way would be to store chat type when first seen.
        # Let's assume if we have multiple non-bot user IDs, it's likely a group.
        chat_id_str = str(chat_id)
        user_ids = set()
        for msg in self.conversations.get(chat_id_str, []):
            if not msg.get("is_bot") and msg.get("user_id"):
                user_ids.add(msg.get("user_id"))
        return len(user_ids) > 1
        
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
        self._dirty = True # Session access updates activity, mark dirty
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
        
        self._dirty = True # Mark memory as dirty
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
        
        self._dirty = True # Mark memory as dirty
        return True
    
    def end_session(self, chat_id):
        """
        Примусово завершує сесію розмови.
        """
        chat_id_str = str(chat_id)
        if chat_id_str in self.active_sessions:
            del self.active_sessions[chat_id_str]
            self._dirty = True # Mark memory as dirty
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
            
        self._dirty = True # Mark memory as dirty since last_interaction changed
    
    def _auto_detect_important_info(self, chat_id, user_id, message):
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
        
        # Pass user_id to add_to_memory if needed later
        # Check for personal information
        for pattern, info_type in personal_info_patterns:
            matches = re.search(pattern, message.lower())
            if matches:
                value = matches.group(1).strip()
                if value and len(value) > 1:  # Ensure we have meaningful content
                    # Add info associated with the user if possible
                    self.add_to_memory(chat_id, "user_info", {info_type: value}, user_id=user_id)
                    self._dirty = True
        
        # Check for topics
        for pattern in topic_patterns:
            matches = re.search(pattern, message.lower())
            if matches:
                topic = matches.group(1).strip()
                if topic and len(topic) > 2:  # Ensure we have meaningful content
                    self.add_to_memory(chat_id, "topics_discussed", topic)
                    self._dirty = True
        
        # Check for important facts
        for pattern in fact_patterns:
            matches = re.search(pattern, message)
            if matches:
                fact = matches.group(1).strip()
                if fact and len(fact) > 3:  # Ensure we have meaningful content
                    self.add_to_memory(chat_id, "important_facts", fact)
                    self._dirty = True # Mark memory as dirty
    
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
    
    def add_to_memory(self, chat_id, category, value, user_id=None):
        """Manually add an important fact to memory"""
        chat_id_str = str(chat_id)
        user_id_str = str(user_id) if user_id else None

        if chat_id_str not in self.memory:
            self.memory[chat_id_str] = {
                "user_info": {},
                "topics_discussed": [],
                "important_facts": [],
                "user_impressions": {}, # Ensure this exists
                "users": {} # Store user-specific info here
            }

        # Initialize user-specific sub-dict if category is user_info and user_id is present
        if category == "user_info" and user_id_str:
            if "users" not in self.memory[chat_id_str]:
                self.memory[chat_id_str]["users"] = {}
            if user_id_str not in self.memory[chat_id_str]["users"]:
                 self.memory[chat_id_str]["users"][user_id_str] = {"user_info": {}}
            if "user_info" not in self.memory[chat_id_str]["users"][user_id_str]:
                 self.memory[chat_id_str]["users"][user_id_str]["user_info"] = {}
            self.memory[chat_id_str]["users"][user_id_str]["user_info"].update(value)
            self._dirty = True
        elif category == "topics_discussed":
            if value not in self.memory[chat_id_str]["topics_discussed"]:
                self.memory[chat_id_str]["topics_discussed"].append(value)
                self._dirty = True # Mark memory as dirty
        elif category == "important_facts":
            if value not in self.memory[chat_id_str]["important_facts"]:
                self.memory[chat_id_str]["important_facts"].append(value)
                self._dirty = True # Mark memory as dirty
        
        # Update last interaction time whenever memory is added
        self.memory[chat_id_str]["last_interaction"] = datetime.now().isoformat()
        self._dirty = True # Also mark dirty for last_interaction update
    
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
            self._dirty = True # Mark dirty as impression state changed
    
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
        self._dirty = True # Mark dirty as impression data added/updated
    
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
        
        # Ensure the base memory structure for the chat exists
        if chat_id_str not in self.memory:
            self._update_memory(chat_id_str) # Initialize memory structure if needed

        # Ensure we have a user_impressions dictionary in memory for this chat
        if "user_impressions" not in self.memory[chat_id_str]:
            self.memory[chat_id_str]["user_impressions"] = {}
            self._dirty = True # Mark dirty if we had to create the dict
            
        # Save the impression
        if self.memory[chat_id_str]["user_impressions"].get(user_id_str) != impression:
            self.memory[chat_id_str]["user_impressions"][user_id_str] = impression
            self._dirty = True # Mark dirty only if impression changed
        
        # Mark as no longer needing generation in the separate tracking dict
        user_key = f"{chat_id_str}:{user_id_str}"
        if user_key in self.user_impressions:
            if self.user_impressions[user_key].get("needs_generation", False):
                 self.user_impressions[user_key]["needs_generation"] = False
                 self._dirty = True # Mark dirty as generation state changed
            
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