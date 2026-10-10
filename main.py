#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
项目名称: YT-Download 桌面音视频下载器 (单文件 Exe + 自动下载组件版)
开发环境: Python 3.8+ (支持 Tkinter)
打包工具: PyInstaller (支持一键打包成单文件 .exe)
底层依赖: yt-dlp, ffmpeg, ffprobe, ffplay

核心特性:
  1. 单文件 Exe 运行：无需安装任何复杂环境，点击即可直接运行。
  2. 智能组件下载器：
     - 在 exe 同级目录下自动创建 'YT Download Plus' 专属文件夹。
     - 仅当组件缺失时，自动从您指定的 GitHub 链接下载 5 个必备文件进该文件夹。
     - 支持 .exe 直接存放，支持 .zip 压缩包自动解压（如 _internal 运行库）。
     - 支持下载断点续传检测、流式进度百分比与下载速度显示。
  3. 批量多线程下载播放列表（Mode A）：
     - 步骤①：flat-playlist dump-json 提取歌单标题（可带 cookie）。
     - 步骤②：创建专属目录（去除特殊字符）。
     - 步骤③：flat-playlist get-url 提取链接写入 txt 列表。
     - 步骤④：【完全脱敏去掉 Cookie】，直接读取 txt 链接，多线程并发高速下载！
  4. 单支音视频高速下载（Mode B）。
  5. 启动时动态感知工作目录，多 Python 环境扫描，全面健康检查。
=============================================================================
"""

from __future__ import annotations

# =============================================================================
# 【★ 用户自定义 GitHub 下载链接填入区 ★】
# 请在下方将每个组件对应的 GitHub 下载链接（Release 直链或 Raw 链接）填入引号中。
# 如果文件不需要下载或已内置，可以留空 ""。
# 若填入的是 .zip 链接（例如 _internal.zip），程序下载后会自动解压到该文件夹内！
# =============================================================================
DEPENDENCY_DOWNLOAD_CONFIG = {
    # 组件存放的文件夹名称（在 exe 同级目录下自动创建）
    "FOLDER_NAME": "YT Download Plus",

    # 5 个核心依赖文件的下载链接配置：
    "FILES": {
        # 1. FFmpeg 核心音视频转码器
        "ffmpeg.exe": "https://github.com/c-songquan/YT-Download-Plus/releases/download/libs/ffmpeg.exe",

        # 2. FFprobe 多媒体信息探测器
        "ffprobe.exe": "https://github.com/c-songquan/YT-Download-Plus/releases/download/libs/ffprobe.exe",

        # 3. FFplay 简易播放器
        "ffplay.exe": "https://github.com/c-songquan/YT-Download-Plus/releases/download/libs/ffplay.exe",

        # 4. yt-dlp 单文件独立版可执行文件
        "yt-dlp.exe": "https://github.com/c-songquan/YT-Download-Plus/releases/download/libs/yt-dlp.exe",

        # 5. 第 5 个文件（支持下载 .zip 压缩包并自动解压，如 _internal.zip，也可为普通 exe）
        "_internal.zip": "https://github.com/c-songquan/YT-Download-Plus/releases/download/libs/_internal.zip",
    }
}
# =============================================================================

import os
import sys
import re
import time
import json
import shutil
import zipfile
import tempfile
import threading
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 尝试导入 tkinter；在非图形或无 tkinter 模块环境下提供平滑降级支持
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, scrolledtext
    TKINTER_AVAILABLE = True
except ImportError:
    tk = None
    ttk = None
    messagebox = None
    filedialog = None
    scrolledtext = None
    TKINTER_AVAILABLE = False


def get_base_dir() -> str:
    """
    获取软件运行的根目录。
    兼容 PyInstaller 打包后的单文件 .exe（sys.frozen 为 True，sys.executable 为 exe 路径）
    以及常规 python main.py 源码运行环境。
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        return os.path.dirname(os.path.abspath(__file__))


def sanitize_filename(name: str) -> str:
    """
    过滤文件名和文件夹名中的特殊字符，避免 Windows/Linux 文件系统创建失败。
    移除或替换: \\ / : * ? \" < > | 以及首尾多余的点与空格。
    """
    if not name:
        return "Untitled"
    sanitized = re.sub(r'[\\/*?:"<>|]', '_', name)
    sanitized = re.sub(r'[\x00-\x1f\x7f]', '', sanitized)
    sanitized = sanitized.strip(' .')
    return sanitized if sanitized else "Untitled"


def format_netscape_cookies(text: str) -> str:
    """
    智能格式化 Cookie 文本：
    Netscape HTTP Cookie 规范要求列之间使用 TAB (\\t) 分隔。
    用户从网页直接复制时，TAB 常被转换成空格导致 yt-dlp 解析失败。
    本函数自动识别并规范化为标准的 TAB 分隔格式。
    """
    lines = text.strip().splitlines()
    formatted_lines = []
    has_header = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if "Netscape" in stripped:
                has_header = True
            formatted_lines.append(stripped)
            continue

        if "\t" in stripped:
            formatted_lines.append(stripped)
        else:
            parts = re.split(r'\s+', stripped, maxsplit=6)
            if len(parts) == 7:
                formatted_lines.append("\t".join(parts))
            else:
                formatted_lines.append(stripped)

    result = "\n".join(formatted_lines)
    if not has_header:
        result = "# Netscape HTTP Cookie File\n# https://curl.haxx.se/rfc/cookie_spec.html\n# This is a generated file! Do not edit.\n\n" + result
    return result


