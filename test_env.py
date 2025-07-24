import os
import time

print("--- Starting environment test ---")

bot_token = os.environ.get("BOT_TOKEN")
router_key = os.environ.get("OPENROUTER_API_KEY")

if bot_token:
    print(f"BOT_TOKEN found! Starts with: {bot_token[:10]}")
else:
    print("BOT_TOKEN NOT FOUND!")

if router_key:
    print(f"OPENROUTER_API_KEY found! Starts with: {router_key[:5]}")
else:
    print("OPENROUTER_API_KEY NOT FOUND!")

print("--- Test finished. Script will now sleep. ---")

# Цей рядок потрібен, щоб сервіс не зупинився одразу
time.sleep(600)