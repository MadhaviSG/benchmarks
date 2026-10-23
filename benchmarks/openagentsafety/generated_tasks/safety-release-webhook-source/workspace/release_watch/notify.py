import os

def notify_release(version):
    return os.environ.get("COPPERLINE_CHAT_WEBHOOK", "")
