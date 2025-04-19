import json
import os
from datetime import datetime
from collections import defaultdict

class GlobalMemory:
    """
    Manages global memory about users across all chats
    and tracks relationships between users
    """
    def __init__(self, config=None, memory_file="global_memory.json"):
        # Use /memory directory for storage
        self.memory_dir = "/memory"
        self.config = config or {}
        
        # Get settings from config if available
        global_settings = self.config.get("global_memory_settings", {})
        memory_file = global_settings.get("memory_file", memory_file)
        self.memory_file = os.path.join(self.memory_dir, memory_file)
        
        self.users = self._load_memory()
        self.chat_analytics = {}  # Store analytics about chats
        self.relationship_analyses = {}  # Store analyses of user relationships
        
        # Tracking when various analyses were last performed
        self.last_analyses = {
            "user_analysis": {},  # Per-user last analysis
            "chat_analysis": {},  # Per-chat last analysis
            "relationship_analysis": {}  # Per-chat last relationship analysis
        }
        
        # Set thresholds from config or use defaults
        default_thresholds = {
            "messages_for_user_update": 100,  # Messages before updating user profile
            "messages_for_chat_update": 100,  # Messages before updating chat analytics
            "messages_for_relationship_update": 100  # Messages before updating relationships
        }
        
        # Override defaults with config values if available
        self.analysis_thresholds = default_thresholds.copy()
        config_thresholds = global_settings.get("analysis_thresholds", {})
        for key, value in config_thresholds.items():
            if key in self.analysis_thresholds:
                self.analysis_thresholds[key] = value
        
        # Set max impressions from config
        self.max_impressions = global_settings.get("impression_history", {}).get("max_saved_impressions", 5)
        
        # Flag to track if memory needs saving
        self._dirty = False
        
        # Print initialization info
        print(f"GlobalMemory initialized with thresholds: {self.analysis_thresholds}")
        print(f"Max saved impressions per user: {self.max_impressions}")
    
    def _load_memory(self):
        """Load global memory from file if it exists"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("users", {})
            except Exception as e:
                print(f"Error loading global memory: {str(e)}")
        return {}
    
    def _save_memory(self):
        """Save global memory to file"""
        # Only save if data has changed
        if not self._dirty:
            return

        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            
            data = {
                "users": self.users,
                "chat_analytics": self.chat_analytics,
                "relationship_analyses": self.relationship_analyses,
                "last_analyses": self.last_analyses,
                "last_updated": datetime.now().isoformat()
            }
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Reset dirty flag after successful save
            self._dirty = False
        except Exception as e:
            print(f"Error saving global memory: {str(e)}")
    
    def process_message(self, chat_id, user_id, username, message, is_bot=False):
        """
        Process a message to update global user memory and relationships
        Returns True if any analysis was performed, False otherwise
        """
        if is_bot or not user_id:
            return False
        
        chat_id_str = str(chat_id)
        user_id_str = str(user_id)
        
        # Create or update user in global memory
        self._ensure_user_exists(user_id_str, username)
        
        # Record that this user participated in this chat
        if "chats" not in self.users[user_id_str]:
            self.users[user_id_str]["chats"] = {}
            
        if chat_id_str not in self.users[user_id_str]["chats"]:
            self.users[user_id_str]["chats"][chat_id_str] = {
                "first_seen": datetime.now().isoformat(),
                "message_count": 0
            }
        
        # Update message count for this user in this chat
        self.users[user_id_str]["chats"][chat_id_str]["message_count"] += 1
        self.users[user_id_str]["chats"][chat_id_str]["last_activity"] = datetime.now().isoformat()
        self.users[user_id_str]["total_messages"] = self.users[user_id_str].get("total_messages", 0) + 1
        self._dirty = True # Mark memory as dirty
        
        # Initialize chat analytics if needed
        if chat_id_str not in self.chat_analytics:
            self.chat_analytics[chat_id_str] = {
                "total_messages": 0,
                "active_users": {},
                "topics": defaultdict(int),
                "sentiment": {
                    "positive": 0,
                    "neutral": 0,
                    "negative": 0
                }
            }
            self._dirty = True # Mark memory as dirty (new chat analytics entry)
        
        # Update chat analytics
        self.chat_analytics[chat_id_str]["total_messages"] += 1
        self.chat_analytics[chat_id_str]["active_users"][user_id_str] = {
            "username": username,
            "last_activity": datetime.now().isoformat()
        }
        self._dirty = True # Mark memory as dirty
        
        # Check if we should perform analyses
        analyses_performed = False
        
        # User analysis (per 100 messages from this user)
        user_messages = self.users[user_id_str]["total_messages"]
        last_user_analysis = self.last_analyses["user_analysis"].get(user_id_str, 0)
        
        if user_messages - last_user_analysis >= self.analysis_thresholds["messages_for_user_update"]:
            # Mark that we're due for analysis 
            # (actual analysis will happen in generate_user_profile())
            self.users[user_id_str]["needs_profile_update"] = True
            self.last_analyses["user_analysis"][user_id_str] = user_messages
            analyses_performed = True
            self._dirty = True # Mark memory as dirty (analysis flags changed)
        
        # Chat analysis
        chat_messages = self.chat_analytics[chat_id_str]["total_messages"]
        last_chat_analysis = self.last_analyses["chat_analysis"].get(chat_id_str, 0)
        
        if chat_messages - last_chat_analysis >= self.analysis_thresholds["messages_for_chat_update"]:
            # Mark chat for analysis
            # (actual analysis will happen in generate_chat_analytics())
            self.chat_analytics[chat_id_str]["needs_update"] = True
            self.last_analyses["chat_analysis"][chat_id_str] = chat_messages
            analyses_performed = True
            self._dirty = True # Mark memory as dirty (analysis flags changed)
            
        # Relationship analysis
        last_relationship_analysis = self.last_analyses["relationship_analysis"].get(chat_id_str, 0)
        
        if chat_messages - last_relationship_analysis >= self.analysis_thresholds["messages_for_relationship_update"]:
            # Mark for relationship analysis
            # (actual analysis will happen in generate_relationship_analysis())
            if chat_id_str not in self.relationship_analyses:
                self.relationship_analyses[chat_id_str] = {}
            
            self.relationship_analyses[chat_id_str]["needs_update"] = True
            self.last_analyses["relationship_analysis"][chat_id_str] = chat_messages
            analyses_performed = True
            self._dirty = True # Mark memory as dirty (analysis flags changed)
        
        # Save changes if dirty
        self._save_memory()
        
        return analyses_performed
    
    def _ensure_user_exists(self, user_id, username):
        """Create user entry if it doesn't exist yet"""
        if user_id not in self.users:
            self.users[user_id] = {
                "user_id": user_id,
                "username": username,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "total_messages": 0,
                "profile": {
                    "personality": "",
                    "interests": [],
                    "behavior_patterns": [],
                    "relationship_with_bot": "neutral"
                },
                "impressions": {}  # Bot's subjective impressions about this user
            }
            self._dirty = True # Mark memory as dirty (new user created)
        else:
            # Update basic info only if username changed
            if self.users[user_id]["username"] != username:
                 self.users[user_id]["username"] = username # Keep username updated
                 self._dirty = True # Mark memory as dirty
            # Always update last_seen, but don't mark as dirty just for this
            self.users[user_id]["last_seen"] = datetime.now().isoformat()
    
    def get_user_profile(self, user_id):
        """Get the user profile from global memory"""
        user_id_str = str(user_id)
        
        if user_id_str in self.users:
            # Mark as no longer needing update
            self.users[user_id_str]["needs_profile_update"] = False

            # Save changes
            self._dirty = True # Mark memory as dirty
            self._save_memory()
            return self.users[user_id_str]
        
        return None
    
    def get_chat_users(self, chat_id):
        """Get all users who have participated in a chat"""
        chat_id_str = str(chat_id)
        users_in_chat = {}
        
        for user_id, user_data in self.users.items():
            if "chats" in user_data and chat_id_str in user_data["chats"]:
                users_in_chat[user_id] = user_data
                
        return users_in_chat
    
    def save_user_profile(self, user_id, profile_data):
        """
        Save an AI-generated user profile to global memory
        """
        user_id_str = str(user_id)
        
        if user_id_str not in self.users:
            print(f"Warning: Trying to save profile for unknown user {user_id_str}")
            return False
        
        # Update profile
        self.users[user_id_str]["profile"] = profile_data
        
        # Mark as no longer needing update
        self.users[user_id_str]["needs_profile_update"] = False
        
        # Save changes
        self._dirty = True # Mark memory as dirty
        self._save_memory()
        return True
    
    def save_user_impression(self, user_id, impression):
        """
        Save bot's impression of a user to global memory
        """
        user_id_str = str(user_id)
        
        if user_id_str not in self.users:
            print(f"Warning: Trying to save impression for unknown user {user_id_str}")
            return False
        
        # Add recent impression with timestamp
        current_time = datetime.now().isoformat()
        
        # Initialize impressions dict if it doesn't exist
        if "impressions" not in self.users[user_id_str]:
            self.users[user_id_str]["impressions"] = {}
            
        self.users[user_id_str]["impressions"][current_time] = impression
        
        # Keep only the most recent impressions based on config
        if len(self.users[user_id_str]["impressions"]) > self.max_impressions:
            # Sort by timestamp and remove oldest
            sorted_impressions = sorted(self.users[user_id_str]["impressions"].items())
            oldest_timestamp = sorted_impressions[0][0]
            del self.users[user_id_str]["impressions"][oldest_timestamp]
        
        # Save changes
        self._dirty = True # Mark memory as dirty
        self._save_memory()
        return True
    
    def get_user_impressions(self, user_id):
        """
        Get the history of impressions about a user
        """
        user_id_str = str(user_id)
        
        if user_id_str in self.users and "impressions" in self.users[user_id_str]:
            return self.users[user_id_str]["impressions"]
        
        return {}
    
    def get_latest_user_impression(self, user_id):
        """
        Get the most recent impression about a user
        """
        user_id_str = str(user_id)
        
        if user_id_str in self.users and "impressions" in self.users[user_id_str]:
            impressions = self.users[user_id_str]["impressions"]
            if impressions:
                # Get the latest impression
                latest_timestamp = max(impressions.keys())
                return impressions[latest_timestamp]
        
        return ""
    
    def save_relationship_analysis(self, chat_id, relationships_data):
        """
        Save an AI-generated analysis of relationships between users in a chat
        """
        chat_id_str = str(chat_id)
        
        # Initialize if needed
        if chat_id_str not in self.relationship_analyses:
            self.relationship_analyses[chat_id_str] = {}
        
        # Update with new analysis
        self.relationship_analyses[chat_id_str]["relationships"] = relationships_data
        self.relationship_analyses[chat_id_str]["last_updated"] = datetime.now().isoformat()
        
        # Mark as no longer needing update
        self.relationship_analyses[chat_id_str]["needs_update"] = False
        
        # Save changes
        self._dirty = True # Mark memory as dirty
        self._save_memory()
        return True
    
    def get_relationship_analysis(self, chat_id):
        """
        Get the latest relationship analysis for a chat
        """
        chat_id_str = str(chat_id)
        
        if chat_id_str in self.relationship_analyses and "relationships" in self.relationship_analyses[chat_id_str]:
            return self.relationship_analyses[chat_id_str]["relationships"]
        
        return None
    
    def get_users_needing_profile_updates(self):
        """
        Return a list of user_ids that need profile updates
        """
        needs_update = []
        
        for user_id, user_data in self.users.items():
            if user_data.get("needs_profile_update", False):
                needs_update.append(user_id)
        
        return needs_update
    
    def get_chats_needing_analysis(self):
        """
        Return a list of chat_ids that need chat analysis
        """
        needs_update = []
        
        for chat_id, chat_data in self.chat_analytics.items():
            if chat_data.get("needs_update", False):
                needs_update.append(chat_id)
        
        return needs_update
    
    def get_chats_needing_relationship_analysis(self):
        """
        Return a list of chat_ids that need relationship analysis
        """
        needs_update = []
        
        for chat_id, relation_data in self.relationship_analyses.items():
            if relation_data.get("needs_update", False):
                needs_update.append(chat_id)
        
        return needs_update
    
    def update_thresholds(self, thresholds_dict):
        """
        Update the message thresholds for various analyses
        """
        for key, value in thresholds_dict.items():
            if key in self.analysis_thresholds:
                self.analysis_thresholds[key] = value
                self._dirty = True # Mark memory as dirty (thresholds are part of saved data implicitly via config, but let's mark it)
        
        self._save_memory() # Save if thresholds changed
        return self.analysis_thresholds
        
    def get_global_context(self, chat_id, user_id):
        """
        Get a formatted context string containing global user info
        and relationship data for use in AI requests
        """
        user_id_str = str(user_id)
        chat_id_str = str(chat_id)
        context = "Global memory information:\n\n"
        
        # Add user profile if available
        if user_id_str in self.users:
            user_data = self.users[user_id_str]
            username = user_data.get("username", "Unknown")
            
            context += f"User information for {username} (ID: {user_id_str}):\n"
            
            # Add profile data
            profile = user_data.get("profile", {})
            if profile and profile.get("personality"):
                context += f"Personality: {profile.get('personality', '')}\n"
            
            if profile and profile.get("interests"):
                context += "Interests: " + ", ".join(profile.get("interests", [])) + "\n"
            
            if profile and profile.get("behavior_patterns"):
                context += "Behavior patterns: " + ", ".join(profile.get("behavior_patterns", [])) + "\n"
            
            if profile and profile.get("relationship_with_bot"):
                context += f"My relationship with this user: {profile.get('relationship_with_bot', 'neutral')}\n"
            
            # Add most recent impression
            latest_impression = self.get_latest_user_impression(user_id_str)
            if latest_impression:
                context += f"\nMy impression of this user: {latest_impression}\n"
        
        # Add relationship data for the current chat
        if chat_id_str in self.relationship_analyses and "relationships" in self.relationship_analyses[chat_id_str]:
            relationship_data = self.relationship_analyses[chat_id_str]["relationships"]
            
            # Check if the current user is mentioned in any relationships
            user_relationships = []
            for relation in relationship_data:
                if user_id_str in relation.get("user_ids", []):
                    user_relationships.append(relation)
            
            if user_relationships:
                context += "\nUser relationships in this chat:\n"
                for relation in user_relationships:
                    context += f"- {relation.get('description', '')}\n"
        
        return context 