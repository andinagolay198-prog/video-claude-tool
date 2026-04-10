@echo off
chcp 65001 >nul
echo.
echo ================================================
echo   Video-Claude Tool - Push to GitHub
echo ================================================
echo.

set REPO_URL=https://github.com/andinagolay198-prog/video-claude-tool.git

cd /d "%~dp0"

echo [1/5] Kiem tra Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Git chua cai. Tai: https://git-scm.com
    pause & exit /b 1
)
echo     Git OK

echo.
echo [2/5] Khoi tao repo...
if not exist ".git" (
    git init
    echo     Da init .git
) else (
    echo     .git da ton tai
)

echo.
echo [3/5] Cau hinh remote origin...
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%
echo     Remote: %REPO_URL%

echo.
echo [4/5] Add va commit...
git add -A
git status --short
git commit -m "feat: Video x Claude - ffmpeg + Whisper + FastAPI + Docker (ports 8765/8766)"
if errorlevel 1 echo     (Khong co thay doi moi)

echo.
echo [5/5] Push len GitHub...
git branch -M main
git push -u origin main -f

if errorlevel 1 (
    echo.
    echo [LOI] Push that bai. Hay thu:
    echo   1. Kiem tra da dang nhap GitHub
    echo   2. Tao PAT: github.com - Settings - Developer settings - Personal access tokens
    echo   3. Khi hoi password, nhap PAT thay vi password
    echo.
    echo Hoac dung GitHub CLI:
    echo   winget install GitHub.cli
    echo   gh auth login
    echo   gh repo push
) else (
    echo.
    echo ================================================
    echo   THANH CONG!
    echo   https://github.com/andinagolay198-prog/video-claude-tool
    echo ================================================
    echo.
    echo Sau do SSH vao docker-server (CT100) va chay:
    echo   bash ^<(curl -fsSL https://raw.githubusercontent.com/andinagolay198-prog/video-claude-tool/main/deploy-proxmox.sh)
)
echo.
pause
