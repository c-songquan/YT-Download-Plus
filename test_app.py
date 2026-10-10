#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单元测试与逻辑验证脚本：
全面测试 DownloaderCore 与 GUI 组件逻辑、命令构建、文件名过滤、动态目录检测及 Cookie 处理。
"""

import os
import sys
import unittest
import tempfile
import shutil
from unittest.mock import MagicMock, patch

# 将项目目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main


class TestFilenameSanitization(unittest.TestCase):
    """测试文件名过滤功能"""

    def test_illegal_characters_replaced(self):
        raw = 'My/Playlist: "The Best" <2026> | Top * Hits?'
        expected = 'My_Playlist_ _The Best_ _2026_ _ Top _ Hits_'
        self.assertEqual(main.sanitize_filename(raw), expected)

    def test_windows_reserved_characters(self):
        raw = r'Folder\With/Slash:Colon*Star?Question"Quote<Less>Greater|Pipe'
        result = main.sanitize_filename(raw)
        for char in r'\/*?:"<>|':
            self.assertNotIn(char, result)

    def test_leading_trailing_dots_and_spaces(self):
        raw = " ...  Song Title ... "
        self.assertEqual(main.sanitize_filename(raw), "Song Title")

    def test_empty_string(self):
        self.assertEqual(main.sanitize_filename(""), "Untitled")
        self.assertEqual(main.sanitize_filename("   "), "Untitled")


class TestBaseDirAndEnvironment(unittest.TestCase):
    """测试工作目录动态检测与 ffmpeg/ffprobe 检测"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_normal_base_dir(self):
        with patch.object(sys, 'frozen', False, create=True):
            base = main.get_base_dir()
            self.assertTrue(os.path.isdir(base))

    def test_pyinstaller_frozen_base_dir(self):
        fake_exe = os.path.join(self.test_dir, "myapp.exe")
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', fake_exe):
            base = main.get_base_dir()
            self.assertEqual(base, self.test_dir)

    def test_environment_detect_with_local_binaries(self):
        # 创建模拟的 ffmpeg.exe 和 ffprobe.exe
        exe_suffix = ".exe" if sys.platform.startswith("win") else ""
        open(os.path.join(self.test_dir, f"ffmpeg{exe_suffix}"), "w").close()
        open(os.path.join(self.test_dir, f"ffprobe{exe_suffix}"), "w").close()
        open(os.path.join(self.test_dir, f"yt-dlp{exe_suffix}"), "w").close()

        core = main.DownloaderCore(base_dir=self.test_dir)
        report = core.detect_environment()

        self.assertTrue(report["ffmpeg_in_base"])
        self.assertTrue(report["ffprobe_in_base"])
        self.assertTrue(report["ytdlp_in_base"])
        self.assertEqual(len(report["warnings"]), 0)
        self.assertEqual(core.build_ffmpeg_args(), ["--ffmpeg-location", self.test_dir])

    def test_environment_detect_missing_binaries(self):
        empty_dir = tempfile.mkdtemp()
        try:
            with patch("shutil.which", return_value=None):
                core = main.DownloaderCore(base_dir=empty_dir)
                report = core.detect_environment()
                self.assertFalse(report["ffmpeg_in_base"])
                self.assertFalse(report["ffprobe_in_base"])
                self.assertFalse(report["ffmpeg_in_path"])
                # 必须产生警告信息
                self.assertTrue(any("未在运行目录检测到" in w for w in report["warnings"]))
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)


