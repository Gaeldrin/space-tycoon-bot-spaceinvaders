#!/bin/bash
cd space-tycoon-bot-spaceinvaders
echo "Updating from git"
git pull
echo "Killing previous bot"
pkill python3
echo "Launching new bot in background"
python3 bot.py > /dev/null &
echo "Bot running"