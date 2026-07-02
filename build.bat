@echo off
REM Build SteamDupeSeller.exe (PyInstaller onefile, bez konsoli).
REM Wymagania: Python 3.10+ w PATH (launcher `py`). Uruchom z katalogu repo.

setlocal

if not exist venv (
    echo [1/3] Tworze venv...
    py -3 -m venv venv || goto :error
)

echo [2/3] Instaluje zaleznosci...
call venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || goto :error

echo [3/3] PyInstaller...
REM --collect-submodules steam.protobufs: pakiet `steam` laduje protobufy
REM dynamicznie — bez tego w .exe zabraknie steammessages_auth_pb2.
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name SteamDupeSeller ^
    --icon app.ico ^
    --add-data "app.ico;." ^
    --collect-submodules steam.protobufs ^
    --hidden-import steam.protobufs.steammessages_auth_pb2 ^
    steam_seller_gui.py || goto :error

echo.
echo Gotowe: dist\SteamDupeSeller.exe
exit /b 0

:error
echo.
echo BUILD NIEUDANY — sprawdz komunikaty wyzej.
exit /b 1
