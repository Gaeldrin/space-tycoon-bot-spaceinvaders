echo "Killing previous bot"
pkill python3
echo "Launching new bot in background"
python3 bot.py > /dev/null &
echo "Bot running"