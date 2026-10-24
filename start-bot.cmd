@echo off
title Arqon Host VPS Bot
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
python bot.py
pause
