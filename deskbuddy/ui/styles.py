"""Qt stylesheets for the meeting card and the correction panel."""

CARD_CSS = """
QWidget#card { background: #1d2233; border: 1px solid #5b73e8; border-radius: 14px; }
QLabel { color: #e8ecf8; }
QLabel#when { color: #9fb2ff; font-size: 11px; font-weight: 600; }
QLabel#countdown { color: #ffffff; font-size: 26px; font-weight: 700; }
QLabel#countdown[late="true"] { color: #ffb3a7; font-size: 18px; }
QLabel#title { font-size: 14px; font-weight: 600; }
QLabel#time { color: #9aa6c8; font-size: 11px; }
QLabel#link { color: #8fb4ff; font-size: 11px; }
QLabel#nolink { color: #c9b27a; font-size: 11px; }
QLabel#brief { color: #d7e3ff; font-size: 12px; background: #262d45; border-radius: 8px;
               padding: 6px 8px; margin-top: 4px; }
QPushButton { background: #2c3450; color: #e8ecf8; border: none; border-radius: 8px;
              padding: 6px 11px; font-size: 12px; }
QPushButton:hover { background: #3a4466; }
QPushButton#join { background: #5b73e8; font-weight: 600; }
QPushButton#join:hover { background: #6d86f0; }
"""


PANEL_CSS = """
QWidget#card { background: #1d2233; border: 1px solid #3a4466; border-radius: 16px; }
QLabel { color: #e8ecf8; }
QLabel#title { font-size: 15px; font-weight: 600; }
QLabel#status { color: #9aa6c8; font-size: 11px; }
QPlainTextEdit { background: #11141f; color: #eef1fb; border: 1px solid #333c5c;
                 border-radius: 10px; padding: 6px; font-size: 13px;
                 selection-background-color: #4f6bd8; }
QPlainTextEdit:focus { border-color: #6d86f0; }
QPlainTextEdit#changes { color: #b9c3e6; font-size: 12px; }
QPushButton { background: #2c3450; color: #e8ecf8; border: none; border-radius: 9px;
              padding: 7px 14px; font-size: 12px; }
QPushButton:hover { background: #3a4466; }
QPushButton#primary { background: #5b73e8; font-weight: 600; }
QPushButton#primary:hover { background: #6d86f0; }
QPushButton#primary:disabled { background: #3b4677; color: #b8c0dc; }
QPushButton#close { background: transparent; font-size: 16px; padding: 2px 8px; }
QPushButton#close:hover { background: #3a2230; color: #ff8fa3; }
"""


CHAT_CSS = """
QWidget#card { background: #1d2233; border: 1px solid #3a4466; border-radius: 16px; }
QLabel { color: #e8ecf8; }
QLabel#title { font-size: 15px; font-weight: 600; }
QLabel#status { color: #9aa6c8; font-size: 11px; }
QTextBrowser { background: #11141f; color: #eef1fb; border: 1px solid #333c5c;
               border-radius: 10px; padding: 6px; font-size: 13px; }
QPlainTextEdit { background: #11141f; color: #eef1fb; border: 1px solid #333c5c;
                 border-radius: 10px; padding: 6px; font-size: 13px;
                 selection-background-color: #4f6bd8; }
QPlainTextEdit:focus { border-color: #6d86f0; }
QPushButton { background: #2c3450; color: #e8ecf8; border: none; border-radius: 9px;
              padding: 7px 14px; font-size: 12px; }
QPushButton:hover { background: #3a4466; }
QPushButton#small { padding: 4px 9px; font-size: 11px; }
QPushButton#primary { background: #5b73e8; font-weight: 600; }
QPushButton#primary:hover { background: #6d86f0; }
QPushButton#primary:disabled { background: #3b4677; color: #b8c0dc; }
QPushButton#close { background: transparent; font-size: 16px; padding: 2px 8px; }
QPushButton#close:hover { background: #3a2230; color: #ff8fa3; }
"""
