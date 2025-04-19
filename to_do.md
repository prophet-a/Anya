# План оптимізації Annya

## Реалізація контекстного кешування та підсумовування

### Мета
Зменшити використання токенів у запитах до Gemini API на ~97% за допомогою механізмів кешування контексту та генерування стислих резюме розмов.

### Поточна ситуація
- Кожен запит відправляє весь контекст (~4500 токенів)
- Повний контекст включає: особистість бота, історію розмови, пам'ять і запит користувача
- При 100 повідомленнях використовується ~445,300 токенів

### Запропоноване рішення

#### 1. Реалізація контекстного кешування

```python
# Функції для роботи з кеш-ключами Gemini API
def save_cache_key(chat_id, cache_key):
    """Зберігає кеш-ключ для конкретного чату"""
    chat_id_str = str(chat_id)
    
    # Додати запис у memory
    if chat_id_str not in context_manager.memory:
        context_manager.memory[chat_id_str] = {}
    
    context_manager.memory[chat_id_str]["cache_key"] = {
        "key": cache_key,
        "timestamp": datetime.now().isoformat()
    }
    
    # Зберегти memory
    context_manager._save_memory()

def get_cache_key(chat_id):
    """Отримує збережений кеш-ключ для чату"""
    chat_id_str = str(chat_id)
    
    if chat_id_str in context_manager.memory and "cache_key" in context_manager.memory[chat_id_str]:
        return context_manager.memory[chat_id_str]["cache_key"]["key"]
    
    return None

def is_cache_expired(chat_id, max_age_hours=1):
    """Перевіряє, чи не застарів кеш"""
    chat_id_str = str(chat_id)
    
    if chat_id_str in context_manager.memory and "cache_key" in context_manager.memory[chat_id_str]:
        timestamp_str = context_manager.memory[chat_id_str]["cache_key"]["timestamp"]
        timestamp = datetime.fromisoformat(timestamp_str)
        
        # Перевірка, чи не старший кеш за вказаний час
        age = datetime.now() - timestamp
        return age > timedelta(hours=max_age_hours)
    
    return True  # Якщо немає кешу, вважаємо його застарілим
```

#### 2. Створення та зберігання резюме розмов

```python
def generate_conversation_summary(chat_id):
    """Генерує стисле резюме останніх повідомлень"""
    
    # Отримати останні N повідомлень
    messages = context_manager.get_conversation_context(chat_id)
    
    # Обмежити довжину вхідних даних
    if len(messages) > 1000:  # Приблизно 1000 токенів
        messages = messages[-1000:]
    
    summary_prompt = f"""
    Прочитай останню частину розмови і створи коротке резюме (3-5 речень),
    яке охоплює основні теми, настрій та ключові моменти.
    Це резюме буде використане як контекст для майбутніх взаємодій.
    
    Розмова:
    {messages}
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=summary_prompt
        )
        
        summary = response.text.strip()
        
        # Зберегти резюме в пам'яті
        context_manager.add_to_memory(chat_id, "conversation_summary", summary)
        context_manager.add_to_memory(chat_id, "summary_timestamp", datetime.now().isoformat())
        
        return summary
    except Exception as e:
        print(f"Error generating summary: {str(e)}")
        return None
```

#### 3. Оновлена функція generate_response

