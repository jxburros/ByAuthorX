"""Shared identity tokens and native Qt presentation; document styles stay authored."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .fonts import register_bundled_fonts

BRAND_ROOT = Path(__file__).parent / 'resources' / 'brand'
TOKENS = json.loads((BRAND_ROOT / 'tokens.json').read_text(encoding='utf-8'))
COLORS = TOKENS['color']


def app_icon():
    icon = QIcon()
    # The simplified micro mark avoids losing the loop at small desktop sizes.
    for size in (16, 20, 24, 32, 48, 64, 96, 128, 256, 512, 1024):
        icon.addFile(str(BRAND_ROOT / f'app-{size}.png'), QSize(size, size))
    return icon


def configure_application(app: QApplication):
    register_bundled_fonts()
    app.setFont(QFont(TOKENS['font']['body'], 11))
    app.setWindowIcon(app_icon())
    palette = QPalette()
    for role, color in (
        (QPalette.Window, COLORS['paper']), (QPalette.WindowText, COLORS['ink']),
        (QPalette.Base, COLORS['surface']), (QPalette.AlternateBase, COLORS['paper']),
        (QPalette.Text, COLORS['ink']), (QPalette.Button, COLORS['surface']),
        (QPalette.ButtonText, COLORS['ink']), (QPalette.Highlight, COLORS['greenText']),
        (QPalette.HighlightedText, '#FFFFFF'), (QPalette.Link, COLORS['greenText']),
        (QPalette.ToolTipBase, COLORS['surface']), (QPalette.ToolTipText, COLORS['ink']),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(COLORS['muted']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())


def stylesheet():
    c = COLORS
    control, panel = TOKENS['radius']['control'], TOKENS['radius']['panel']
    return f'''
        QMainWindow, QDialog {{ background: {c['paper']}; color: {c['ink']}; }}
        QMenuBar, QToolBar, QStatusBar {{ background: {c['surface']}; color: {c['ink']};
            border: 0; padding: 8px; }}
        QToolBar {{ border-bottom: 1px solid {c['rule']}; spacing: 8px; }}
        QMenu {{ background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['rule']}; }}
        QMenu::item {{ padding: 8px 24px; }}
        QMenu::item:selected, QMenuBar::item:selected {{ background: {c['paper']}; color: {c['greenText']}; }}
        QToolButton, QPushButton {{ background: {c['surface']}; color: {c['ink']};
            border: 1px solid {c['rule']}; border-radius: {control}px; padding: 8px 12px; }}
        QToolButton:hover, QPushButton:hover {{ background: {c['paper']}; border-color: {c['green']}; }}
        QToolButton:pressed, QPushButton:pressed, QToolButton:checked {{
            background: {c['greenText']}; color: white; }}
        QPushButton[primary="true"] {{ background: {c['green']}; color: white; border-color: {c['green']}; }}
        QPushButton[primary="true"]:hover {{ background: {c['greenText']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background: {c['paper']}; }}
        QDockWidget {{ color: {c['muted']}; font-weight: 600; }}
        QDockWidget::title {{ background: {c['paper']}; padding: 12px 8px; }}
        QTreeWidget, QListWidget {{ border: 0; background: {c['surface']}; color: {c['ink']}; padding: 8px; }}
        QTreeWidget::item, QListWidget::item {{ padding: 8px; }}
        QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c['greenText']}; color: white; }}
        QTabWidget::pane {{ border: 1px solid {c['rule']}; border-radius: {panel}px; background: {c['surface']}; }}
        QTabBar::tab {{ padding: 8px 16px; color: {c['muted']}; background: {c['paper']}; }}
        QTabBar::tab:selected {{ color: {c['greenText']}; background: {c['surface']};
            border-bottom: 2px solid {c['greenText']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
            background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['rule']};
            border-radius: {control}px; padding: 4px 8px; selection-background-color: {c['greenText']};
            selection-color: white; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 28px; }}
        QAbstractScrollArea:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QToolButton:focus, QPushButton:focus {{ border: 2px solid {c['greenText']}; }}
        QScrollArea#pagePreview {{ background: {c['paper']}; border: 0; }}
        QLabel#brandTagline {{ font-family: "IM Fell English"; font-size: 32px; color: {c['ink']}; }}
    '''


class BrandGraphic(QWidget):
    """Aspect-preserving, high-DPI drawing of approved exports, without SVG Tiny losses."""
    def __init__(self, filename, width, parent=None, clear_space=0):
        super().__init__(parent)
        self.pixmap = QPixmap(str(BRAND_ROOT / filename))
        if self.pixmap.isNull():
            raise RuntimeError(f'Missing brand graphic: {filename}')
        height = round(width * self.pixmap.height() / self.pixmap.width())
        self.art_rect = QRectF(clear_space, clear_space, width, height)
        self.setFixedSize(width + clear_space * 2, height + clear_space * 2)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.art_rect, self.pixmap, QRectF(self.pixmap.rect()))
