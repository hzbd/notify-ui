import sys
import json
import platform
import subprocess
import threading
import signal
import os
import requests
import logging
import time
from logging.handlers import RotatingFileHandler
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox, QLabel,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolTip,
    QDialog, QCheckBox, QFormLayout, QLineEdit, QSpinBox, QGroupBox,
    QTextBrowser, QTextEdit
)
from PySide6.QtGui import (
    QAction, QIcon, QPixmap, QFont, QColor, QPalette,
    QCursor, QPainter, QTextCursor
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSize, Signal, QObject,
    QEvent, QSettings, QUrl
)
from PySide6.QtMultimedia import QSoundEffect
from datetime import datetime
from functools import partial

class NotificationHandler(BaseHTTPRequestHandler):
    def _set_response(self, content_type="text/plain"):
        self.send_response(200)
        self.send_header("Content-type", content_type)
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)

        try:
            data = json.loads(post_data.decode('utf-8'))
            title = data.get('title', '通知')
            message = data.get('message', '这是一条通知消息')
            timestamp = data.get('timestamp', None)

            self.server.status_bar_app.notification_received.emit(title, message, timestamp)

            response = json.dumps({
                "status": "success",
                "message": "通知已发送",
                "data": {
                    "title": title,
                    "message": message,
                    "timestamp": timestamp
                }
            })
            self._set_response("application/json")
            self.wfile.write(response.encode('utf-8'))
        except Exception as e:
            response = json.dumps({
                "status": "error",
                "message": str(e),
                "details": "请确保发送的是有效的JSON格式，包含title和message字段"
            })
            self._set_response("application/json")
            self.wfile.write(response.encode('utf-8'))

    def log_message(self, format, *args):
        return

class NotificationPopup(QWidget):
    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.title = title
        self.message = message
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.SplashScreen
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setup_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fade_out)
        self.timer.start(5000)
        self.opacity = 1.0

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        title_label = QLabel(self.title)
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(12)
        title_label.setFont(title_font)

        message_label = QLabel(self.message)
        message_label.setWordWrap(True)
        message_label.setMaximumWidth(300)

        button_layout = QHBoxLayout()
        view_button = QPushButton("查看")
        view_button.clicked.connect(self.view_notification)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(view_button)
        button_layout.addWidget(close_button)

        main_layout.addWidget(title_label)
        main_layout.addWidget(message_label)
        main_layout.addLayout(button_layout)

        self.setStyleSheet("""
            QWidget {
                background-color: rgba(255, 255, 255, 240);
                border: 1px solid #cccccc;
                border-radius: 5px;
                padding: 10px;
            }
            QLabel {
                color: #333333;
            }
            QPushButton {
                background-color: #4a86e8;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #3a76d8;
            }
        """)
        self.adjustSize()

    def view_notification(self):
        msg_box = QMessageBox()
        msg_box.setWindowTitle(self.title)
        msg_box.setText(self.message)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.exec()
        self.close()

    def fade_out(self):
        self.opacity -= 0.1
        if self.opacity <= 0:
            self.close()
        else:
            palette = self.palette()
            bg_color = palette.color(QPalette.Window)
            bg_color.setAlphaF(self.opacity)
            palette.setColor(QPalette.Window, bg_color)
            self.setPalette(palette)
            QTimer.singleShot(50, self.fade_out)

    def show_at_position(self, position):
        screen_geometry = QApplication.primaryScreen().geometry()
        popup_width = self.width()
        popup_height = self.height()

        x = position.x() - popup_width // 2
        if x < 0:
            x = 0
        elif x + popup_width > screen_geometry.width():
            x = screen_geometry.width() - popup_width

        y = position.y() + 20
        if y + popup_height > screen_geometry.height():
            y = screen_geometry.height() - popup_height

        self.move(x, y)
        self.show()

