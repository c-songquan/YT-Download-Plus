#!/usr/bin/env bash
# =========================================================
#      YT-Download Linux / macOS 打包脚本 (PyInstaller)
# =========================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "正在清理旧的构建文件..."
rm -rf build dist

echo "开始执行 PyInstaller 打包..."
pyinstaller --onefile --windowed --name="YT-Download" --clean main.py

echo "========================================================="
echo "打包成功！输出文件位于: dist/YT-Download"
echo "========================================================="
