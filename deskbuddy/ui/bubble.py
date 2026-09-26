"""Speech bubble that floats above Buddy."""

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class Bubble(QWidget):
    """Little speech bubble that floats above the buddy."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.text = ""
        self.font_ = QFont("Sans", 10)
        self._hide = QTimer(self, singleShot=True, timeout=self.hide)

    def show_text(self, text, ms):
        self.text = text
        screen = QGuiApplication.primaryScreen().availableGeometry()
        fm = QFontMetrics(self.font_)
        w = min(320, screen.width() - 24, fm.horizontalAdvance(text) + 32)
        bounds = fm.boundingRect(0, 0, w - 28, 1000, Qt.TextFlag.TextWordWrap, text)
        self.resize(w, bounds.height() + 32)
        self.show()
        self.update()
        self._hide.start(ms)

    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            top = buddy.stack_top()
            self.move(x, max(screen.top() + 8, top - self.height()))
            self._tail_x = buddy.char_x() - x

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 12), 14, 14)
        tail = QPainterPath()
        mid = min(self.width() - 22, max(22, getattr(self, "_tail_x", self.width() / 2)))
        tail.moveTo(mid - 7, self.height() - 13)
        tail.lineTo(mid, self.height() - 2)
        tail.lineTo(mid + 7, self.height() - 13)
        path = path.united(tail)
        p.setPen(QPen(QColor(40, 50, 90), 1.5))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawPath(path)
        p.setFont(self.font_)
        p.setPen(QColor(30, 30, 40))
        p.drawText(QRectF(14, 6, self.width() - 28, self.height() - 24),
                   Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.text)