class LogViewerDialog(QDialog):
    def __init__(self, log_file, parent=None):
        super().__init__(parent)
        self.setWindowTitle("应用运行日志")
        self.setMinimumSize(600, 400)
        self.log_file = log_file
        self.last_position = 0
        self.last_refresh_time = 0
        self.MIN_REFRESH_INTERVAL = 0.5  # 秒
        self.MAX_LOG_LINES = 1000
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 日志显示区域
        self.log_text = QTextBrowser()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 10))
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)

        # 控制按钮
        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.manual_refresh)
        self.auto_refresh_check = QCheckBox("自动刷新")
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.stateChanged.connect(self.toggle_auto_refresh)
        clear_button = QPushButton("清空日志")
        clear_button.clicked.connect(self.clear_log)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)

        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.auto_refresh_check)
        button_layout.addWidget(clear_button)
        button_layout.addStretch()
        button_layout.addWidget(close_button)

        layout.addWidget(self.log_text)
        layout.addLayout(button_layout)

        # 设置定时器用于自动刷新
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.auto_refresh)
        self.refresh_timer.start(1000)

        # 初始加载日志
        self.manual_refresh()

    def toggle_auto_refresh(self, state):
        if state == Qt.Checked:
            self.refresh_timer.start(1000)
        else:
            self.refresh_timer.stop()

    def manual_refresh(self):
        self.last_position = 0
        self.log_text.clear()
        self.auto_refresh()

    def auto_refresh(self):
        """自动刷新日志，只读取新增内容"""
        now = time.time()
        if now - self.last_refresh_time < self.MIN_REFRESH_INTERVAL:
            return
        self.last_refresh_time = now

        try:
            # 检查文件是否存在
            if not os.path.exists(self.log_file):
                # 尝试创建文件
                open(self.log_file, 'a').close()
                return

            current_size = os.path.getsize(self.log_file)
            if current_size < self.last_position:
                # 文件被截断，重置
                self.last_position = 0
                self.log_text.clear()

            if current_size > self.last_position:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    f.seek(self.last_position)
                    new_content = f.read()
                    self.last_position = f.tell()

                    if new_content:
                        # 添加新内容
                        self.log_text.append(new_content)

                        # 限制行数
                        lines = self.log_text.document().blockCount()
                        if lines > self.MAX_LOG_LINES:
                            cursor = QTextCursor(self.log_text.document())
                            cursor.movePosition(QTextCursor.Start)
                            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, lines - self.MAX_LOG_LINES)
                            cursor.removeSelectedText()

                        # 滚动到底部
                        scroll_bar = self.log_text.verticalScrollBar()
                        if scroll_bar:
                            scroll_bar.setValue(scroll_bar.maximum())
        except Exception as e:
            current_content = self.log_text.toPlainText()
            if "读取日志错误" not in current_content[-100:]:  # 只检查最后100个字符
                self.log_text.append(f"读取日志错误: {str(e)}")

    def clear_log(self):
        """清空日志文件"""
        reply = QMessageBox.question(
            self, "确认",
            "确定要清空日志文件吗？此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                with open(self.log_file, "w", encoding="utf-8") as f:
                    f.write("")
                self.last_position = 0
                self.log_text.clear()
            except Exception as e:
                QMessageBox.critical(
                    self, "错误",
                    f"清空日志失败: {str(e)}"
                )

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setWindowFlags(
            self.windowFlags() |
            Qt.WindowStaysOnTopHint |
            Qt.Dialog |
            Qt.WindowCloseButtonHint
        )
        self.setFixedSize(500, 350)

        self.settings = QSettings("PiApp", "NotificationApp")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)  # 设置边距

        # 基本设置组
        basic_group = QGroupBox("基本设置")
        basic_layout = QFormLayout(basic_group)
        basic_layout.setFormAlignment(Qt.AlignLeft)  # 设置表单左对齐
        basic_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)  # 允许字段扩展

        self.sound_checkbox = QCheckBox("启用通知音效")
        self.sound_checkbox.setChecked(self.settings.value("sound_enabled", True, type=bool))
        basic_layout.addRow("音效设置:", self.sound_checkbox)

        # API设置组
        api_group = QGroupBox("远端API设置")
        api_layout = QFormLayout(api_group)
        api_layout.setFormAlignment(Qt.AlignLeft)  # 设置表单左对齐
        api_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)  # 允许字段扩展

        self.api_enabled_checkbox = QCheckBox("启用远端API获取消息")
        self.api_enabled_checkbox.setChecked(self.settings.value("api_enabled", False, type=bool))
        self.api_enabled_checkbox.stateChanged.connect(self.toggle_api_settings)
        api_layout.addRow("启用状态:", self.api_enabled_checkbox)

        self.api_url_edit = QLineEdit()
        self.api_url_edit.setPlaceholderText("https://example.com/api/notifications")
        self.api_url_edit.setText(self.settings.value("api_url", ""))
        api_layout.addRow("API URL:", self.api_url_edit)

        self.poll_interval_spin = QSpinBox()
        self.poll_interval_spin.setRange(1, 86400)  # 1秒到24小时
        self.poll_interval_spin.setValue(self.settings.value("poll_interval", 300, type=int))
        self.poll_interval_spin.setSuffix(" 秒")
        api_layout.addRow("轮询间隔:", self.poll_interval_spin)

        self.test_button = QPushButton("测试连接")
        self.test_button.setFixedWidth(100)

        # 测试结果显示
        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)
        self.test_result_label.setAlignment(Qt.AlignLeft)  # 左对齐测试结果

        # 加载动画
        self.loading_label = QLabel()
        self.loading_label.setAlignment(Qt.AlignLeft)  # 左对齐加载动画
        self.loading_label.hide()

        # 如果没有加载动画资源，可以使用文字代替
        self.loading_text = QLabel("测试中...")
        self.loading_text.setAlignment(Qt.AlignLeft)  # 左对齐加载文字
        self.loading_text.hide()

        test_layout = QHBoxLayout()
        test_layout.addWidget(self.test_button)
        test_layout.addStretch()
        api_layout.addRow("", test_layout)

        result_layout = QHBoxLayout()
        result_layout.addWidget(self.loading_label)
        result_layout.addWidget(self.loading_text)
        result_layout.addWidget(self.test_result_label)
        result_layout.addStretch()
        api_layout.addRow("测试结果:", result_layout)

        # 初始状态设置
        self.toggle_api_settings()

        # 按钮布局
        button_layout = QHBoxLayout()
        ok_button = QPushButton("确定")
        ok_button.clicked.connect(self.save_settings)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addStretch(1)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)

        layout.addWidget(basic_group)
        layout.addWidget(api_group)
        layout.addLayout(button_layout)

        # 连接信号
        self.test_button.clicked.connect(self.test_connection)

        # 设置整体布局的对齐方式
        layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def toggle_api_settings(self):
        enabled = self.api_enabled_checkbox.isChecked()
        self.api_url_edit.setEnabled(enabled)
        self.poll_interval_spin.setEnabled(enabled)
        self.test_button.setEnabled(enabled)

    def test_connection(self):
        api_url = self.api_url_edit.text().strip()
        if not api_url:
            self.show_test_result("请输入API URL", "red")
            return

        self.test_button.setEnabled(False)
        self.test_result_label.clear()
        self.loading_text.show()

        QTimer.singleShot(100, lambda: self._perform_test_connection(api_url))

    def _perform_test_connection(self, api_url):
        try:
            response = requests.get(api_url, timeout=5)
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "notifications" in data:
                        self.show_test_result("连接成功，API格式正确", "green")
                    else:
                        self.show_test_result("连接成功，但返回数据格式不符合预期", "orange")
                except ValueError:
                    self.show_test_result("连接成功，但返回的不是有效JSON", "orange")
            else:
                self.show_test_result(
                    f"连接失败，状态码: {response.status_code}",
                    "red"
                )
        except requests.exceptions.Timeout:
            self.show_test_result("连接超时", "red")
        except requests.exceptions.ConnectionError:
            self.show_test_result("无法连接到服务器", "red")
        except Exception as e:
            self.show_test_result(f"连接测试失败: {str(e)}", "red")
        finally:
            self.test_button.setEnabled(True)
            self.loading_text.hide()

    def show_test_result(self, message, color):
        self.test_result_label.setText(message)
        self.test_result_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def save_settings(self):
        self.settings.setValue("sound_enabled", self.sound_checkbox.isChecked())
        self.settings.setValue("api_enabled", self.api_enabled_checkbox.isChecked())
        self.settings.setValue("api_url", self.api_url_edit.text().strip())
        self.settings.setValue("poll_interval", self.poll_interval_spin.value())
        self.accept()

