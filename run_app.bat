@echo off
cd /d "C:\Users\peckh\Desktop\OneDrive\VSC Projects\Darius Kor Kor"
call .venv\Scripts\activate.bat
py -3.11 -m streamlit run app.py
pause
