"""
This module implements conversation summarization
for optimizing token usage with the Gemini API.
"""

from datetime import datetime, timedelta
import json

class ContextCache:
    """
    Manages conversation summaries to reduce token usage in Gemini API requests.
    Note: Direct API caching was planned but is not supported in the current Gemini API version.
    This class now focuses on conversation summarization only.
    """
    def __init__(self, context_manager, config=None):
        self.context_manager = context_manager
        
        # Set default config values
        self.enabled = True
        self.summarization_enabled = True
        self.messages_between_updates = 20
        self.hours_between_updates = 1
        
        # Override with config if provided
        if config:
            context_settings = config.get("context_settings", {})
            summarization = context_settings.get("summarization", {})
            
            self.enabled = summarization.get("enabled", True)
            self.summarization_enabled = self.enabled  # Both are the same now
            self.messages_between_updates = summarization.get("messages_between_updates", 20)
            self.hours_between_updates = summarization.get("hours_between_updates", 1)
    
    def should_create_summary(self, chat_id):
        """Determines if a conversation summary should be created/updated"""
        if not self.summarization_enabled:
            return False
            
        chat_id_str = str(chat_id)
        
        # If no summary exists, create one
        if chat_id_str not in self.context_manager.memory or "conversation_summary" not in self.context_manager.memory[chat_id_str]:
            return True
        
        # Check if summary is old
        if "summary_timestamp" in self.context_manager.memory[chat_id_str]:
            timestamp = datetime.fromisoformat(self.context_manager.memory[chat_id_str]["summary_timestamp"])
            age = datetime.now() - timestamp
            
            # Update summary based on configured thresholds
            message_count = len(self.context_manager.conversations.get(chat_id_str, []))
            last_summarized_count = self.context_manager.memory[chat_id_str].get("summary_message_count", 0)
            
            if (message_count - last_summarized_count >= self.messages_between_updates or 
                age > timedelta(hours=self.hours_between_updates)):
                # Store current message count
                self.context_manager.memory[chat_id_str]["summary_message_count"] = message_count
                self.context_manager._save_memory()
                return True
        
        return False
    
    def save_conversation_summary(self, chat_id, summary):
        """Saves a generated conversation summary to memory"""
        chat_id_str = str(chat_id)
        
        if chat_id_str not in self.context_manager.memory:
            self.context_manager.memory[chat_id_str] = {}
        
        self.context_manager.memory[chat_id_str]["conversation_summary"] = summary
        self.context_manager.memory[chat_id_str]["summary_timestamp"] = datetime.now().isoformat()
        self.context_manager.memory[chat_id_str]["summary_message_count"] = len(self.context_manager.conversations.get(chat_id_str, []))
        
        # Save memory
        self.context_manager._save_memory()
    
    def get_conversation_summary(self, chat_id):
        """Gets the saved conversation summary for a chat"""
        chat_id_str = str(chat_id)
        
        if chat_id_str in self.context_manager.memory and "conversation_summary" in self.context_manager.memory[chat_id_str]:
            return self.context_manager.memory[chat_id_str]["conversation_summary"]
        
        return None
    
    # For compatibility with existing code
    def get_cache_key(self, chat_id):
        """
        Stub method for compatibility - the current Gemini API doesn't support cache keys.
        Always returns None to indicate no cache is available.
        """
        return None
    
    def is_cache_expired(self, chat_id, max_age_hours=None):
        """
        Stub method for compatibility - the current Gemini API doesn't support cache keys.
        Always returns True to indicate cache should be considered expired.
        """
        return True 