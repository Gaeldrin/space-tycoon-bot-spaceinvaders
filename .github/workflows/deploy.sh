echo "Killing previous bot"
pkill python3
echo "Change directory"
cd space-tycoon-bot-spaceinvaders
echo "Updating from git"
git pull
echo "Launching new bot in background"
python3 bot.py > /dev/null &