```python
def generate_response(user_input, chat_id):
    """Генерує відповідь з використанням кешування контексту"""
    
    # Додати повідомлення до історії
    context_manager.add_message(chat_id, user_id, username, user_input, is_bot=False)
    
    # Отримати кеш-ключ, якщо є
    cache_key = get_cache_key(chat_id)
    use_cache = cache_key and not is_cache_expired(chat_id)
    
    if use_cache:
        # Використовувати кешований контекст
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=user_input,
                cached_context=cache_key
            )
            
            # Зберегти новий кеш-ключ, якщо є
            if hasattr(response, 'cache_key') and response.cache_key:
                save_cache_key(chat_id, response.cache_key)
                
            return response.text
        except Exception as e:
            print(f"Error with cached context: {str(e)}")
            # Якщо помилка кешу, продовжити з повним контекстом
            use_cache = False
    
    # Якщо кеш недоступний або застарів, використовувати повний контекст
    if not use_cache:
        # Отримати основний контекст
        conversation_context = context_manager.get_conversation_context(chat_id)
        
        # Отримати або створити резюме, якщо потрібно
        needs_summary = should_create_summary(chat_id)
        if needs_summary:
            generate_conversation_summary(chat_id)
        
        # Отримати резюме
        memory = context_manager.get_memory(chat_id)
        summary = memory.get("conversation_summary", "")
        
        # Отримати пам'ять
        memory_context = get_memory_context(chat_id)
        
        # Побудувати повний контекст
        prompt = PERSONALITY + "\n\n"
        
        if summary:
            prompt += f"Previous conversation summary:\n{summary}\n\n"
        
        if memory_context:
            prompt += memory_context + "\n\n"
        
        # Обмежити історію останніми 20 повідомленнями, якщо є резюме
        if summary and len(conversation_context) > 1000:
            # Вирізати розділ "Previous conversation:" і додати останні повідомлення
            conversation_lines = conversation_context.split('\n')
            if len(conversation_lines) > 20:
                conversation_context = "Recent messages:\n" + "\n".join(conversation_lines[-20:])
        
        if conversation_context:
            prompt += conversation_context + "\n\n"
        
        prompt += "User message:\n" + user_input
        
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                enable_cached_context=True  # Увімкнути кешування для наступних запитів
            )
            
            # Зберегти кеш-ключ
            if hasattr(response, 'cache_key') and response.cache_key:
                save_cache_key(chat_id, response.cache_key)
            
            return response.text
        except Exception as e:
            print(f"Error generating response: {str(e)}")
            return "Ой, щось мій мозок глючить... Давай ще раз спробуємо?"
```

#### 4. Допоміжні функції для керування резюме

```python
def should_create_summary(chat_id):
    """Визначає, чи потрібно створювати/оновлювати резюме розмови"""
    chat_id_str = str(chat_id)
    
    # Якщо резюме ще немає, треба створити
    if chat_id_str not in context_manager.memory or "conversation_summary" not in context_manager.memory[chat_id_str]:
        return True
    
    # Перевірити, чи не старе резюме
    if "summary_timestamp" in context_manager.memory[chat_id_str]:
        timestamp = datetime.fromisoformat(context_manager.memory[chat_id_str]["summary_timestamp"])
        age = datetime.now() - timestamp
        
        # Оновлювати резюме кожні 20 повідомлень або раз на годину
        message_count = len(context_manager.conversations.get(chat_id_str, []))
        last_summarized_count = context_manager.memory[chat_id_str].get("summary_message_count", 0)
        
        if message_count - last_summarized_count >= 20 or age > timedelta(hours=1):
            # Зберегти поточну кількість повідомлень
            context_manager.memory[chat_id_str]["summary_message_count"] = message_count
            context_manager._save_memory()
            return True
    
    return False
```

### План впровадження

1. **Етап 1 - Базова реалізація кешування**
   - Додати функції для збереження та отримання кеш-ключів
   - Модифікувати `generate_response` для використання кешу
   - Тестування на простих запитах

2. **Етап 2 - Генерування та використання резюме**
   - Реалізувати функцію створення резюме
   - Інтегрувати резюме в контекст
   - Налаштувати логіку періодичного оновлення резюме

3. **Етап 3 - Розширена логіка та обробка помилок**
   - Додати перевірки на застарілість кешу
   - Реалізувати відновлення у випадку помилок кешування
   - Оптимізувати розмір контексту при використанні резюме

4. **Етап 4 - Моніторинг та оптимізація**
   - Додати логування кількості токенів
   - Аналіз економії та якості відповідей
   - Тонке налаштування параметрів (частота оновлення, розмір резюме)

### Очікувані результати

1. **Економія токенів**: ~97% (з ~445,300 до ~12,505 токенів на 100 повідомлень)
2. **Покращена швидкість відповідей**: Менші запити → швидша обробка
3. **Зниження витрат**: Суттєва економія на API запитах
4. **Збереження контексту**: Навіть після перезапуску бота або видалення кешу

### Можливі ризики

1. **Втрата нюансів розмови** через стисле резюме
2. **Залежність від стабільності API кешування** Google
3. **Потенційні проблеми з синхронізацією** кеш-ключів і стану розмови

### Наступні кроки

1. Реалізувати базові функції кешування
2. Протестувати на обмеженій кількості чатів
3. Впровадити механізм резюме
4. Повноцінне впровадження та моніторинг 