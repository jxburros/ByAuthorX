"""Shared identity tokens and native Qt presentation; document styles stay authored."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .fonts import register_bundled_fonts

BRAND_ROOT = Path(__file__).parent / 'resources' / 'brand'
TOKENS = json.loads((BRAND_ROOT / 'tokens.json').read_text(encoding='utf-8'))
COLORS = TOKENS['color']
# Input and button outlines identify controls, so they need 3:1 against the
# surfaces they sit on (WCAG 1.4.11); the decorative rule colour is lighter.
COLORS.setdefault('control', '#857C6D')


def contrast_ratio(first, second):
    def luminance(value):
        channels = [QColor(value).redF(), QColor(value).greenF(), QColor(value).blueF()]
        r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def prefers_high_contrast(app):
    # Keep each wrapper referenced: a discarded temporary invalidates the hints object.
    hints = app.styleHints()
    accessibility = getattr(hints, 'accessibility', None)
    if accessibility is None:
        return False
    accessibility = accessibility()
    preference = accessibility.contrastPreference()
    return preference == Qt.ContrastPreference.HighContrast


def app_icon():
    icon = QIcon()
    # The simplified micro mark avoids losing the loop at small desktop sizes.
    for size in (16, 20, 24, 32, 48, 64, 96, 128, 256, 512, 1024):
        icon.addFile(str(BRAND_ROOT / f'app-{size}.png'), QSize(size, size))
    return icon


DARK_COLORS = {
    **COLORS,
    'paper': '#1D2220',
    'surface': '#262D29',
    'ink': '#F2EFE5',
    'muted': '#BABDB4',
    'rule': '#535E56',
    'control': '#929E94',
    'green': '#35784C',
    'greenText': '#77BF8A',
}


def interface_font(*, plain=False):
    """The application font: the body face first, then the fallback face for the scripts it lacks.

    ``plain`` sets the fallback face alone, for readers who asked for high contrast.
    """
    body, fallback = TOKENS['font']['body'], TOKENS['font']['fallback']
    font = QFont(fallback if plain else body, 11)
    font.setFamilies([fallback] if plain else [body, fallback])
    return font


def configure_application(app: QApplication):
    register_bundled_fonts()
    app.setWindowIcon(app_icon())
    if prefers_high_contrast(app):
        # The platform high-contrast theme outranks brand colours, and a plain face reads best.
        app.setFont(interface_font(plain=True))
        app.setStyleSheet('')
        return
    mode = QSettings('ByAuthorX', 'ByAuthorX').value('appearance/theme', 'system')
    dark = mode == 'dark' or (mode == 'system' and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    colors = DARK_COLORS if dark else COLORS
    app.setProperty('byaxTheme', 'dark' if dark else 'light')

    app.setFont(interface_font())
    palette = QPalette()
    for role, color in (
        (QPalette.Window, colors['paper']),
        (QPalette.WindowText, colors['ink']),
        (QPalette.Base, colors['surface']),
        (QPalette.AlternateBase, colors['paper']),
        (QPalette.Text, colors['ink']),
        (QPalette.Button, colors['surface']),
        (QPalette.ButtonText, colors['ink']),
        (QPalette.Highlight, colors['greenText']),
        (QPalette.HighlightedText, '#18231B' if dark else '#FFFFFF'),
        (QPalette.Link, colors['greenText']),
        (QPalette.ToolTipBase, colors['surface']),
        (QPalette.ToolTipText, colors['ink']),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(colors['muted']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(colors, 'dark' if dark else 'light'))


def rule_url(name, theme='light'):
    """A Vixl-authored hand-inked rule for the stylesheet (Qt picks the @2x file on high-DPI displays)."""
    return f'url("{(BRAND_ROOT / "ui" / f"{name}-{theme}.png").as_posix()}")'


def stylesheet(colors=None, theme='light'):
    c = colors or COLORS
    selected_text = '#18231B' if theme == 'dark' else '#FFFFFF'
    control, panel = TOKENS['radius']['control'], TOKENS['radius']['panel']
    # The brand faces cover Latin only, so each role names the fallback face after its own: a character
    # they lack (Cyrillic, Greek, Latin Extended-B) is set in Manrope rather than in whatever the system picks.
    display, annotation, counter = (
        f'"{TOKENS["font"][role]}", "{TOKENS["font"]["fallback"]}"'
        for role in ('display', 'annotation', 'counter')
    )
    return f"""
        QMainWindow, QDialog {{ background: {c['paper']}; color: {c['ink']}; }}
        QMenuBar, QToolBar, QStatusBar {{ background: {c['surface']}; color: {c['ink']};
            border: 0; padding: 4px; }}
        QToolBar {{ spacing: 6px; background-image: {rule_url('rule-h', theme)}; background-repeat: repeat-x;
            background-position: bottom left; background-origin: border; }}
        QToolBar::separator {{ width: 7px; margin: 6px 6px; background-image: {rule_url('rule-v', theme)};
            background-repeat: no-repeat; background-position: center; }}
        QStatusBar {{ background-image: {rule_url('rule-h', theme)}; background-repeat: repeat-x;
            background-position: top left; background-origin: border; }}
        QMenu {{ background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['rule']};
            icon-size: 20px; }}
        QMenu::item {{ padding: 8px 24px 8px 12px; }}
        QMenu::separator {{ height: 6px; margin: 4px 10px; background-image: {rule_url('rule-h', theme)};
            background-repeat: repeat-x; background-position: center left; }}
        QMenu::icon {{ padding-left: 8px; }}
        QMenu::item:selected, QMenuBar::item:selected {{ background: {c['paper']}; color: {c['greenText']}; }}
        QToolButton, QPushButton {{ background: {c['surface']}; color: {c['ink']};
            border: 1px solid {c['control']}; border-radius: {control}px; padding: 8px 12px; }}
        QToolButton:hover, QPushButton:hover {{ background: {c['paper']}; border-color: {c['green']}; }}
        QToolBar QToolButton {{ border-color: transparent; padding: 8px 10px; }}
        QToolBar QToolButton:hover {{ background: {c['paper']}; border-color: {c['rule']}; }}
        QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{
            background: {c['paper']}; color: {c['greenText']}; border-color: {c['greenText']}; }}
        QToolButton:pressed, QPushButton:pressed, QToolButton:checked {{
            background: {c['greenText']}; color: {selected_text}; }}
        QPushButton[primary="true"] {{ background: {c['green']}; color: white; border-color: {c['green']}; }}
        QPushButton[primary="true"]:hover {{ background: {c['greenText']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background: {c['paper']}; }}
        QDockWidget {{ color: {c['ink']}; font-family: {display}; font-size: 16px; }}
        QDockWidget::title {{ background: {c['surface']}; padding: 12px; background-image: {rule_url('rule-h', theme)};
            background-repeat: repeat-x; background-position: bottom left; background-origin: border; }}
        QTreeWidget, QListWidget {{ border: 0; background: {c['surface']}; color: {c['ink']}; padding: 8px; }}
        QTreeWidget::item, QListWidget::item {{ padding: 8px; }}
        QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c['greenText']}; color: {selected_text}; }}
        QTabWidget::pane {{ border: 1px solid {c['rule']}; border-radius: {panel}px; background: {c['surface']}; }}
        QTabBar::tab {{ padding: 8px 16px 2px 16px; color: {c['muted']}; background: {c['paper']};
            border-bottom: 8px solid transparent; }}
        QTabBar::tab:selected {{ color: {c['greenText']}; background: {c['surface']};
            border-image: {rule_url('tab-underline', theme)} 0 0 8 0 stretch stretch; border-bottom: 8px solid; }}
        QTextEdit {{ background: {c['surface']}; color: {c['ink']};
            border: 2px solid {c['control']}; border-radius: 8px; padding: 24px;
            selection-background-color: {c['greenText']}; selection-color: {selected_text}; }}
        QTextEdit:focus {{ border: 2px solid {c['greenText']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
            background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['control']};
            border-radius: {control}px; padding: 4px 8px; selection-background-color: {c['greenText']};
            selection-color: {selected_text}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 28px; }}
        QAbstractScrollArea:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QToolButton:focus, QPushButton:focus {{ border: 2px solid {c['greenText']}; }}
        QScrollArea#pagePreview {{ background: {c['paper']}; border: 0; }}
        QLabel#brandTagline {{ font-family: {display}; font-size: 32px; color: {c['ink']}; }}
        QWidget#writingWorkspace {{ background: {c['paper']}; }}
        QLabel#documentTitle {{ font-family: {display}; font-size: 19px; color: {c['ink']}; }}
        QLabel#documentState, QLabel#workspaceView {{ color: {c['muted']}; font-size: 12px; }}
        QWidget#emptyPanel {{ background: {c['surface']}; }}
        QLabel#emptyHeading {{ font-family: {display}; font-size: 18px; color: {c['ink']}; }}
        QLabel#emptyDescription {{ font-family: {annotation}; color: {c['ink']}; font-size: 16px; }}
        QLabel#statusCounter {{ font-family: {counter}; color: {c['ink']}; font-size: 13px; }}
    """


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
