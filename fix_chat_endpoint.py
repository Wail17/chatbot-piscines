#!/usr/bin/env python3
"""
Script to add error handling to the chat endpoint
"""
import re

# Read the file
with open("app/main.py", "r") as f:
    content = f.read()

# Find the chat function
chat_pattern = r'(@app\.post\("/chat"\)\ndef chat\(req: ChatRequest, request: Request\):)'

# Replace to add try-except wrapper
replacement = r'''\1
    try:'''

content = re.sub(chat_pattern, replacement, content, count=1)

# Now find the end of the chat function and add the except block
# The function ends with the last return statement before the next @app decorator or end of file

# Find the position after the last return in the chat function
# We'll insert the except block before the next function or end of file

# Find the chat function start
chat_start = content.find('@app.post("/chat")')
if chat_start == -1:
    print("ERROR: Could not find chat endpoint")
    exit(1)

# Find the next @app decorator or end of file
next_decorator = content.find('\n@app.', chat_start + 100)
if next_decorator == -1:
    next_decorator = len(content)

# Find the last 'return' statement before the next decorator
chat_section = content[chat_start:next_decorator]
last_return_in_chat = chat_section.rfind('\n    return ')

if last_return_in_chat == -1:
    print("ERROR: Could not find return statement in chat function")
    exit(1)

# Find the end of that return statement (next newline)
return_end = chat_section.find('\n', last_return_in_chat + 1)
if return_end == -1:
    return_end = len(chat_section)

# Insert the except block after this return
insert_pos = chat_start + return_end

except_block = '''

    except Exception as e:
        # Log the error for debugging
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Chat endpoint error: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())

        # Return a user-friendly error in the expected format
        lang_code = "nl"  # Default to Dutch
        try:
            # Try to detect language from request if available
            if hasattr(req, 'query') and req.query:
                detected = detect_language_code(req.query)
                if detected:
                    lang_code = detected
        except:
            pass

        error_response = {
            "answer": _ensure_language(
                "Er is een technische fout opgetreden. Probeer het opnieuw of neem contact op met support@beniferro.eu",
                lang_code
            ),
            "citations": [],
            "error": True,
            "error_details": f"{type(e).__name__}: {str(e)}" if req.debug else None
        }

        return error_response
'''

content = content[:insert_pos] + except_block + content[insert_pos:]

# Write the modified content
with open("app/main.py", "w") as f:
    f.write(content)

print("✓ Successfully added error handling to chat endpoint")
