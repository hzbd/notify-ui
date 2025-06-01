import sys
import json
import platform
import subprocess
import threading
import signal
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox, QLabel,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolTip,
    QDialog, QCheckBox, QFormLayout
)
from PySide6.QtGui import (
    QAction, QIcon, QPixmap, QFont, QColor, QPalette,
    QCursor, QPainter
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

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setWindowFlags(
            self.windowFlags() |
            Qt.WindowStaysOnTopHint |  # 始终在最上层
            Qt.Dialog |               # 作为对话框类型
            Qt.WindowCloseButtonHint  # 有关闭按钮
        )
        self.setFixedSize(300, 150)

        self.settings = QSettings("PiApp", "NotificationApp")

        layout = QFormLayout(self)

        self.sound_checkbox = QCheckBox("启用通知音效")
        self.sound_checkbox.setChecked(self.settings.value("sound_enabled", True, type=bool))
        layout.addRow("音效设置:", self.sound_checkbox)

        button_layout = QHBoxLayout()
        ok_button = QPushButton("确定")
        ok_button.clicked.connect(self.save_settings)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)

        layout.addRow(button_layout)

    def save_settings(self):
        self.settings.setValue("sound_enabled", self.sound_checkbox.isChecked())
        self.accept()

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

        # 初始化设置
        self.settings = QSettings("PiApp", "NotificationApp")
        self.sound_enabled = self.settings.value("sound_enabled", True, type=bool)

        # 初始化音频
        self.sound_effect = QSoundEffect()
        self.init_sound()

        self.notification_received.connect(self.handle_notification)

        # 加载基础图标
        self.base_icon_black = self.load_icon("pi_black.svg")
        self.base_icon_orange = self.load_icon("pi_orange.svg")
        self.numbered_icons = {}

        self.tray_icon = QSystemTrayIcon(self.app)
        self.update_icon_state()
        self.tray_icon.setToolTip("Pi - 消息通知")

        self.menu = QMenu()

        # 设置菜单项
        self.settings_action = QAction("设置", self.menu)
        self.settings_action.triggered.connect(self.show_settings)

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

    def load_icon(self, filename):
        """加载SVG图标"""
        icon_path = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        print(f"警告: 未找到图标文件 {filename}，将使用默认图标")
        return QIcon.fromTheme("dialog-information")

    def init_sound(self):
        """初始化音效 - 使用本地 alarm.mp3 文件"""
        # 查找 alarm.mp3 文件
        sound_file = self.find_sound_file()
        if sound_file:
            self.sound_effect.setSource(QUrl.fromLocalFile(sound_file))
            self.sound_effect.setVolume(0.5)
            print(f"已加载音效文件: {sound_file}")
        else:
            print("警告: 未找到 alarm.mp3 文件，音效功能将不可用")

    def find_sound_file(self):
        """查找 alarm.mp3 文件"""
        # 在当前目录查找
        local_path = os.path.join(os.path.dirname(__file__), "alarm.wav")
        if os.path.exists(local_path):
            return local_path

        # 在其他可能的位置查找
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
        """播放通知音效"""
        if self.sound_enabled and self.sound_effect.source().isValid():
            print("播放通知音效...")
            self.sound_effect.play()

    def show_settings(self):
        """显示设置对话框"""
        dialog = SettingsDialog()
        if dialog.exec() == QDialog.Accepted:
            self.sound_enabled = self.settings.value("sound_enabled", True, type=bool)

    def create_numbered_icon(self, count):
        """创建带数字的图标"""
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
        """更新图标状态"""
        icon = self.create_numbered_icon(self.unread_count)
        self.tray_icon.setIcon(icon)

    def mark_all_as_read(self):
        """标记所有消息为已读"""
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
        print(f"服务器运行在端口 {server_address[1]}")

        try:
            while self.server_running:
                self.server.handle_request()
        except OSError:
            pass

    def signal_handler(self, signum, frame):
        print("收到终止信号，正在关闭...")
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

        # 播放通知音效
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
        self.tray_icon.hide()
        self.app.quit()
        print("应用程序已关闭")

if __name__ == "__main__":
    app = StatusBarApp()
    sys.exit(app.app.exec())
