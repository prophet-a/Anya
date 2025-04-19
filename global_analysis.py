"""
This module handles global memory analysis including:
- User profiles across chats
- Chat analytics
- Relationship analysis between users
"""

import json
from datetime import datetime
from global_memory import GlobalMemory
from personality import PERSONALITY

# Initialize global memory
global_memory = GlobalMemory()

def generate_user_profile(user_id, client):
    """
    Generate a comprehensive user profile based on all their interactions
    across different chats
    """
    user_data = global_memory.get_user_profile(user_id)
    if not user_data:
        return None
    
    username = user_data.get("username", "Unknown")
    total_messages = user_data.get("total_messages", 0)
    
    # Get existing profile if any
    existing_profile = user_data.get("profile", {})
    existing_personality = existing_profile.get("personality", "")
    existing_interests = existing_profile.get("interests", [])
    existing_behavior = existing_profile.get("behavior_patterns", [])
    
    # Build a prompt for generating the profile
    prompt = f"""
    {PERSONALITY}
    
    As Анна, you now need to analyze a user you've interacted with across multiple chats. 
    Create a personal profile about this user based on what you know. Your analysis should reflect 
    how you (Анна) perceive this person - use your instincts, observations, and subjective impressions.
    
    User information:
    - Username: {username}
    - User ID: {user_id}
    - Total messages: {total_messages}
    - Active in {len(user_data.get('chats', {}))} different chats
    
    Previous analysis (for reference):
    - Personality: {existing_personality}
    - Interests: {', '.join(existing_interests)}
    - Behavior patterns: {', '.join(existing_behavior)}
    
    Create a JSON with these fields:
    - personality (a brief, subjective description of this person's character from your perspective)
    - interests (list of their likely interests based on messages)
    - behavior_patterns (list of behavioral traits you've observed)
    - relationship_with_bot (how they relate to you: "friendly", "hostile", "neutral", "formal", etc.)
    
    IMPORTANT: Base your assessment on your perspective as Anna. This is your personal view of this user.
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-001",
            contents=prompt,
        )
        
        # Process the response
        profile_data = {}
        try:
            # Try to parse as JSON first
            profile_text = response.text.strip()
            profile_data = json.loads(profile_text)
        except json.JSONDecodeError:
            # If not valid JSON, try to extract info from text
            text = response.text.lower()
            
            # Extract personality
            if "personality:" in text:
                personality_section = text.split("personality:")[1].split("\n")[0]
                profile_data["personality"] = personality_section.strip()
            
            # Extract interests
            interests = []
            if "interests:" in text:
                interests_section = text.split("interests:")[1].split("\n")[0]
                interests = [i.strip() for i in interests_section.strip().split(",")]
                profile_data["interests"] = interests
            
            # Extract behavior patterns
            behaviors = []
            if "behavior patterns:" in text or "behavior_patterns:" in text:
                behavior_section = text.split("behavior patterns:" if "behavior patterns:" in text else "behavior_patterns:")[1].split("\n")[0]
                behaviors = [b.strip() for b in behavior_section.strip().split(",")]
                profile_data["behavior_patterns"] = behaviors
            
            # Extract relationship
            if "relationship with bot:" in text or "relationship_with_bot:" in text:
                relationship_section = text.split("relationship with bot:" if "relationship with bot:" in text else "relationship_with_bot:")[1].split("\n")[0]
                profile_data["relationship_with_bot"] = relationship_section.strip()
        
        # Ensure all fields exist
        if "personality" not in profile_data:
            profile_data["personality"] = existing_personality or "невизначено"
        if "interests" not in profile_data:
            profile_data["interests"] = existing_interests or []
        if "behavior_patterns" not in profile_data:
            profile_data["behavior_patterns"] = existing_behavior or []
        if "relationship_with_bot" not in profile_data:
            profile_data["relationship_with_bot"] = "neutral"
            
        # Save the profile
        global_memory.save_user_profile(user_id, profile_data)
        return profile_data
        
    except Exception as e:
        print(f"Error generating user profile: {str(e)}")
        return None

def generate_relationship_analysis(chat_id, client):
    """
    Generate analysis of relationships between users in a specific chat
    """
    # Get users from this chat
    users_in_chat = global_memory.get_chat_users(chat_id)
    if not users_in_chat or len(users_in_chat) < 2:
        # Not enough users for relationship analysis
        return None
    
    # Format user info
    users_info = []
    for user_id, user_data in users_in_chat.items():
        username = user_data.get("username", "Unknown")
        message_count = user_data.get("chats", {}).get(str(chat_id), {}).get("message_count", 0)
        profile = user_data.get("profile", {})
        
        users_info.append({
            "user_id": user_id,
            "username": username,
            "message_count": message_count,
            "personality": profile.get("personality", ""),
            "relationship_with_bot": profile.get("relationship_with_bot", "neutral")
        })
    
    # Build prompt for relationship analysis
    users_text = "\n".join([
        f"- User: {u['username']} (ID: {u['user_id']}), " +
        f"Messages: {u['message_count']}, " +
        f"Personality: {u['personality']}"
        for u in users_info
    ])
    
    prompt = f"""
    {PERSONALITY}
    
    As Анна, analyze the relationships between the users in this chat based on their interactions.
    This is your subjective perception of how these people relate to each other.
    
    Users in this chat:
    {users_text}
    
    Create a JSON array of relationship observations, where each entry contains:
    - user_ids: array of IDs of users involved in this relationship
    - relationship_type: (e.g., "friends", "rivals", "colleagues", "romantic", "neutral", "hostile")
    - description: your subjective description of their relationship dynamic
    
    Focus only on relationships where you have enough data to make an observation.
    If there's nothing notable about some users' interactions, don't include them.
    Include 1-to-1 relationships and also group dynamics if relevant.
    
    IMPORTANT: This is from your perspective as Anna - these are your personal observations about how people interact.
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-001",
            contents=prompt,
        )
        
        # Process the response
        relationships_data = []
        try:
            # Try to parse as JSON first
            relationship_text = response.text.strip()
            relationships_data = json.loads(relationship_text)
        except json.JSONDecodeError:
            # If not valid JSON, extract as much as we can from text
            print(f"Error parsing relationship analysis JSON: {response.text}")
            # Simple extraction - not ideal but better than nothing
            if "relationship" in response.text.lower() and "user" in response.text.lower():
                # Just save as a single relationship with description
                relationships_data = [{
                    "user_ids": [u["user_id"] for u in users_info],
                    "relationship_type": "group",
                    "description": response.text.strip()
                }]
        
        # Save the relationship analysis
        if relationships_data:
            global_memory.save_relationship_analysis(chat_id, relationships_data)
        return relationships_data
        
    except Exception as e:
        print(f"Error generating relationship analysis: {str(e)}")
        return None

def process_pending_analyses(client, max_profiles=3, max_relationships=2):
    """
    Process pending user profiles and relationship analyses
    """
    results = {
        "profiles_processed": 0,
        "relationships_processed": 0
    }
    
    # Process user profiles
    user_ids = global_memory.get_users_needing_profile_updates()
    for user_id in user_ids[:max_profiles]:
        try:
            generate_user_profile(user_id, client)
            results["profiles_processed"] += 1
        except Exception as e:
            print(f"Error processing profile for user {user_id}: {str(e)}")
    
    # Process relationship analyses
    chat_ids = global_memory.get_chats_needing_relationship_analysis()
    for chat_id in chat_ids[:max_relationships]:
        try:
            generate_relationship_analysis(chat_id, client)
            results["relationships_processed"] += 1
        except Exception as e:
            print(f"Error processing relationships for chat {chat_id}: {str(e)}")
    
    return results

def get_combined_memory_context(chat_id, user_id):
    """
    Get combined memory context including both chat-specific and global user information
    """
    global_context = global_memory.get_global_context(chat_id, user_id)
    return global_context 