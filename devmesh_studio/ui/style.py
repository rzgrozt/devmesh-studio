from __future__ import annotations

APP_STYLE = r"""
* {
    font-family: Inter, "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #d8dee9;
}

QMainWindow, QWidget {
    background: #0b0f14;
}

QToolTip {
    background: #151b23;
    color: #e6edf3;
    border: 1px solid #2a3441;
    padding: 6px 8px;
}

QFrame#sidebar {
    background: #090d12;
    border-right: 1px solid #1b2430;
}

QLabel#brand {
    color: #f4f7fb;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 1px;
}

QLabel#sidebarMuted {
    color: #667382;
    font-size: 11px;
}

QFrame#sidebarFooter {
    background: #0d141b;
    border: 1px solid #1d2732;
    border-radius: 8px;
}

#nav {
    background: transparent;
    border: none;
    padding: 2px 0;
    outline: 0;
}

#nav::item {
    min-height: 34px;
    margin: 2px 0;
    padding: 0 12px;
    border-radius: 8px;
    color: #9ba7b4;
}

#nav::item:hover {
    background: #111821;
    color: #dce5ee;
}

#nav::item:selected {
    background: #16202b;
    color: #f3f7fb;
    font-weight: 600;
}

QLabel#title {
    font-size: 26px;
    font-weight: 700;
    color: #f4f7fb;
}

QLabel#subtitle {
    color: #7f8b99;
    font-size: 12px;
}

QLabel#sectionTitle {
    font-size: 15px;
    font-weight: 650;
    color: #edf3f8;
}

QLabel#muted {
    color: #7f8b99;
}

QLabel#metric {
    font-size: 25px;
    font-weight: 700;
    color: #f4f7fb;
}

QLabel#success {
    color: #69d58c;
    font-weight: 600;
}

QLabel#warning {
    color: #f0b85c;
    font-weight: 600;
}

QLabel#dangerText {
    color: #ff7b72;
    font-weight: 600;
}

QFrame#card, QFrame#panel, QFrame#metricCard {
    background: #10161d;
    border: 1px solid #202a35;
    border-radius: 10px;
}

QFrame#metricCard {
    min-height: 92px;
}

QFrame#statusCard {
    background: #0e151d;
    border: 1px solid #1f2b37;
    border-radius: 9px;
}

QPushButton {
    background: #151c24;
    border: 1px solid #2a3440;
    border-radius: 7px;
    padding: 7px 12px;
    color: #d9e1e8;
}

QPushButton:hover {
    background: #1b2530;
    border-color: #374555;
}

QPushButton:pressed {
    background: #111821;
}

QPushButton:disabled {
    color: #59636f;
    background: #10151b;
    border-color: #1b242d;
}

QPushButton#primary {
    background: #2563eb;
    border-color: #2f6ff0;
    color: white;
    font-weight: 600;
}

QPushButton#primary:hover {
    background: #2e6ff2;
}

QPushButton#danger {
    background: #34171b;
    border-color: #5a242c;
    color: #ffb3b8;
}

QPushButton#ghost {
    background: transparent;
    border-color: transparent;
    color: #8fa0b2;
}

QPushButton#ghost:hover {
    background: #111821;
    color: #dce6ef;
}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
    background: #0d131a;
    border: 1px solid #25303c;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: #315da8;
    selection-color: white;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {
    border-color: #3d6db5;
}

QPlainTextEdit#diffView, QPlainTextEdit#codeView {
    font-family: "JetBrains Mono", "Fira Code", "Noto Sans Mono", monospace;
    font-size: 12px;
    background: #090e13;
    border-color: #1e2833;
    padding: 8px;
}

QTabWidget::pane {
    border: 1px solid #202a35;
    border-radius: 8px;
    background: #0d1319;
    top: -1px;
}

QTabBar::tab {
    background: #0e151c;
    color: #7f8b99;
    border: 1px solid #202a35;
    border-bottom: none;
    padding: 8px 12px;
    margin-right: 3px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}

QTabBar::tab:selected {
    background: #151f2a;
    color: #eef4fa;
    border-color: #304154;
}

QTabBar::tab:hover:!selected {
    background: #121b24;
    color: #c9d3dd;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox QAbstractItemView {
    background: #111820;
    border: 1px solid #26313d;
    selection-background-color: #1f2d3b;
    selection-color: white;
}

QTableWidget {
    background: #0d1319;
    alternate-background-color: #0f151d;
    gridline-color: #1c2530;
    border: 1px solid #202a35;
    border-radius: 9px;
    selection-background-color: #1a3047;
    selection-color: #f2f6fa;
}

QTableWidget::item {
    padding: 7px 8px;
    border-bottom: 1px solid #18212b;
}

QTableWidget::item:selected {
    background: #1a3047;
}

QHeaderView::section {
    background: #111820;
    color: #8d99a6;
    border: none;
    border-right: 1px solid #1c2530;
    border-bottom: 1px solid #28323e;
    padding: 8px;
    font-weight: 600;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #2a3441;
    min-height: 28px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #384656;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
    border: none;
}

QSplitter::handle {
    background: #121922;
}

QStatusBar {
    background: #0b1016;
    border-top: 1px solid #1b2430;
    color: #778493;
}

QCheckBox {
    spacing: 8px;
    color: #c9d2db;
}
"""