class APIPoller(QObject):
    notification_fetched = Signal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("PiApp", "NotificationApp")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_api)
        self.setup_polling()

    def setup_polling(self):
        if not self.settings.value("api_enabled", False, type=bool):
            logging.info("远端API功能未启用")
            return

        interval = self.settings.value("poll_interval", 300, type=int)
        self.timer.start(interval * 1000)
        logging.info(f"设置API轮询间隔为 {interval} 秒")

    def update_polling_interval(self, enabled, interval):
        self.timer.stop()

        if enabled:
            self.timer.start(interval * 1000)
            logging.info(f"更新API轮询间隔为 {interval} 秒")
        else:
            logging.info("远端API功能已禁用")

    def poll_api(self):
        if not self.settings.value("api_enabled", False, type=bool):
            return

        api_url = self.settings.value("api_url", "")
        if not api_url:
            logging.warning("未配置API URL，跳过轮询")
            return

        logging.info(f"开始轮询API: {api_url}")
        try:
            response = requests.get(api_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.process_api_response(data)
            else:
                logging.warning(f"API请求失败，状态码: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logging.error(f"API请求错误: {str(e)}")
        except json.JSONDecodeError:
            logging.error("API返回的不是有效JSON数据")

    def process_api_response(self, data):
        notifications = data.get("notifications", [])
        if not notifications:
            logging.info("API返回无新通知")
            return

        logging.info(f"从API获取到 {len(notifications)} 条新通知")
        for notification in notifications:
            title = notification.get("title", "API通知")
            message = notification.get("message", "收到新通知")
            timestamp = notification.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.notification_fetched.emit(title, message, timestamp)

class StatusBarApp(QObject):
    notification_received = Signal(str, str, object)

    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.server = None
        self.server_thread = None
        self.notifications = []
        self.unread_count = 0
        self.server_running = True
        self.popup = None
        self.log_viewer = None

        # 初始化日志系统
        self.setup_logging()

        # 初始化设置
        self.settings = QSettings("PiApp", "NotificationApp")
        self.sound_enabled = self.settings.value("sound_enabled", True, type=bool)

        # 初始化音频
        self.sound_effect = QSoundEffect()
        self.init_sound()

        # 初始化API轮询器
        self.api_poller = APIPoller(self)
        self.api_poller.notification_fetched.connect(self.handle_notification)

        self.notification_received.connect(self.handle_notification)

        # 加载基础图标
        self.base_icon_black = self.load_icon("media/pi_black.svg")
        self.base_icon_orange = self.load_icon("media/pi_orange.svg")
        self.numbered_icons = {}

        self.tray_icon = QSystemTrayIcon(self.app)
        self.update_icon_state()
        self.tray_icon.setToolTip("Pi - 消息通知")

        self.menu = QMenu()

        # 设置菜单项
        self.settings_action = QAction("设置", self.menu)
        self.settings_action.triggered.connect(self.show_settings)

        # 查看日志菜单项
        self.view_log_action = QAction("查看运行日志", self.menu)
        self.view_log_action.triggered.connect(self.show_log_viewer)
        self.menu.addAction(self.view_log_action)
        self.menu.addSeparator()

        self.history_menu = QMenu("消息历史", self.menu)
        self.history_menu.setProperty("_q_menu_sloppy_behavior", False)

        self.mark_read_action = QAction("标记所有为已读", self.menu)
        self.mark_read_action.triggered.connect(self.mark_all_as_read)

        self.menu.addAction(self.settings_action)
        self.menu.addMenu(self.history_menu)
        self.menu.addAction(self.mark_read_action)
        self.menu.addSeparator()
        self.quit_action = QAction("退出", self.menu)
        self.quit_action.triggered.connect(self.quit)
        self.menu.addAction(self.quit_action)

        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        self.start_server_thread()
        self.update_history_menu()

        QToolTip.setFont(QFont("PingFang SC", 10))
        QToolTip.setPalette(QPalette(QColor(240, 240, 240), QColor(80, 80, 80)))

        # 立即执行一次API轮询
        QTimer.singleShot(5000, self.api_poller.poll_api)

    def setup_logging(self):
        log_dir = os.path.expanduser("~/.pi_notification")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        log_file = os.path.join(log_dir, "app.log")

        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        file_handler = RotatingFileHandler(
            log_file, maxBytes=1024*1024, backupCount=5
        )
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        sys.stdout = self.LoggerWriter(self.logger.info)
        sys.stderr = self.LoggerWriter(self.logger.error)

    class LoggerWriter:
        def __init__(self, log_func):
            self.log_func = log_func

        def write(self, message):
            if message.strip():
                self.log_func(message.strip())

        def flush(self):
            pass

    def show_log_viewer(self):
        log_file = os.path.expanduser("~/.pi_notification/app.log")

        if not os.path.exists(log_file):
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception as e:
                QMessageBox.information(
                    None, "日志", f"无法创建日志文件: {str(e)}"
                )
                return

        if self.log_viewer is None:
            self.log_viewer = LogViewerDialog(log_file)
            self.log_viewer.finished.connect(self.on_log_viewer_closed)

        self.log_viewer.show()
        self.log_viewer.activateWindow()
        self.log_viewer.raise_()

    def on_log_viewer_closed(self):
        self.log_viewer = None

    def load_icon(self, filename):
        icon_path = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        logging.warning(f"未找到图标文件 {filename}，将使用默认图标")
        return QIcon.fromTheme("dialog-information")

    def init_sound(self):
        sound_file = self.find_sound_file()
        if sound_file:
            self.sound_effect.setSource(QUrl.fromLocalFile(sound_file))
            self.sound_effect.setVolume(0.5)
            logging.info(f"已加载音效文件: {sound_file}")
        else:
            logging.warning("未找到 alarm.wav 文件，音效功能将不可用")

    def find_sound_file(self):
        local_path = os.path.join(os.path.dirname(__file__), "media/alarm.wav")
        if os.path.exists(local_path):
            return local_path

        search_paths = [
            os.path.expanduser("~/alarm.wav"),
            "/usr/share/sounds/alarm.wav",
            "/usr/local/share/sounds/alarm.wav"
        ]

        for path in search_paths:
            if os.path.exists(path):
                return path

        return None

    def play_notification_sound(self):
        if self.sound_enabled and self.sound_effect.source().isValid():
            logging.info("播放通知音效...")
            self.sound_effect.play()

    def show_settings(self):
        dialog = SettingsDialog()
        if dialog.exec() == QDialog.Accepted:
            self.sound_enabled = self.settings.value("sound_enabled", True, type=bool)
            api_enabled = self.settings.value("api_enabled", False, type=bool)
            poll_interval = self.settings.value("poll_interval", 300, type=int)
            self.api_poller.update_polling_interval(api_enabled, poll_interval)

    def create_numbered_icon(self, count):
        if count in self.numbered_icons:
            return self.numbered_icons[count]

        base_icon = self.base_icon_orange if count > 0 else self.base_icon_black
        pixmap = base_icon.pixmap(32, 32)
        if pixmap.isNull():
            return base_icon

        new_pixmap = pixmap.copy()
        painter = QPainter(new_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        font = QFont()
        font.setBold(True)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))

        text = str(count) if count <= 10 else "10+"
        text_rect = painter.fontMetrics().boundingRect(text)
        x = (new_pixmap.width() - text_rect.width()) / 2
        y = (new_pixmap.height() + text_rect.height()) / 2 - 2

        painter.drawText(int(x), int(y), text)
        painter.end()

        self.numbered_icons[count] = QIcon(new_pixmap)
        return self.numbered_icons[count]

    def update_icon_state(self):
        icon = self.create_numbered_icon(self.unread_count)
        self.tray_icon.setIcon(icon)

    def mark_all_as_read(self):
        for notification in self.notifications:
            notification["read"] = True
        self.unread_count = 0
        self.update_icon_state()
        self.update_history_menu()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Context:
            tray_geometry = self.tray_icon.geometry()
            position = tray_geometry.center()
            self.menu.popup(position)

    def start_server_thread(self):
        self.server_thread = threading.Thread(target=self.start_server)
        self.server_thread.daemon = True
        self.server_thread.start()

    def start_server(self):
        server_address = ('', 8000)
        self.server = ThreadingHTTPServer(server_address, NotificationHandler)
        self.server.status_bar_app = self
        logging.info(f"服务器运行在端口 {server_address[1]}")

        try:
            while self.server_running:
                self.server.handle_request()
        except OSError:
            pass

    def signal_handler(self, signum, frame):
        logging.info("收到终止信号，正在关闭...")
        self.quit()

    def handle_notification(self, title, message, timestamp):
        self.notifications.append({
            "title": title,
            "message": message,
            "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "read": False
        })
        self.unread_count += 1

        if len(self.notifications) > 20:
            removed = self.notifications.pop(0)
            if not removed["read"]:
                self.unread_count -= 1

        self.update_history_menu()
        self.update_icon_state()

        self.play_notification_sound()

        if platform.system() == "Darwin":
            subprocess.run(['osascript', '-e',
                           f'display notification "{message}" with title "{title}"'])
        else:
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 5000)
        self.show_popup(title, message)

    def update_history_menu(self):
        QTimer.singleShot(0, self._update_history_menu)

    def _update_history_menu(self):
        self.history_menu.blockSignals(True)
        self.history_menu.clear()

        if not self.notifications:
            action = QAction("消息数量: 0", self.history_menu)
            action.setEnabled(False)
            self.history_menu.addAction(action)
            self.history_menu.addSeparator()
            action = QAction("暂无消息", self.history_menu)
            action.setEnabled(False)
            self.history_menu.addAction(action)
        else:
            count_text = f"消息数量: {len(self.notifications)}"
            if self.unread_count > 0:
                count_text += f" (未读: {self.unread_count})"
            count_action = QAction(count_text, self.history_menu)
            count_action.setEnabled(False)
            self.history_menu.addAction(count_action)
            self.history_menu.addSeparator()

            for notification in reversed(self.notifications):
                title = notification["title"]
                msg = notification["message"][:20] + ("..." if len(notification["message"]) > 20 else "")
                timestamp = notification["timestamp"]
                read_status = "" if notification["read"] else " [未读]"
                display_text = f"{title}: {msg}{read_status}"
                if timestamp:
                    display_text += f" [{timestamp}]"

                action = QAction(display_text, self.history_menu)
                action.setData(notification)
                action.triggered.connect(partial(self.show_notification_detail, notification))
                action.hovered.connect(self.on_action_hovered)

                if not notification["read"]:
                    font = action.font()
                    font.setBold(True)
                    action.setFont(font)

                self.history_menu.addAction(action)

        self.history_menu.blockSignals(False)

    def on_action_hovered(self):
        action = self.sender()
        if not action or not action.data():
            return

        notification = action.data()
        pos = QCursor.pos()

        tooltip_content = f"<b>{notification['title']}</b>"
        if not notification["read"]:
            tooltip_content += " <font color='red'>[未读]</font>"
        if notification['timestamp']:
            tooltip_content += f"<br><small>{notification['timestamp']}</small>"
        tooltip_content += f"<p>{notification['message']}</p>"

        QToolTip.showText(pos, tooltip_content, self.history_menu)

    def show_notification_detail(self, notification):
        if not notification["read"]:
            notification["read"] = True
            self.unread_count -= 1
            self.update_icon_state()
            self.update_history_menu()

        msg_box = QMessageBox()
        msg_box.setWindowTitle(notification["title"])
        detail = notification["message"]
        if notification["timestamp"]:
            detail += f"\n\n时间: {notification['timestamp']}"
        msg_box.setText(detail)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.exec()

    def show_popup(self, title, message):
        QTimer.singleShot(0, lambda: self._show_popup(title, message))

    def _show_popup(self, title, message):
        if self.popup and self.popup.isVisible():
            self.popup.close()
        self.popup = NotificationPopup(title, message)
        tray_pos = self.tray_icon.geometry().center()
        self.popup.show_at_position(tray_pos)

    def quit(self):
        if self.server_running:
            self.server_running = False
            self.server.socket.close()
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(1)
        if self.popup:
            self.popup.close()
        if self.log_viewer:
            self.log_viewer.close()
        self.tray_icon.hide()
        self.app.quit()
        logging.info("应用程序已关闭")

if __name__ == "__main__":
    app = StatusBarApp()
    sys.exit(app.app.exec())
