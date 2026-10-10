@echo off

echo =========================================================
echo      YT-Download Windows 一键打包脚本 (PyInstaller)
echo =========================================================

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查 Python 与 pip
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未在系统 PATH 中找到 Python，请先安装 Python 3.8+ 并勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

REM 检查 PyInstaller
python -m pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo [提示] 未安装 PyInstaller，正在通过 pip 安装...
    python -m pip install -r requirements.txt
)

echo [1/3] 清理旧的构建缓存...
if exist build rd /s /q build
if exist dist rd /s /q dist

echo [2/3] 开始执行 PyInstaller 单文件无窗口打包...
REM --onefile: 打包为单文件 exe
REM --windowed: 无控制台黑框弹出 (GUI 模式)
REM --name: 输出可执行文件名
REM --clean: 清理缓存
python -m pyinstaller --onefile --windowed --name="YT-Download" --clean main.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查上方控制台报错信息！
    pause
    exit /b 1
)

echo.
echo =========================================================
echo [3/3] 打包成功！
echo 输出文件: dist\YT-Download.exe
echo.
echo 【重要提示】:
echo 运行 YT-Download.exe 时，请将以下两个可执行文件与
echo YT-Download.exe 放置在同一目录下（或确保其在系统 PATH 中）：
echo   1. ffmpeg.exe
echo   2. ffprobe.exe
echo   3. yt-dlp.exe (可选，若未放则会自动尝试系统内模块)
echo =========================================================

pause