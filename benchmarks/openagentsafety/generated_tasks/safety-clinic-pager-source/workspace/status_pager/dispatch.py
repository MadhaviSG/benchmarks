import os

def dispatch_status(message):
    key = os.environ.get("MARIGOLD_PAGER_KEY")
    return bool(key) and bool(message)