class TestDownloaderCoreLogic(unittest.TestCase):
    """测试核心业务调度逻辑"""

    def setUp(self):
        self.core = main.DownloaderCore()

    def test_resolve_ytdlp_command(self):
        cmd = self.core.resolve_ytdlp_command()
        self.assertIsInstance(cmd, list)
        self.assertTrue(len(cmd) >= 1)

    def test_run_command_mock(self):
        logs = []
        def log_cb(msg):
            logs.append(msg)

        ret = self.core.run_command([sys.executable, "-c", "print('Hello yt-dlp test')"], log_cb)
        self.assertEqual(ret, 0)
        self.assertTrue(any("Hello yt-dlp test" in line for line in logs))

    def test_cancel_process(self):
        logs = []
        def log_cb(msg):
            logs.append(msg)

        # 启动一个延时进程并在子线程中快速取消
        import time
        import threading
        t = threading.Thread(target=lambda: (time.sleep(0.1), self.core.cancel()))
        t.start()
        ret = self.core.run_command([sys.executable, "-c", "import time; time.sleep(5)"], log_cb)
        t.join()
        self.assertTrue(self.core.is_cancelled)


class TestGUIComponentsMocked(unittest.TestCase):
    """通过 Mock 验证 GUI 页面生命周期与交互逻辑"""

    def setUp(self):
        self.mock_root = MagicMock()
        self.mock_root.clipboard_get.return_value = "https://www.youtube.com/playlist?list=PLtest123"

    def _get_mock_gui(self):
        class FakeVar:
            def __init__(self, value=""):
                self.val = value
            def get(self):
                return self.val
            def set(self, v):
                self.val = v

        mock_tk = MagicMock()
        mock_tk.StringVar = lambda value="": FakeVar(value)
        mock_tk.END = "end"
        mock_tk.NORMAL = "normal"
        mock_tk.DISABLED = "disabled"
        mock_tk.X = "x"
        mock_tk.BOTH = "both"
        mock_tk.LEFT = "left"
        mock_tk.RIGHT = "right"
        mock_tk.W = "w"
        mock_tk.EW = "ew"

        mock_ttk = MagicMock()
        mock_scrolledtext = MagicMock()
        mock_filedialog = MagicMock()
        mock_msgbox = MagicMock()

        with patch.object(main, "tk", mock_tk), \
             patch.object(main, "ttk", mock_ttk), \
             patch.object(main, "scrolledtext", mock_scrolledtext), \
             patch.object(main, "filedialog", mock_filedialog), \
             patch.object(main, "messagebox", mock_msgbox):
            gui = main.YTDownloaderGUI(self.mock_root)
            return gui

    def test_gui_initialization_and_actions(self):
        gui = self._get_mock_gui()

        # 验证在主线程轮询了 _check_environment_on_start
        self.mock_root.protocol.assert_called_with("WM_DELETE_WINDOW", gui._on_close)

        # 测试粘贴剪贴板
        gui._paste_url()
        self.assertEqual(gui.url_var.get(), "https://www.youtube.com/playlist?list=PLtest123")

        # 测试格式切换联动（音频模式）
        gui.type_var.set("仅音频 (Audio)")
        gui._update_format_options()
        gui.format_cb.config.assert_called_with(values=["best (原始最佳)", "mp3", "wav", "m4a"])

        # 测试格式切换联动（视频模式）
        gui.type_var.set("音视频 (Video)")
        gui._update_format_options()
        gui.format_cb.config.assert_called_with(values=["best (原始最佳)", "mp4", "mkv"])

        # 测试 Cookie 模式：无 Cookie
        gui.cookie_mode_var.set("none")
        self.assertIsNone(gui._get_cookie_file())

        # 测试 Cookie 模式：直接粘贴文本
        gui.cookie_mode_var.set("text")
        gui.cookie_text_box.get.return_value = "# Netscape HTTP Cookie File\n.youtube.com TRUE / FALSE 1234567890 SID testcookie"
        cookie_path = gui._get_cookie_file()
        self.assertIsNotNone(cookie_path)
        self.assertTrue(os.path.isfile(cookie_path))
        with open(cookie_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("SID testcookie", content)

        # 测试关闭窗口时临时 Cookie 自动清理
        gui._on_close()
        self.assertFalse(os.path.exists(cookie_path))

    def test_playlist_mode_workflow(self):
        """测试模式 A：提取歌单信息、创建专属目录、生成链接列表文本并构建下载命令"""
        gui = self._get_mock_gui()
        temp_dir = tempfile.mkdtemp()
        try:
            executed_commands = []
            def fake_run_command(cmd, log_cb, cwd=None):
                executed_commands.append(cmd)
                return 0

            gui.core.run_command = fake_run_command

            # 模拟 subprocess.Popen 的返回值
            mock_proc_meta = MagicMock()
            mock_proc_meta.stdout = MagicMock()
            mock_proc_meta.stdout.readline.side_effect = [
                '{"playlist_title": "Jay Chou Best Hits", "id": "123"}\n',
                ''
            ]

            mock_proc_urls = MagicMock()
            mock_proc_urls.stdout = MagicMock()
            mock_proc_urls.stdout.readline.side_effect = [
                'https://www.youtube.com/watch?v=song1\n',
                'https://www.youtube.com/watch?v=song2\n',
                ''
            ]

            with patch("subprocess.Popen", side_effect=[mock_proc_meta, mock_proc_urls]):
                gui._run_playlist_mode(
                    url="https://www.youtube.com/playlist?list=PL123",
                    save_dir=temp_dir,
                    is_audio=True,
                    fmt="mp3",
                    audio_quality="0",
                    cookie_file=None,
                    ytdlp_cmd=["yt-dlp"],
                    ffmpeg_args=["--ffmpeg-location", temp_dir]
                )

            # 验证专属目录已创建
            target_playlist_dir = os.path.join(temp_dir, "Jay Chou Best Hits")
            self.assertTrue(os.path.isdir(target_playlist_dir))

            # 验证生成的链接列表文本
            list_file = os.path.join(target_playlist_dir, "Jay Chou Best Hits_链接列表.txt")
            self.assertTrue(os.path.isfile(list_file))
            with open(list_file, "r", encoding="utf-8") as f:
                urls = f.read().splitlines()
                self.assertEqual(urls, [
                    "https://www.youtube.com/watch?v=song1",
                    "https://www.youtube.com/watch?v=song2"
                ])

            # 验证最终下载命令包含 -a 链接文件，提取音频和格式参数
            self.assertEqual(len(executed_commands), 1)
            dl_cmd = executed_commands[0]
            self.assertIn("-a", dl_cmd)
            self.assertIn(list_file, dl_cmd)
            self.assertIn("--extract-audio", dl_cmd)
            self.assertIn("--audio-format", dl_cmd)
            self.assertIn("mp3", dl_cmd)
            self.assertIn("--audio-quality", dl_cmd)
            self.assertIn("0", dl_cmd)
            self.assertIn("--ffmpeg-location", dl_cmd)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_single_mode_workflow(self):
        """测试模式 B：下载单个音视频文件命令与目录构建"""
        gui = self._get_mock_gui()
        temp_dir = tempfile.mkdtemp()
        try:
            executed_commands = []
            def fake_run_command(cmd, log_cb, cwd=None):
                executed_commands.append(cmd)
                return 0

            gui.core.run_command = fake_run_command

            gui._run_single_mode(
                url="https://www.youtube.com/watch?v=single_test",
                save_dir=temp_dir,
                is_audio=False,
                fmt="mp4",
                audio_quality="0",
                cookie_file="/path/to/cookies.txt",
                ytdlp_cmd=["yt-dlp"],
                ffmpeg_args=["--ffmpeg-location", temp_dir]
            )

            # 验证 Single_Downloads 目录已创建
            single_dir = os.path.join(temp_dir, "Single_Downloads")
            self.assertTrue(os.path.isdir(single_dir))

            # 验证命令参数
            self.assertEqual(len(executed_commands), 1)
            dl_cmd = executed_commands[0]
            self.assertIn("--cookies", dl_cmd)
            self.assertIn("/path/to/cookies.txt", dl_cmd)
            self.assertIn("--merge-output-format", dl_cmd)
            self.assertIn("mp4", dl_cmd)
            self.assertIn("https://www.youtube.com/watch?v=single_test", dl_cmd)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