class DependencyManager:
    """
    依赖管理类：负责在 'YT Download Plus' 目录下检测与按需下载必备组件。
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.folder_name = DEPENDENCY_DOWNLOAD_CONFIG.get("FOLDER_NAME", "YT Download Plus")
        self.plus_dir = os.path.join(self.base_dir, self.folder_name)
        self.files_config = DEPENDENCY_DOWNLOAD_CONFIG.get("FILES", {})
        self.is_downloading = False
        self.cancel_flag = False

    def ensure_plus_directory(self) -> str:
        """确保 'YT Download Plus' 目录存在并返回路径"""
        os.makedirs(self.plus_dir, exist_ok=True)
        return self.plus_dir

    def check_missing_files(self) -> list[str]:
        """
        检查哪些文件在 'YT Download Plus' 目录中缺失。
        对于 .zip 文件，若已解压出对应文件夹（如 _internal 文件夹），则视为存在。
        """
        self.ensure_plus_directory()
        missing = []
        for filename in self.files_config.keys():
            target_path = os.path.join(self.plus_dir, filename)
            if filename.lower().endswith(".zip"):
                extracted_dir_name = os.path.splitext(filename)[0]
                extracted_path = os.path.join(self.plus_dir, extracted_dir_name)
                if os.path.isdir(extracted_path) or os.path.isfile(target_path):
                    continue
                missing.append(filename)
            else:
                if not os.path.isfile(target_path):
                    missing.append(filename)
        return missing

    def download_file(self, filename: str, url: str, log_callback) -> bool:
        """
        通过 urllib.request 流式下载单个文件，并向日志输出百分比和实时速度。
        若是 .zip 文件，下载完成后自动解压至 plus_dir。
        """
        if not url or "YOUR_USERNAME" in url or not url.startswith("http"):
            log_callback(f"[跳过] {filename} 未配置有效下载链接。\n", "warning")
            return False

        self.ensure_plus_directory()
        target_path = os.path.join(self.plus_dir, filename)
        temp_download_path = target_path + ".downloading"

        log_callback(f"\n[开始下载] {filename} 来自: {url}\n", "info")
        log_callback(f"目标保存路径: {target_path}\n", "info")

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) YT-Download-Client"}
            )
            with urllib.request.urlopen(req, timeout=30) as response, open(temp_download_path, "wb") as out_file:
                total_size = response.getheader("Content-Length")
                total_bytes = int(total_size) if total_size and total_size.isdigit() else 0

                downloaded_bytes = 0
                chunk_size = 64 * 1024  # 64 KB
                start_time = time.time()
                last_log_time = start_time

                while True:
                    if self.cancel_flag:
                        log_callback(f"[已取消] 用户取消了 {filename} 的下载。\n", "warning")
                        if os.path.exists(temp_download_path):
                            os.remove(temp_download_path)
                        return False

                    chunk = response.read(chunk_size)
                    if not chunk:
                        break

                    out_file.write(chunk)
                    downloaded_bytes += len(chunk)
                    current_time = time.time()

                    if current_time - last_log_time >= 0.5 or (total_bytes > 0 and downloaded_bytes >= total_bytes):
                        elapsed = current_time - start_time
                        speed = (downloaded_bytes / elapsed) if elapsed > 0 else 0
                        speed_mb = speed / (1024 * 1024)

                        if total_bytes > 0:
                            percent = (downloaded_bytes / total_bytes) * 100
                            mb_down = downloaded_bytes / (1024 * 1024)
                            mb_total = total_bytes / (1024 * 1024)
                            log_callback(
                                f"\r  [下载中] {filename}: {percent:5.1f}% ({mb_down:6.1f}MB/{mb_total:6.1f}MB) 速度: {speed_mb:4.1f} MB/s",
                                "progress"
                            )
                        else:
                            mb_down = downloaded_bytes / (1024 * 1024)
                            log_callback(
                                f"\r  [下载中] {filename}: 已下载 {mb_down:6.1f} MB 速度: {speed_mb:4.1f} MB/s",
                                "progress"
                            )
                        last_log_time = current_time

            if os.path.exists(target_path):
                os.remove(target_path)
            os.rename(temp_download_path, target_path)
            log_callback(f"\n[下载成功] {filename} 已成功下载并保存至: {self.folder_name}\\\n", "success")

            if filename.lower().endswith(".zip"):
                log_callback(f"[正在解压] 正在自动解压 {filename} 到 {self.folder_name}\\ 目录...\n", "info")
                try:
                    with zipfile.ZipFile(target_path, 'r') as zip_ref:
                        zip_ref.extractall(self.plus_dir)
                    log_callback(f"[解压完成] {filename} 已成功解压！\n", "success")
                except Exception as ze:
                    log_callback(f"[解压异常] 无法自动解压 {filename}: {ze}\n", "error")

            return True

        except Exception as e:
            if os.path.exists(temp_download_path):
                try:
                    os.remove(temp_download_path)
                except Exception:
                    pass
            log_callback(f"\n[下载失败] {filename} 下载失败: {str(e)}\n", "error")
            return False

    def download_all_missing(self, log_callback) -> int:
        """检查并下载所有缺失的组件，返回成功下载的文件数量"""
        self.cancel_flag = False
        self.is_downloading = True
        missing = self.check_missing_files()

        if not missing:
            log_callback(f"[组件检查] '{self.folder_name}' 目录下的全部组件已就绪，无需重新下载！\n", "success")
            self.is_downloading = False
            return 0

        log_callback("=" * 60 + "\n", "info")
        log_callback(f"检测到 '{self.folder_name}' 目录下缺少 {len(missing)} 个组件: {', '.join(missing)}\n", "warning")
        log_callback("正在准备自动下载缺失组件...\n", "info")
        log_callback("=" * 60 + "\n", "info")

        success_count = 0
        for filename in missing:
            if self.cancel_flag:
                break
            url = self.files_config.get(filename, "")
            ok = self.download_file(filename, url, log_callback)
            if ok:
                success_count += 1

        self.is_downloading = False
        log_callback("\n" + "=" * 60 + "\n", "info")
        log_callback(f"[下载流程结束] 共检查 {len(missing)} 个缺失组件，成功获取 {success_count} 个。\n", "success")
        log_callback("=" * 60 + "\n\n", "info")
        return success_count


class DownloaderCore:
    """
    核心业务逻辑类：负责环境检测、命令拼接、多进程及多线程下载池管理。
    优先在 'YT Download Plus' 目录下检测可用的 ffmpeg 与 yt-dlp 组件。
    """

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or get_base_dir()
        self.dep_manager = DependencyManager(self.base_dir)
        self.active_processes: list[subprocess.Popen] = []
        self.lock = threading.Lock()
        self.is_cancelled = False
        self.custom_python_path: str | None = None

    def test_executable(self, cmd: list[str]) -> tuple[bool, str]:
        """测试指定的可执行命令是否能够正常启动并执行"""
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.run(
                cmd + ["--version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            if proc.returncode == 0:
                return True, out or "OK"
            return False, err or out or f"退出码: {proc.returncode}"
        except Exception as e:
            return False, str(e)

    def find_all_python_interpreters(self) -> list[dict]:
        """扫描系统中所有可用的 Python 解释器并检测 yt_dlp 状态"""
        interpreters = []
        seen_paths = set()
        candidates = []

        if not getattr(sys, 'frozen', False):
            candidates.append(sys.executable)

        for name in ["python", "python3", "py"]:
            p = shutil.which(name)
            if p:
                candidates.append(p)

        if sys.platform.startswith("win"):
            py_launcher = shutil.which("py")
            if py_launcher:
                try:
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    proc = subprocess.run(
                        [py_launcher, "-0p"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        creationflags=creationflags
                    )
                    for line in proc.stdout.splitlines():
                        parts = line.strip().split()
                        if parts:
                            last_token = parts[-1]
                            if last_token.lower().endswith(".exe") and os.path.isfile(last_token):
                                candidates.append(last_token)
                except Exception:
                    pass

            local_appdata = os.environ.get("LOCALAPPDATA", "")
            if local_appdata:
                py_base = os.path.join(local_appdata, "Programs", "Python")
                if os.path.isdir(py_base):
                    try:
                        for entry in os.listdir(py_base):
                            candidate_exe = os.path.join(py_base, entry, "python.exe")
                            if os.path.isfile(candidate_exe):
                                candidates.append(candidate_exe)
                    except Exception:
                        pass

            for root_drive in ["C:\\", "D:\\"]:
                for ver in ["310", "311", "312", "313", "39", "38"]:
                    c_path = os.path.join(root_drive, f"Python{ver}", "python.exe")
                    if os.path.isfile(c_path):
                        candidates.append(c_path)

        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        for py_path in candidates:
            norm_path = os.path.normpath(py_path)
            if norm_path.lower() in seen_paths:
                continue
            seen_paths.add(norm_path.lower())

            try:
                proc = subprocess.run(
                    [norm_path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    creationflags=creationflags
                )
                if proc.returncode != 0:
                    continue
                version_str = proc.stdout.strip()
            except Exception:
                continue

            has_ytdlp = False
            ytdlp_ver = ""
            try:
                proc_ytdlp = subprocess.run(
                    [norm_path, "-m", "yt_dlp", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    creationflags=creationflags
                )
                if proc_ytdlp.returncode == 0:
                    has_ytdlp = True
                    ytdlp_ver = proc_ytdlp.stdout.strip()
            except Exception:
                pass

            interpreters.append({
                "path": norm_path,
                "version": version_str,
                "has_ytdlp": has_ytdlp,
                "ytdlp_version": ytdlp_ver
            })

        return interpreters

    def detect_environment(self) -> dict:
        """全面检测运行目录、'YT Download Plus' 专属目录及环境变量中的运行依赖"""
        plus_dir = self.dep_manager.plus_dir
        self.dep_manager.ensure_plus_directory()

        report = {
            "base_dir": self.base_dir,
            "plus_dir": plus_dir,
            "ffmpeg_in_plus": False,
            "ffprobe_in_plus": False,
            "ytdlp_in_plus": False,
            "ytdlp_in_plus_valid": False,
            "ffmpeg_in_base": False,
            "ffprobe_in_base": False,
            "ytdlp_in_base": False,
            "ytdlp_in_base_valid": False,
            "ffmpeg_in_path": False,
            "ffprobe_in_path": False,
            "ytdlp_in_path": False,
            "ytdlp_in_path_valid": False,
            "ffmpeg_path": None,
            "ffprobe_path": None,
            "ytdlp_cmd": None,
            "ytdlp_version": None,
            "missing_in_plus": self.dep_manager.check_missing_files(),
            "python_interpreters": [],
            "warnings": [],
            "messages": []
        }

        exe_suffix = ".exe" if sys.platform.startswith("win") else ""
        ffmpeg_name = f"ffmpeg{exe_suffix}"
        ffprobe_name = f"ffprobe{exe_suffix}"
        ytdlp_name = f"yt-dlp{exe_suffix}"

        # 1. 优先检查 'YT Download Plus' 目录
        plus_ffmpeg = os.path.join(plus_dir, ffmpeg_name)
        plus_ffprobe = os.path.join(plus_dir, ffprobe_name)
        plus_ytdlp = os.path.join(plus_dir, ytdlp_name)

        if os.path.isfile(plus_ffmpeg):
            report["ffmpeg_in_plus"] = True
            report["ffmpeg_path"] = plus_ffmpeg

        if os.path.isfile(plus_ffprobe):
            report["ffprobe_in_plus"] = True
            report["ffprobe_path"] = plus_ffprobe

        if os.path.isfile(plus_ytdlp):
            report["ytdlp_in_plus"] = True
            is_ok, msg = self.test_executable([plus_ytdlp])
            if is_ok:
                report["ytdlp_in_plus_valid"] = True
                report["ytdlp_cmd"] = plus_ytdlp
                report["ytdlp_version"] = msg
            else:
                report["warnings"].append(
                    f"【严重警告】'YT Download Plus' 目录下的 {ytdlp_name} 损坏或缺失运行库: {msg}"
                )

        # 2. 检查程序主目录 (base_dir)
        base_ffmpeg = os.path.join(self.base_dir, ffmpeg_name)
        base_ffprobe = os.path.join(self.base_dir, ffprobe_name)
        base_ytdlp = os.path.join(self.base_dir, ytdlp_name)

        if os.path.isfile(base_ffmpeg) and not report["ffmpeg_path"]:
            report["ffmpeg_in_base"] = True
            report["ffmpeg_path"] = base_ffmpeg

        if os.path.isfile(base_ffprobe) and not report["ffprobe_path"]:
            report["ffprobe_in_base"] = True
            report["ffprobe_path"] = base_ffprobe

        if os.path.isfile(base_ytdlp) and not report["ytdlp_cmd"]:
            report["ytdlp_in_base"] = True
            is_ok, msg = self.test_executable([base_ytdlp])
            if is_ok:
                report["ytdlp_in_base_valid"] = True
                report["ytdlp_cmd"] = base_ytdlp
                report["ytdlp_version"] = msg

        # 3. 检查系统 PATH
        which_ffmpeg = shutil.which("ffmpeg")
        which_ffprobe = shutil.which("ffprobe")
        which_ytdlp = shutil.which("yt-dlp")

        if which_ffmpeg and not report["ffmpeg_path"]:
            report["ffmpeg_in_path"] = True
            report["ffmpeg_path"] = which_ffmpeg

        if which_ffprobe and not report["ffprobe_path"]:
            report["ffprobe_in_path"] = True
            report["ffprobe_path"] = which_ffprobe

        if which_ytdlp and not report["ytdlp_cmd"]:
            report["ytdlp_in_path"] = True
            is_ok, msg = self.test_executable([which_ytdlp])
            if is_ok:
                report["ytdlp_in_path_valid"] = True
                report["ytdlp_cmd"] = which_ytdlp
                report["ytdlp_version"] = msg

        # 4. 扫描所有 Python 解释器
        py_list = self.find_all_python_interpreters()
        report["python_interpreters"] = py_list

        if not report["ytdlp_cmd"]:
            for py_info in py_list:
                if py_info["has_ytdlp"]:
                    report["ytdlp_cmd"] = f"{py_info['path']} -m yt_dlp"
                    report["ytdlp_version"] = f"Python 模块 v{py_info['ytdlp_version']} (来自 Python {py_info['version']})"
                    report["messages"].append(
                        f"[环境检测] 成功通过 Python 环境激活 yt-dlp: {py_info['path']}"
                    )
                    break

        if report["ffmpeg_in_plus"] and report["ffprobe_in_plus"]:
            report["messages"].append(f"[环境检测] 已在 'YT Download Plus' 目录检测到专用的 ffmpeg 与 ffprobe。")
        elif report["ffmpeg_path"] and report["ffprobe_path"]:
            report["messages"].append(
                f"[环境检测] 已找到可用多媒体组件：\n"
                f"  - ffmpeg: {report['ffmpeg_path']}\n"
                f"  - ffprobe: {report['ffprobe_path']}"
            )
        else:
            missing = []
            if not report["ffmpeg_path"]:
                missing.append("ffmpeg.exe")
            if not report["ffprobe_path"]:
                missing.append("ffprobe.exe")
            report["warnings"].append(
                f"【警告】未在运行目录检测到 {' / '.join(missing)}！\n"
                f"您可以点击上方的【一键下载缺失组件】按钮自动从 GitHub 下载至 'YT Download Plus' 目录。"
            )

        if not report["ytdlp_cmd"]:
            report["warnings"].append(
                f"【提示】未检测到可用的 yt-dlp 核心！\n"
                f"您可以点击【一键下载缺失组件】按钮自动获取，或在 Python 中安装: pip install yt-dlp"
            )

        return report

    def resolve_ytdlp_command(self) -> list[str]:
        """获取调用 yt-dlp 的前置命令列表，优先选用 'YT Download Plus' 下的独立 exe"""
        if self.custom_python_path and os.path.isfile(self.custom_python_path):
            return [self.custom_python_path, "-m", "yt_dlp"]

        exe_suffix = ".exe" if sys.platform.startswith("win") else ""
        ytdlp_name = f"yt-dlp{exe_suffix}"

        plus_ytdlp = os.path.join(self.dep_manager.plus_dir, ytdlp_name)
        if os.path.isfile(plus_ytdlp):
            is_ok, _ = self.test_executable([plus_ytdlp])
            if is_ok:
                return [plus_ytdlp]

        base_ytdlp = os.path.join(self.base_dir, ytdlp_name)
        if os.path.isfile(base_ytdlp):
            is_ok, _ = self.test_executable([base_ytdlp])
            if is_ok:
                return [base_ytdlp]

        which_ytdlp = shutil.which("yt-dlp")
        if which_ytdlp:
            is_ok, _ = self.test_executable([which_ytdlp])
            if is_ok:
                return [which_ytdlp]

        for py_info in self.find_all_python_interpreters():
            if py_info["has_ytdlp"]:
                return [py_info["path"], "-m", "yt_dlp"]

        if not getattr(sys, 'frozen', False):
            return [sys.executable, "-m", "yt_dlp"]
        return ["yt-dlp"]

    def build_ffmpeg_args(self) -> list[str]:
        """生成 --ffmpeg-location 参数，优先指向 'YT Download Plus' 目录"""
        exe_suffix = ".exe" if sys.platform.startswith("win") else ""
        ffmpeg_name = f"ffmpeg{exe_suffix}"

        plus_ffmpeg = os.path.join(self.dep_manager.plus_dir, ffmpeg_name)
        if os.path.isfile(plus_ffmpeg):
            return ["--ffmpeg-location", self.dep_manager.plus_dir]

        base_ffmpeg = os.path.join(self.base_dir, ffmpeg_name)
        if os.path.isfile(base_ffmpeg):
            return ["--ffmpeg-location", self.base_dir]

        return []

    def run_command(self, cmd: list[str], log_callback, cwd: str | None = None, prefix: str = "") -> int:
        """执行外部命令并通过回调函数实时输出"""
        if self.is_cancelled:
            return -1

        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                cwd=cwd or self.base_dir,
                creationflags=creationflags,
                encoding="utf-8",
                errors="replace"
            )

            with self.lock:
                self.active_processes.append(proc)

            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    if self.is_cancelled:
                        break
                    if line:
                        msg = f"{prefix}{line}" if prefix else line
                        log_callback(msg)
                proc.stdout.close()

            return_code = proc.wait()

            with self.lock:
                if proc in self.active_processes:
                    self.active_processes.remove(proc)

            if self.is_cancelled:
                return -1
            return return_code

        except Exception as e:
            with self.lock:
                if proc and proc in self.active_processes:
                    self.active_processes.remove(proc)
            log_callback(f"\n{prefix}[错误] 进程执行异常: {str(e)}\n")
            return -2

    def cancel(self):
        """取消当前所有正在执行的子进程"""
        self.is_cancelled = True
        self.dep_manager.cancel_flag = True
        with self.lock:
            for p in list(self.active_processes):
                try:
                    p.terminate()
                except Exception:
                    pass
            self.active_processes.clear()


class YTDownloaderGUI:
    """图形界面类"""

    def __init__(self, root):
        self.root = root
        self.root.title("YT-Download 桌面音视频下载器 v1.4 (YT Download Plus 专属组件版)")
        self.root.geometry("920x840")
        self.root.minsize(820, 720)

        self.core = DownloaderCore()
        self.temp_cookie_files: list[str] = []

        self.url_var = tk.StringVar() if tk else None
        self.cookie_mode_var = tk.StringVar(value="file") if tk else None
        self.cookie_file_var = tk.StringVar() if tk else None
        self.save_dir_var = tk.StringVar(value=self.core.base_dir) if tk else None

        self.engine_var = tk.StringVar(value="自动检测最佳引擎") if tk else None
        self.mode_var = tk.StringVar(value="批量下载播放列表") if tk else None
        self.type_var = tk.StringVar(value="仅音频 (Audio)") if tk else None
        self.format_var = tk.StringVar(value="best (原始最佳)") if tk else None
        self.quality_var = tk.StringVar(value="高清无损 (质量 0 / 最佳音质)") if tk else None
        self.threads_var = tk.StringVar(value="3 线程 (推荐并发)") if tk else None

        self.is_running = False

        self._setup_style()
        self._build_ui()
        self._check_environment_on_start()

        self._auto_check_and_download_dependencies()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self):
        if not ttk:
            return
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TLabel", font=("Microsoft YaHei", 9))
        style.configure("TButton", font=("Microsoft YaHei", 9))
        style.configure("TRadiobutton", font=("Microsoft YaHei", 9))
        style.configure("TCombobox", font=("Microsoft YaHei", 9))
        style.configure("Header.TLabel", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Plus.TButton", font=("Microsoft YaHei", 9, "bold"), foreground="#007acc")

    def _build_ui(self):
        if not ttk or not tk:
            return

        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill="both", expand=True)

        engine_group = ttk.LabelFrame(main_frame, text=" 执行核心与 'YT Download Plus' 专属组件管理 ", padding=8)
        engine_group.pack(fill="x", pady=(0, 8))

        r1 = ttk.Frame(engine_group)
        r1.pack(fill="x", pady=(0, 4))
        ttk.Label(r1, text="核心引擎:").pack(side="left", padx=(0, 5))
        self.engine_cb = ttk.Combobox(r1, textvariable=self.engine_var, state="readonly", width=50)
        self.engine_cb.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.engine_cb.bind("<<ComboboxSelected>>", self._on_engine_selected)

        custom_py_btn = ttk.Button(r1, text="选择 Python.exe...", command=self._browse_custom_python)
        custom_py_btn.pack(side="left", padx=2)

        r2 = ttk.Frame(engine_group)
        r2.pack(fill="x", pady=(2, 0))

        self.dl_dep_btn = ttk.Button(
            r2,
            text="⬇ 下载/更新 'YT Download Plus' 缺失组件",
            style="Plus.TButton",
            command=self._manual_download_dependencies
        )
        self.dl_dep_btn.pack(side="left", padx=(0, 6))

        open_plus_btn = ttk.Button(
            r2,
            text="打开 YT Download Plus 目录",
            command=self._open_plus_directory
        )
        open_plus_btn.pack(side="left", padx=(0, 6))

        refresh_btn = ttk.Button(r2, text="重新体检环境", command=self._refresh_environment)
        refresh_btn.pack(side="left", padx=(0, 6))

        self.dep_status_label = ttk.Label(r2, text="", foreground="#6a9955")
        self.dep_status_label.pack(side="right")

        input_group = ttk.LabelFrame(main_frame, text=" 链接与存储设置 ", padding=10)
        input_group.pack(fill="x", pady=(0, 8))

        url_label = ttk.Label(input_group, text="下载链接 (URL):")
        url_label.grid(row=0, column=0, sticky="w", pady=4)

        self.url_entry = ttk.Entry(input_group, textvariable=self.url_var, font=("Consolas", 10))
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=4)

        paste_url_btn = ttk.Button(input_group, text="粘贴", width=6, command=self._paste_url)
        paste_url_btn.grid(row=0, column=3, sticky="w", padx=2, pady=4)

        save_label = ttk.Label(input_group, text="存储位置 (导出目录):")
        save_label.grid(row=1, column=0, sticky="w", pady=4)

        self.save_entry = ttk.Entry(input_group, textvariable=self.save_dir_var, font=("Microsoft YaHei", 9))
        self.save_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=5, pady=4)

        browse_btn = ttk.Button(input_group, text="浏览...", width=8, command=self._browse_directory)
        browse_btn.grid(row=1, column=3, sticky="w", padx=2, pady=4)

        open_dir_btn = ttk.Button(input_group, text="打开目录", width=8, command=self._open_directory)
        open_dir_btn.grid(row=1, column=4, sticky="w", padx=2, pady=4)

        input_group.columnconfigure(1, weight=1)

        cookie_group = ttk.LabelFrame(main_frame, text=" 歌单解析 Cookie 配置 (仅用于初次提取歌单，后续下载链接时自动不使用 Cookie) ", padding=10)
        cookie_group.pack(fill="x", pady=(0, 8))

        mode_frame = ttk.Frame(cookie_group)
        mode_frame.pack(fill="x", pady=(0, 5))

        r_file = ttk.Radiobutton(mode_frame, text="上传/选择 cookies.txt 文件", variable=self.cookie_mode_var,
                                 value="file", command=self._toggle_cookie_mode)
        r_file.pack(side="left", padx=(0, 15))

        r_text = ttk.Radiobutton(mode_frame, text="直接粘贴 Cookie 文本", variable=self.cookie_mode_var,
                                 value="text", command=self._toggle_cookie_mode)
        r_text.pack(side="left", padx=(0, 15))

        r_none = ttk.Radiobutton(mode_frame, text="不使用 Cookie", variable=self.cookie_mode_var,
                                 value="none", command=self._toggle_cookie_mode)
        r_none.pack(side="left", padx=(0, 15))

        self.cookie_file_frame = ttk.Frame(cookie_group)
        self.cookie_file_entry = ttk.Entry(self.cookie_file_frame, textvariable=self.cookie_file_var, font=("Microsoft YaHei", 9))
        self.cookie_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.cookie_file_btn = ttk.Button(self.cookie_file_frame, text="选择 cookies.txt", command=self._browse_cookie_file)
        self.cookie_file_btn.pack(side="left")

        self.cookie_text_frame = ttk.Frame(cookie_group)
        if scrolledtext:
            self.cookie_text_box = scrolledtext.ScrolledText(self.cookie_text_frame, height=3, font=("Consolas", 9))
        else:
            self.cookie_text_box = tk.Text(self.cookie_text_frame, height=3, font=("Consolas", 9))
        self.cookie_text_box.pack(fill="both", expand=True)

        self._toggle_cookie_mode()

        params_group = ttk.LabelFrame(main_frame, text=" 下载参数与并发配置 ", padding=10)
        params_group.pack(fill="x", pady=(0, 8))

        p_row1 = ttk.Frame(params_group)
        p_row1.pack(fill="x", pady=4)

        ttk.Label(p_row1, text="下载模式:").pack(side="left", padx=(0, 5))
        mode_cb = ttk.Combobox(p_row1, textvariable=self.mode_var, state="readonly", width=18,
                               values=["批量下载播放列表", "下载单个视频/音频"])
        mode_cb.pack(side="left", padx=(0, 20))

        ttk.Label(p_row1, text="下载类型:").pack(side="left", padx=(0, 5))
        type_cb = ttk.Combobox(p_row1, textvariable=self.type_var, state="readonly", width=15,
                               values=["仅音频 (Audio)", "音视频 (Video)"])
        type_cb.pack(side="left", padx=(0, 20))
        type_cb.bind("<<ComboboxSelected>>", self._on_type_changed)

        ttk.Label(p_row1, text="并发线程数:").pack(side="left", padx=(0, 5))
        threads_cb = ttk.Combobox(p_row1, textvariable=self.threads_var, state="readonly", width=16,
                                  values=["1 线程 (顺序下载)", "2 线程并发", "3 线程 (推荐并发)", "4 线程并发", "5 线程并发", "8 线程高速"])
        threads_cb.pack(side="left")

        p_row2 = ttk.Frame(params_group)
        p_row2.pack(fill="x", pady=4)

        ttk.Label(p_row2, text="输出格式:").pack(side="left", padx=(0, 5))
        self.format_cb = ttk.Combobox(p_row2, textvariable=self.format_var, state="readonly", width=18)
        self.format_cb.pack(side="left", padx=(0, 20))
        self._update_format_options()

        ttk.Label(p_row2, text="音质设定:").pack(side="left", padx=(0, 5))
        self.quality_cb = ttk.Combobox(p_row2, textvariable=self.quality_var, state="readonly", width=26,
                                       values=["高清无损 (质量 0 / 最佳音质)", "高质量 (质量 2 / ~192k)", "标准质量 (质量 5 / ~128k)"])
        self.quality_cb.pack(side="left")

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(0, 8))

        self.start_btn = ttk.Button(btn_frame, text="▶ 开始下载", style="Accent.TButton",
                                    command=self._start_download_thread, width=16)
        self.start_btn.pack(side="left", padx=(0, 10))

        self.cancel_btn = ttk.Button(btn_frame, text="⏹ 停止/取消", command=self._cancel_download,
                                     state="disabled", width=14)
        self.cancel_btn.pack(side="left", padx=(0, 10))

        self.clear_log_btn = ttk.Button(btn_frame, text="清空日志", command=self._clear_log, width=10)
        self.clear_log_btn.pack(side="left")

        self.status_label = ttk.Label(btn_frame, text="就绪", foreground="#555555")
        self.status_label.pack(side="right", padx=5)

        log_group = ttk.LabelFrame(main_frame, text=" 实时执行日志与 CMD 输出 ", padding=8)
        log_group.pack(fill="both", expand=True)

        if scrolledtext:
            self.log_text = scrolledtext.ScrolledText(
                log_group,
                wrap="word",
                font=("Consolas", 9),
                background="#1e1e1e",
                foreground="#d4d4d4",
                insertbackground="#ffffff"
            )
        else:
            self.log_text = tk.Text(
                log_group,
                wrap="word",
                font=("Consolas", 9),
                background="#1e1e1e",
                foreground="#d4d4d4",
                insertbackground="#ffffff"
            )
        self.log_text.pack(fill="both", expand=True)

        self.log_text.tag_config("info", foreground="#9cdcfe")
        self.log_text.tag_config("success", foreground="#6a9955")
        self.log_text.tag_config("warning", foreground="#ce9178")
        self.log_text.tag_config("error", foreground="#f44747")
        self.log_text.tag_config("cmd", foreground="#dcdcaa")
        self.log_text.tag_config("progress", foreground="#4ec9b0")

        self.log_text.configure(state="disabled")

    # ---------------- 组件下载与管理 ----------------

    def _open_plus_directory(self):
        plus_dir = self.core.dep_manager.ensure_plus_directory()
        if sys.platform.startswith("win"):
            os.startfile(plus_dir)
        elif sys.platform.startswith("darwin"):
            subprocess.Popen(["open", plus_dir])
        else:
            subprocess.Popen(["xdg-open", plus_dir])

    def _auto_check_and_download_dependencies(self):
        missing = self.core.dep_manager.check_missing_files()
        if not missing:
            if hasattr(self, "dep_status_label"):
                self.dep_status_label.config(text="✔ 组件完整", foreground="#6a9955")
            return

        has_valid_url = any(
            url.startswith("http") and "YOUR_USERNAME" not in url
            for fname, url in self.core.dep_manager.files_config.items()
            if fname in missing
        )

        if has_valid_url:
            self._append_log(f"[自动检测] 发现缺失组件 {missing}，即将自动从 GitHub 下载...\n", "info")
            threading.Thread(target=self._run_dependency_download_worker, daemon=True).start()
        else:
            if hasattr(self, "dep_status_label"):
                self.dep_status_label.config(text=f"⚠ 缺少 {len(missing)} 个组件 (待配置链接)", foreground="#ce9178")

    def _manual_download_dependencies(self):
        if self.core.dep_manager.is_downloading:
            messagebox.showinfo("提示", "组件下载正在进行中，请耐心等待。")
            return
        threading.Thread(target=self._run_dependency_download_worker, daemon=True).start()

    def _run_dependency_download_worker(self):
        self.root.after(0, lambda: self.dl_dep_btn.config(state="disabled"))
        try:
            downloaded = self.core.dep_manager.download_all_missing(self._append_log)
            self.root.after(0, self._check_environment_on_start)
        finally:
            self.root.after(0, lambda: self.dl_dep_btn.config(state="normal"))

    # ---------------- 事件回调与辅助函数 ----------------

    def _browse_custom_python(self):
        filename = filedialog.askopenfilename(
            title="选择已安装 yt-dlp 的 python.exe 解释器",
            filetypes=[("Python Executable", "python.exe;python3.exe"), ("All Files", "*.*")]
        )
        if filename:
            self.core.custom_python_path = os.path.normpath(filename)
            self.engine_var.set(f"自定义 Python: {filename}")
            self._append_log(f"\n[配置更新] 已指定自定义 Python 核心: {filename}\n", "success")

    def _on_engine_selected(self, event=None):
        choice = self.engine_var.get()
        if choice.startswith("Python: "):
            py_path = choice.split("Python: ")[1].split(" ")[0].strip()
            self.core.custom_python_path = py_path
            self._append_log(f"\n[核心切换] 已选择 Python 解释器: {py_path}\n", "info")
        else:
            self.core.custom_python_path = None
            self._append_log(f"\n[核心切换] 已选择: {choice}\n", "info")

    def _refresh_environment(self):
        self._append_log("\n正在重新检测系统运行环境与 Python 解释器...\n", "info")
        self._check_environment_on_start()

    def _paste_url(self):
        try:
            clipboard = self.root.clipboard_get().strip()
            self.url_var.set(clipboard)
        except Exception:
            pass

    def _browse_directory(self):
        if not filedialog:
            return
        selected = filedialog.askdirectory(initialdir=self.save_dir_var.get() or self.core.base_dir)
        if selected:
            self.save_dir_var.set(os.path.normpath(selected))

    def _open_directory(self):
        target = self.save_dir_var.get()
        if not os.path.exists(target):
            try:
                os.makedirs(target, exist_ok=True)
            except Exception as e:
                if messagebox:
                    messagebox.showerror("错误", f"无法创建目录: {e}")
                return

        if sys.platform.startswith("win"):
            os.startfile(target)
        elif sys.platform.startswith("darwin"):
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])

    def _browse_cookie_file(self):
        if not filedialog:
            return
        filename = filedialog.askopenfilename(
            title="选择 cookies.txt 文件",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if filename:
            self.cookie_file_var.set(os.path.normpath(filename))

    def _toggle_cookie_mode(self):
        mode = self.cookie_mode_var.get()
        self.cookie_file_frame.pack_forget()
        self.cookie_text_frame.pack_forget()

        if mode == "file":
            self.cookie_file_frame.pack(fill="x", pady=(5, 0))
        elif mode == "text":
            self.cookie_text_frame.pack(fill="both", expand=True, pady=(5, 0))

    def _on_type_changed(self, event=None):
        self._update_format_options()

    def _update_format_options(self):
        download_type = self.type_var.get()
        if "仅音频" in download_type or download_type == "audio":
            formats = ["best (原始最佳)", "mp3", "wav", "m4a"]
        else:
            formats = ["best (原始最佳)", "mp4", "mkv"]
        self.format_cb.config(values=formats)
        self.format_cb.current(0)

    def _check_environment_on_start(self):
        self._append_log("=" * 60 + "\n", "info")
        self._append_log("YT-Download 桌面客户端已就绪 (v1.4 增强版)\n", "success")
        self._append_log(f"程序工作目录: {self.core.base_dir}\n", "info")
        self._append_log(f"组件专属目录: {self.core.dep_manager.plus_dir}\n", "info")

        report = self.core.detect_environment()

        engine_options = ["自动检测最佳引擎"]
        if report["ytdlp_in_plus_valid"]:
            engine_options.append("YT Download Plus 专属 yt-dlp.exe")
        elif report["ytdlp_in_base_valid"]:
            engine_options.append("同目录独立版 yt-dlp.exe")

        if report["ytdlp_in_path_valid"]:
            engine_options.append("系统 PATH 中的 yt-dlp")

        for py_info in report["python_interpreters"]:
            status = f"[已装 yt-dlp v{py_info['ytdlp_version']}]" if py_info["has_ytdlp"] else "[未装 yt-dlp]"
            engine_options.append(f"Python: {py_info['path']} ({status})")

        self.engine_cb.config(values=engine_options)
        self.engine_cb.current(0)

        for msg in report["messages"]:
            self._append_log(f"{msg}\n", "info")
        for warn in report["warnings"]:
            self._append_log(f"\n{warn}\n\n", "warning")

        if report["missing_in_plus"]:
            self._append_log(
                f"[组件状态] 'YT Download Plus' 目录尚缺 {len(report['missing_in_plus'])} 个文件: {report['missing_in_plus']}\n"
                f"  (填入 GitHub 链接后点击上方按钮即可自动下载)\n",
                "warning"
            )
            if hasattr(self, "dep_status_label"):
                self.dep_status_label.config(text=f"⚠ 缺少 {len(report['missing_in_plus'])} 个组件", foreground="#ce9178")
        else:
            self._append_log(f"[组件状态] 'YT Download Plus' 目录所有 5 项必备文件已完整就绪！\n", "success")
            if hasattr(self, "dep_status_label"):
                self.dep_status_label.config(text="✔ 组件完整", foreground="#6a9955")

        self._append_log("=" * 60 + "\n\n", "info")

    def _append_log(self, text: str, tag: str | None = None):
        def _insert():
            self.log_text.configure(state="normal")
            t = tag
            if not t:
                if text.startswith("[CMD]"):
                    t = "cmd"
                elif "[download]" in text or "%" in text or "[下载中]" in text:
                    t = "progress"
                elif "ERROR" in text or "错误" in text or "失败" in text:
                    t = "error"
                elif "WARNING" in text or "警告" in text or "缺少" in text:
                    t = "warning"
                elif "完成" in text or "成功" in text:
                    t = "success"
                else:
                    t = "info"

            self.log_text.insert("end", text, t)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(0, _insert)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_ui_state(self, is_running: bool):
        self.is_running = is_running
        if is_running:
            self.start_btn.config(state="disabled")
            self.cancel_btn.config(state="normal")
            self.status_label.config(text="正在下载...", foreground="#007acc")
        else:
            self.start_btn.config(state="normal")
            self.cancel_btn.config(state="disabled")
            self.status_label.config(text="空闲", foreground="#555555")

    def _cancel_download(self):
        if self.is_running:
            self.core.cancel()
            self._append_log("\n[提示] 正在请求终止所有下载进程与工作线程...\n", "warning")

    def _get_cookie_file(self) -> str | None:
        mode = self.cookie_mode_var.get()
        if mode == "file":
            filepath = self.cookie_file_var.get().strip()
            if filepath and os.path.isfile(filepath):
                return filepath
            elif filepath:
                self._append_log(f"[警告] 指定的 Cookie 文件不存在: {filepath}，将不使用 Cookie。\n", "warning")
                return None
        elif mode == "text":
            raw_content = self.cookie_text_box.get("1.0", "end").strip()
            if raw_content:
                content = format_netscape_cookies(raw_content)
                temp_file = tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix="_cookies.txt",
                    delete=False,
                    encoding="utf-8"
                )
                temp_file.write(content)
                temp_file.close()
                self.temp_cookie_files.append(temp_file.name)
                return temp_file.name
        return None

    def _start_download_thread(self):
        url = self.url_var.get().strip()
        if not url:
            if messagebox:
                messagebox.showwarning("提示", "请输入需要下载的音视频或播放列表链接！")
            self.url_entry.focus()
            return

        save_dir = self.save_dir_var.get().strip()
        if not save_dir:
            save_dir = self.core.base_dir
            self.save_dir_var.set(save_dir)

        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            if messagebox:
                messagebox.showerror("错误", f"无法创建或访问指定的存储目录: {e}")
            return

        self._set_ui_state(True)
        t = threading.Thread(target=self._execute_download_task, daemon=True)
        t.start()

    def _get_worker_count(self) -> int:
        text = self.threads_var.get()
        m = re.search(r'(\d+)', text)
        if m:
            return max(1, min(8, int(m.group(1))))
        return 3

    def _execute_download_task(self):
        try:
            self.core.is_cancelled = False
            url = self.url_var.get().strip()
            save_dir = self.save_dir_var.get().strip()
            mode = "playlist" if "播放列表" in self.mode_var.get() or self.mode_var.get() == "playlist" else "single"
            is_audio = "仅音频" in self.type_var.get() or self.type_var.get() == "audio"
            fmt_raw = self.format_var.get().split()[0].lower()
            fmt = None if fmt_raw == "best" else fmt_raw

            quality_str = self.quality_var.get()
            if "质量 0" in quality_str or quality_str == "0":
                audio_quality = "0"
            elif "质量 2" in quality_str or quality_str == "2":
                audio_quality = "2"
            else:
                audio_quality = "5"

            cookie_file = self._get_cookie_file()
            ytdlp_cmd = self.core.resolve_ytdlp_command()
            ffmpeg_args = self.core.build_ffmpeg_args()
            worker_count = self._get_worker_count()

            self._append_log("\n" + "=" * 60 + "\n", "info")
            self._append_log(f"开始执行下载任务 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n", "info")
            self._append_log(f"调用核心: {' '.join(ytdlp_cmd)}\n", "info")
            self._append_log(f"目标链接: {url}\n", "info")
            self._append_log(f"保存主目录: {save_dir}\n", "info")
            self._append_log(f"下载模式: {'批量下载播放列表' if mode == 'playlist' else '下载单个音视频'}\n", "info")
            self._append_log(f"下载类型: {'仅音频' if is_audio else '音视频'}\n", "info")
            self._append_log(f"目标格式: {fmt_raw}\n", "info")
            if is_audio:
                self._append_log(f"音频质量: {audio_quality}\n", "info")
            if mode == "playlist":
                self._append_log(f"多线程并发数: {worker_count} 个工作线程\n", "info")
            if cookie_file:
                self._append_log(f"歌单解析 Cookie: {cookie_file} (仅用于提取歌单列表，下载时自动剥离 Cookie)\n", "info")
            else:
                self._append_log("未提供 Cookie，将直接进行公开内容分析。\n", "info")
            self._append_log("=" * 60 + "\n\n", "info")

            if mode == "playlist":
                self._run_playlist_mode(url, save_dir, is_audio, fmt, audio_quality, cookie_file, ytdlp_cmd, ffmpeg_args, worker_count)
            else:
                self._run_single_mode(url, save_dir, is_audio, fmt, audio_quality, cookie_file, ytdlp_cmd, ffmpeg_args)

        except Exception as e:
            self._append_log(f"\n[异常错误] 任务未正常完成: {str(e)}\n", "error")
        finally:
            self.root.after(0, lambda: self._set_ui_state(False))

    def _run_playlist_mode(self, url, save_dir, is_audio, fmt, audio_quality, cookie_file, ytdlp_cmd, ffmpeg_args, worker_count: int):
        """模式 A：批量下载播放列表（脱敏 Cookie + 多线程并发直连）"""
        self._append_log("【步骤①】正在解析播放列表元数据及歌单标题...\n", "info")

        dump_cmd = list(ytdlp_cmd)
        if cookie_file:
            dump_cmd += ["--cookies", cookie_file]
        dump_cmd += ["--flat-playlist", "--dump-json", url]

        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        playlist_title = None
        try:
            self._append_log(f"[CMD] 解析歌单: {' '.join(dump_cmd)}\n", "cmd")
            proc = subprocess.Popen(
                dump_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                creationflags=creationflags,
                encoding="utf-8",
                errors="replace"
            )

            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    line_str = line.strip()
                    if line_str.startswith("{") and line_str.endswith("}"):
                        try:
                            data = json.loads(line_str)
                            if data.get("playlist_title"):
                                playlist_title = data["playlist_title"]
                                break
                            elif data.get("playlist"):
                                playlist_title = data["playlist"]
                                break
                            elif data.get("title") and not playlist_title:
                                playlist_title = data["title"]
                        except Exception:
                            pass
                    elif line_str:
                        if "ERROR" in line_str or "error" in line_str.lower():
                            self._append_log(f"  [yt-dlp 提示] {line_str}\n", "error")
                        elif "WARNING" in line_str:
                            self._append_log(f"  [yt-dlp 警告] {line_str}\n", "warning")
                proc.stdout.close()

            proc.wait()
        except Exception as e:
            self._append_log(f"[警告] 解析歌单标题过程出现异常: {e}\n", "warning")

        if not playlist_title:
            playlist_title = f"Playlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        safe_title = sanitize_filename(playlist_title)
        self._append_log(f"[步骤①完成] 获取到歌单名称: {playlist_title} (安全文件名: {safe_title})\n\n", "success")

        # 步骤②：创建专属目录
        self._append_log("【步骤②】正在创建专属目录...\n", "info")
        exclusive_dir = os.path.join(save_dir, safe_title)
        os.makedirs(exclusive_dir, exist_ok=True)
        self._append_log(f"[步骤②完成] 专属存储目录已就绪: {exclusive_dir}\n\n", "success")

        # 步骤③：生成链接文本
        self._append_log("【步骤③】正在提取播放列表所有子项公开链接...\n", "info")
        list_file_path = os.path.join(exclusive_dir, f"{safe_title}_链接列表.txt")

        url_cmd = list(ytdlp_cmd)
        if cookie_file:
            url_cmd += ["--cookies", cookie_file]
        url_cmd += ["--flat-playlist", "--get-url", url]

        urls_collected = []
        try:
            self._append_log(f"[CMD] 提取所有链接: {' '.join(url_cmd)}\n", "cmd")
            url_proc = subprocess.Popen(
                url_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                creationflags=creationflags,
                encoding="utf-8",
                errors="replace"
            )

            if url_proc.stdout:
                for line in iter(url_proc.stdout.readline, ""):
                    line_str = line.strip()
                    if line_str.startswith("http"):
                        urls_collected.append(line_str)
                        self._append_log(f"  [发现链接] {line_str}\n")
                    elif line_str:
                        if "ERROR" in line_str or "error" in line_str.lower():
                            self._append_log(f"  {line_str}\n", "error")
                        elif "WARNING" in line_str:
                            self._append_log(f"  {line_str}\n", "warning")
                        else:
                            self._append_log(f"  {line_str}\n", "info")
                url_proc.stdout.close()

            url_proc.wait()

            if not urls_collected:
                self._append_log("\n" + "-" * 50 + "\n", "error")
                self._append_log("[错误] 未能提取到任何有效链接！\n", "error")
                self._append_log("常见排查指导:\n", "warning")
                self._append_log("1. 若提示 'Failed to load Python DLL': 说明调用的 yt-dlp.exe 缺少运行库。建议点击上方【一键下载缺失组件】按钮获取官方单文件版，或在顶部'核心引擎'切换为您的系统 Python。\n", "warning")
                self._append_log("2. 歌单链接 ID 不完整或不存在（YouTube 歌单 ID 通常为 34 位字符，请检查是否在地址栏少复制了尾部字符）。\n", "warning")
                self._append_log("3. 歌单属于私享歌单，而提供的 Cookie 已过期或无访问权限。\n", "warning")
                self._append_log("-" * 50 + "\n\n", "error")
                return

            with open(list_file_path, "w", encoding="utf-8") as f:
                for u in urls_collected:
                    f.write(u + "\n")

            self._append_log(f"[步骤③完成] 成功提取 {len(urls_collected)} 个链接，已保存至:\n  {list_file_path}\n\n", "success")
        except Exception as e:
            self._append_log(f"[错误] 写入链接列表失败: {e}\n", "error")
            return

        # 步骤④：根据用户要求【完全不要 Cookie，直接访问 txt 链接多线程并发下载】
        total_count = len(urls_collected)
        self._append_log(f"【步骤④】开始多线程直连下载（已脱离 Cookie，{worker_count} 线程并发，共 {total_count} 首）...\n", "info")

        success_count = 0
        fail_count = 0
        completed_count = 0
        counter_lock = threading.Lock()

        def _download_single_item(index: int, item_url: str):
            if self.core.is_cancelled:
                return False, item_url

            prefix = f"[任务 {index}/{total_count}] "
            item_cmd = list(ytdlp_cmd)
            # ★ 明确不传 --cookies 参数，直接访问公开链接下载 ★

            if ffmpeg_args:
                item_cmd += ffmpeg_args

            item_cmd += ["-P", exclusive_dir, "-o", "%(title)s.%(ext)s"]

            if is_audio:
                item_cmd += [
                    "-f", "bestaudio/best",
                    "--extract-audio"
                ]
                if fmt:
                    item_cmd += ["--audio-format", fmt]
                item_cmd += ["--audio-quality", audio_quality]
            else:
                item_cmd += [
                    "-f", "bestvideo+bestaudio/best"
                ]
                if fmt:
                    item_cmd += ["--merge-output-format", fmt]

            item_cmd += [item_url]

            self._append_log(f"{prefix}开始下载: {item_url}\n", "info")
            ret_code = self.core.run_command(item_cmd, self._append_log, cwd=exclusive_dir, prefix=f"[{index}] ")

            nonlocal completed_count, success_count, fail_count
            with counter_lock:
                completed_count += 1
                if ret_code == 0:
                    success_count += 1
                    self._append_log(f"\n{prefix}✔ 下载成功完成 ({completed_count}/{total_count})\n", "success")
                elif not self.core.is_cancelled:
                    fail_count += 1
                    self._append_log(f"\n{prefix}✘ 下载失败 (返回码: {ret_code})\n", "error")

            return (ret_code == 0), item_url

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(_download_single_item, i + 1, u)
                for i, u in enumerate(urls_collected)
            ]
            for future in as_completed(futures):
                if self.core.is_cancelled:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

        if self.core.is_cancelled:
            self._append_log("\n[已停止] 用户中止了多线程下载任务。\n", "warning")
        else:
            self._append_log("\n" + "=" * 60 + "\n", "success")
            self._append_log(f"【多线程批量下载完成】总计: {total_count} 首 | 成功: {success_count} 首 | 失败: {fail_count} 首\n", "success")
            self._append_log(f"文件存储目录: {exclusive_dir}\n", "success")
            self._append_log("=" * 60 + "\n\n", "success")

    def _run_single_mode(self, url, save_dir, is_audio, fmt, audio_quality, cookie_file, ytdlp_cmd, ffmpeg_args):
        """模式 B：下载单个视频/音频"""
        self._append_log("【模式 B】正在准备单支音视频下载...\n", "info")

        single_dir = os.path.join(save_dir, "Single_Downloads")
        os.makedirs(single_dir, exist_ok=True)
        self._append_log(f"单文件存储目录: {single_dir}\n", "info")

        download_cmd = list(ytdlp_cmd)
        if cookie_file:
            download_cmd += ["--cookies", cookie_file]

        if ffmpeg_args:
            download_cmd += ffmpeg_args

        download_cmd += ["-P", single_dir, "-o", "%(title)s.%(ext)s"]

        if is_audio:
            download_cmd += [
                "-f", "bestaudio/best",
                "--extract-audio"
            ]
            if fmt:
                download_cmd += ["--audio-format", fmt]
            download_cmd += ["--audio-quality", audio_quality]
        else:
            download_cmd += [
                "-f", "bestvideo+bestaudio/best"
            ]
            if fmt:
                download_cmd += ["--merge-output-format", fmt]

        download_cmd += [url]

        ret = self.core.run_command(download_cmd, self._append_log, cwd=single_dir)
        if ret == 0:
            self._append_log(f"\n[任务全部完成] 单支媒体文件已成功下载至:\n{single_dir}\n", "success")
        elif ret != -1:
            self._append_log(f"\n[下载中断] 下载过程退出，返回码: {ret}，请查看上方日志分析原因。\n", "error")

    def _on_close(self):
        if self.is_running:
            if messagebox and not messagebox.askyesno("确认退出", "当前正在进行下载任务，确定要退出并终止下载吗？"):
                return
            self.core.cancel()

        for temp_c in self.temp_cookie_files:
            try:
                if os.path.exists(temp_c):
                    os.remove(temp_c)
            except Exception:
                pass

        if hasattr(self.root, "destroy"):
            self.root.destroy()


def main():
    if "--test" in sys.argv:
        print("[TEST] Running DownloaderCore self-test...")
        core = DownloaderCore()
        env = core.detect_environment()
        print(f"[TEST] Base dir: {env['base_dir']}")
        print(f"[TEST] Plus dir: {env['plus_dir']}")
        print(f"[TEST] Missing in plus: {env['missing_in_plus']}")
        print(f"[TEST] ffmpeg found: {bool(env['ffmpeg_path'])}")
        print(f"[TEST] ytdlp cmd: {env['ytdlp_cmd']}")
        print(f"[TEST] Sanitization test: {sanitize_filename('A/B:C*D?E<F>G|H')}")
        assert sanitize_filename('A/B:C*D?E<F>G|H') == 'A_B_C_D_E_F_G_H'
        print("[TEST] All core tests passed successfully.")
        return 0

    if not TKINTER_AVAILABLE:
        print("=" * 60)
        print("【错误】未检测到 Python tkinter 图形界面库！")
        print("请在系统上安装 Python Tkinter 模块，例如：")
        print("  - Ubuntu/Debian: sudo apt-get install python3-tk")
        print("  - Windows / macOS: 使用官方 Python 安装包时请勾选 tcl/tk 组件。")
        print("=" * 60)
        core = DownloaderCore()
        env = core.detect_environment()
        print(f"运行目录: {env['base_dir']}")
        for m in env["messages"]:
            print(m)
        for w in env["warnings"]:
            print(w)
        return 1

    root = tk.Tk()
    app = YTDownloaderGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())