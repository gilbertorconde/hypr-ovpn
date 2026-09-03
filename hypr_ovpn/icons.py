"""Programmatically drawn padlock tray icons — no icon-theme dependency."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

COLOR_IDLE = QColor("#8a8f98")
COLOR_BUSY = QColor("#e8a33d")
COLOR_OK = QColor("#4caf50")
COLOR_ERR = QColor("#d9534f")


def _padlock(color: QColor, open_shackle: bool, size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0

    # Shackle
    pen = QPen(color, 7 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    shackle_rect = QRectF(17 * s, 6 * s, 30 * s, 34 * s)
    if open_shackle:
        p.drawArc(shackle_rect, 0 * 16, 200 * 16)
    else:
        p.drawArc(shackle_rect, 0 * 16, 180 * 16)

    # Body
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(10 * s, 27 * s, 44 * s, 32 * s), 7 * s, 7 * s)

    # Keyhole
    p.setBrush(QColor(255, 255, 255, 230))
    p.drawEllipse(QRectF(28 * s, 36 * s, 8 * s, 8 * s))
    p.drawRoundedRect(QRectF(30 * s, 42 * s, 4 * s, 10 * s), 2 * s, 2 * s)
    p.end()
    return QIcon(pm)


def icon_idle() -> QIcon:
    return _padlock(COLOR_IDLE, open_shackle=True)


def icon_busy() -> QIcon:
    return _padlock(COLOR_BUSY, open_shackle=True)


def icon_busy_dim() -> QIcon:
    return _padlock(QColor("#a3781f"), open_shackle=True)


def icon_connected() -> QIcon:
    return _padlock(COLOR_OK, open_shackle=False)


def icon_error() -> QIcon:
    return _padlock(COLOR_ERR, open_shackle=True)
