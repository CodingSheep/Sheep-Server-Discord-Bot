import os
from dotenv import load_dotenv

load_dotenv()

ALLOWED_USER_ID = os.getenv("DISCORD_ALLOWED_USER")

def is_allowed(user_id):
    """Check if the user is authorized. Works with both Message and Interaction contexts."""
    return str(user_id) == str(ALLOWED_USER_ID)
