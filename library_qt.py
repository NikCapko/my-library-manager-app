#!/usr/bin/env python3
"""
Book Library Manager — PySide6 port with dark/light theme toggle.
"""

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    # QShortcut,
    QVBoxLayout,
    QWidget,
)
from watchdog.observers import Observer

try:
    from library_watcher import LibraryWatcher
except ImportError:
    LibraryWatcher = None

try:
    from dialog_manager import DialogManager
except ImportError:
    DialogManager = None


DB_FILE = "library.db"

# ---------- ТЕМЫ ----------

LIGHT_QSS = """
QMainWindow, QDialog { background: #f4f6f8; }
QWidget { color: #2c3e50; }
QLineEdit, QTextEdit, QComboBox, QTextBrowser {
    background: #ffffff; border: 1px solid #c9d0d6; border-radius: 4px; padding: 4px;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #2980b9; }
QPushButton {
    background: #e8ecef; border: 1px solid #c9d0d6; border-radius: 4px;
    padding: 5px 10px;
}
QPushButton:hover { background: #dde3e8; }
QPushButton:pressed { background: #cfd7dd; }
QTableWidget {
    background: #ffffff; alternate-background-color: #f2f5f7;
    gridline-color: #d7dde2; selection-background-color: #2980b9;
    selection-color: white;
}
QHeaderView::section {
    background: #e8ecef; border: none; border-bottom: 1px solid #c9d0d6;
    padding: 4px; font-weight: bold;
}
QScrollBar:vertical { background: #eef1f4; width: 12px; }
QScrollBar::handle:vertical { background: #bdc3c7; border-radius: 6px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #95a5a6; }
QLabel { background: transparent; }
QMenuBar, QMenu { background: #e8ecef; }
QMenu::item:selected { background: #2980b9; color: white; }
QCheckBox { background: transparent; }
#detailsPanel {
    background: #eef1f4;
    border-left: 1px solid #d7dde2;
}
#detailsPanel > QWidget > QWidget {
    background: transparent;
}
#detailsPanel QScrollBar:vertical {
    background: #eef1f4;
}
"""

DARK_QSS = """
QMainWindow, QDialog { background: #1e2227; }
QWidget { color: #dfe6ec; }
QLabel { background: transparent; }
QLineEdit, QTextEdit, QComboBox, QTextBrowser {
    background: #2a3036; border: 1px solid #3c444c; border-radius: 4px;
    padding: 4px; color: #dfe6ec;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #3498db; }
QPushButton {
    background: #2f363d; border: 1px solid #3c444c; border-radius: 4px;
    padding: 5px 10px; color: #dfe6ec;
}
QPushButton:hover { background: #3a424a; }
QPushButton:pressed { background: #242a30; }
QTableWidget {
    background: #23282e; alternate-background-color: #282e34;
    gridline-color: #3a424a; selection-background-color: #2980b9;
    selection-color: white;
}
QHeaderView::section {
    background: #2f363d; color: #dfe6ec; border: none;
    border-bottom: 1px solid #3c444c; padding: 4px; font-weight: bold;
}
QScrollBar:vertical { background: #1e2227; width: 12px; }
QScrollBar::handle:vertical { background: #4a525a; border-radius: 6px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #5a636b; }
QMenuBar, QMenu { background: #2a3036; color: #dfe6ec; }
QMenu::item:selected { background: #2980b9; color: white; }
QCheckBox { background: transparent; }
QTextBrowser a { color: #5dade2; }
QToolTip { background: #2a3036; color: #dfe6ec; border: 1px solid #3c444c; }
#detailsPanel {
    background: #191d21;
    border-left: 1px solid #2f363d;
}
#detailsPanel > QWidget > QWidget {
    background: transparent;
}
#detailsPanel QScrollBar:vertical {
    background: #191d21;
}
"""


# ---------- БД (без изменений) ----------


def connect():
    conn = sqlite3.connect(DB_FILE)

    def _cmp(a, b):
        a = "" if a is None else str(a)
        b = "" if b is None else str(b)
        aa = a.casefold()
        bb = b.casefold()
        return (aa > bb) - (aa < bb)

    conn.create_collation("UNI_NOCASE", _cmp)
    conn.create_function(
        "UNI_LOWER", 1, lambda s: "" if s is None else str(s).casefold()
    )
    return conn


def init_db():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, orig_name TEXT, author TEXT, description TEXT,
            lang TEXT, bnf_path TEXT, favorite INTEGER DEFAULT 0
        )
    """)
    cur.execute(
        "CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_books_author_title ON books (author, title)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS book_tags (
            book_id INTEGER, tag_id INTEGER, UNIQUE(book_id, tag_id),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def save_tags(book_id, tags):
    conn = connect()
    cur = conn.cursor()
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
        cur.execute("SELECT id FROM tags WHERE name=?", (tag,))
        tag_id = cur.fetchone()[0]
        cur.execute(
            "INSERT OR IGNORE INTO book_tags (book_id, tag_id) VALUES (?, ?)",
            (book_id, tag_id),
        )
    conn.commit()
    conn.close()


def get_tags_for_book(book_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT name FROM tags JOIN book_tags ON tags.id = book_tags.tag_id
        WHERE book_tags.book_id=?
    """,
        (book_id,),
    )
    tags = [row[0] for row in cur.fetchall()]
    conn.close()
    return tags


def find_book_id(title, author):
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM books
        WHERE UNI_LOWER(title) = UNI_LOWER(?) AND UNI_LOWER(author) = UNI_LOWER(?)
    """,
        (title, author),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def add_or_update_book(
    title,
    orig_name,
    author,
    description,
    lang=None,
    favorite=False,
    bnf_path=None,
    tags=None,
):
    book_id = find_book_id(title, author)
    conn = connect()
    cur = conn.cursor()
    if book_id:
        cur.execute(
            """
            UPDATE books SET title=?, orig_name=?, author=?, description=?,
                             lang=?, favorite=?, bnf_path=? WHERE id=?
        """,
            (title, orig_name, author, description, lang, favorite, bnf_path, book_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO books (title, orig_name, author, description, lang, favorite, bnf_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (title, orig_name, author, description, lang, favorite, bnf_path),
        )
        book_id = cur.lastrowid
    conn.commit()
    conn.close()
    if tags:
        save_tags(book_id, tags)
    return book_id


def get_books(filter_text=""):
    conn = connect()
    cur = conn.cursor()
    if filter_text:
        f = filter_text.casefold()
        cur.execute(
            """
            SELECT DISTINCT books.* FROM books
            LEFT JOIN book_tags ON books.id = book_tags.book_id
            LEFT JOIN tags ON tags.id = book_tags.tag_id
            WHERE UNI_LOWER(books.title) LIKE UNI_LOWER(?)
               OR UNI_LOWER(books.orig_name) LIKE UNI_LOWER(?)
               OR UNI_LOWER(books.author) LIKE UNI_LOWER(?)
               OR UNI_LOWER(tags.name) LIKE UNI_LOWER(?)
            ORDER BY UNI_LOWER(books.title)
        """,
            (f"%{f}%", f"%{f}%", f"%{f}%", f"%{f}%"),
        )
    else:
        cur.execute("SELECT * FROM books ORDER BY UNI_LOWER(title)")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_book(book_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM books WHERE id=?", (book_id,))
    book = cur.fetchone()
    conn.close()
    return book


def check_db_files_exist():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, bnf_path FROM books")
    rows = cur.fetchall()
    deleted = 0
    for book_id, path in rows:
        if not os.path.exists(path):
            cur.execute("DELETE FROM books WHERE id=?", (book_id,))
            deleted += 1
    if deleted:
        conn.commit()
        print(f"Удалено {deleted} записей без файлов.")
    conn.close()


# ---------- Открытие внешних приложений ----------


def open_folder(file_path):
    folder = os.path.dirname(file_path)
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", file_path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", file_path])
        else:
            for fm in ["nautilus", "dolphin", "thunar", "pcmanfm"]:
                if (
                    subprocess.call(
                        ["which", fm],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    == 0
                ):
                    if fm == "nautilus":
                        parent = os.path.dirname(os.path.abspath(file_path))
                        subprocess.Popen(["nautilus", "--new-window", parent])
                    elif fm == "dolphin":
                        subprocess.Popen(["dolphin", "--select", file_path])
                    else:
                        subprocess.Popen([fm, file_path])
                    return
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        QMessageBox.critical(None, "Ошибка", f"Не удалось открыть папку: {e}")


def open_lang_file(folder, base_name, lang_code, paraline=False):
    if lang_code in ("ru", "en"):
        file_name = f"{base_name}.{lang_code}.md"
    elif lang_code == "en-ru":
        file_name = f"{base_name}.en.md"
    else:
        file_name = f"{base_name}.md"
    file_path = os.path.join(folder, file_name)
    if not os.path.exists(file_path):
        QMessageBox.critical(None, "Ошибка", f"Файл {file_path} не найден")
        return
    try:
        if paraline:
            subprocess.Popen(  ["/home/nikolay/bin/paraline_qt", file_path])
        else:
            subprocess.Popen(["/home/nikolay/bin/md_editor_qt", file_path])
    except Exception as e:
        QMessageBox.critical(None, "Ошибка", str(e))


def open_file(folder, base_name):
    file_name = f"{base_name}.md"
    file_path = os.path.join(folder, file_name)
    if not os.path.exists(file_path):
        QMessageBox.critical(None, "Ошибка", f"Файл {file_path} не найден")
        return
    try:
        subprocess.Popen(
            ["/home/nikolay/bin/md_editor_qt", file_path],
            cwd="/home/nikolay/Projects/md_editor_qt",
        )
    except Exception as e:
        QMessageBox.critical(None, "Ошибка", str(e))


# ---------- Watcher-адаптер ----------


class WatcherBridge(QObject):
    """Мост между потоком watchdog и Qt-сигналами."""

    file_changed = Signal(str)  # путь к изменённому .bnf

    def __init__(self, path: str):
        super().__init__()
        self._path = path
        self._observer = None

    def start(self):
        if LibraryWatcher is None:
            return
        self._queue = queue.Queue()
        handler = LibraryWatcher(self._queue)
        self._observer = Observer()
        self._observer.schedule(handler, self._path, recursive=True)

        def _run():
            self._observer.start()

        threading.Thread(target=_run, daemon=True).start()

        # Polling из GUI-потока
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(300)

    def _poll(self):
        try:
            while True:
                ev = self._queue.get_nowait()
                # ev может быть (event_type, path) или просто path
                if isinstance(ev, (tuple, list)) and len(ev) == 2:
                    _, p = ev
                else:
                    p = str(ev)
                self.file_changed.emit(p)
        except queue.Empty:
            pass

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)


# ---------- Диалог чипсов-тегов ----------


class TagChip(QFrame):
    clicked = Signal(str)

    def __init__(self, tag: str, active: bool, parent=None):
        super().__init__(parent)
        self.tag = tag
        self.active = active
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()

        lbl = QLabel(tag, self)
        lbl.setFont(QFont("Arial", 10))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.addWidget(lbl)
        self._label = lbl
        lbl.mousePressEvent = lambda e: self.clicked.emit(self.tag)
        self.mousePressEvent = lambda e: self.clicked.emit(self.tag)
        self.enterEvent = lambda e: self._hover(True)
        self.leaveEvent = lambda e: self._hover(False)

    def _apply_style(self, hover=False):
        if self.active:
            bg = "#3498db" if hover else "#2980b9"
            fg = "white"
        else:
            bg = "#d5dbdb" if hover else "#ecf0f1"
            fg = "#2c3e50"
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid #bdc3c7;"
            f" border-radius: 10px; }}"
            f"QLabel {{ color: {fg}; background: transparent; }}"
        )

    def _hover(self, on):
        self._apply_style(on)


class MetadataDialog(QDialog):
    def __init__(self, app: "LibraryApp", book_id: int):
        super().__init__(app)
        self.app = app
        self.book_id = book_id
        self.setWindowTitle("Метаданные книги")
        self.resize(1100, 750)
        self.setModal(True)

        book = get_book(book_id)
        if not book:
            self.reject()
            return
        (_, title, orig_name, author, desc, lang, bnf_path, favorite) = book
        self.bnf_path = bnf_path
        self.tags = get_tags_for_book(book_id)

        self.all_tags = sorted(app.tag_history[:])

        layout = QVBoxLayout(self)

        # --- Форма полей ---
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self.title_edit = QLineEdit(title)
        self.orig_edit = QLineEdit(orig_name)
        self.author_edit = QLineEdit(author)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["en-ru", "ru"])
        self.lang_combo.setCurrentText(lang if lang in ("en-ru", "ru") else "en-ru")
        self.fav_check = QCheckBox("В избранном")
        self.fav_check.setChecked(bool(favorite))

        form.addRow("Название:", self.title_edit)
        if lang != "ru":
            form.addRow("Оригинальное название:", self.orig_edit)
        else:
            self.orig_edit.setVisible(False)
            form.addRow("", self.orig_edit)
        form.addRow("Автор:", self.author_edit)
        form.addRow("Язык:", self.lang_combo)
        form.addRow("Избранное:", self.fav_check)
        layout.addLayout(form)

        # --- Теги ---
        layout.addWidget(QLabel("Теги:"))
        self.tag_entry = QLineEdit()
        self.tag_entry.setPlaceholderText("Введите тег и нажмите Enter")
        self.tag_entry.returnPressed.connect(self._on_add_tag)
        layout.addWidget(self.tag_entry)

        chips_scroll = QScrollArea()
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setMinimumHeight(180)
        self.chips_host = QWidget()
        self.chips_layout = QGridLayout(self.chips_host)
        self.chips_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.chips_layout.setSpacing(6)
        chips_scroll.setWidget(self.chips_host)
        layout.addWidget(chips_scroll)

        # --- Описание ---
        layout.addWidget(QLabel("Описание:"))
        self.desc_edit = QTextEdit()
        self.desc_edit.setPlainText(desc)
        layout.addWidget(self.desc_edit, 1)

        # --- Кнопки ---
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("Сохранить")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._rebuild_chips()

    def _rebuild_chips(self):
        # очистка
        while self.chips_layout.count():
            it = self.chips_layout.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

        cols = 8
        for i, tag in enumerate(self.all_tags):
            chip = TagChip(tag, tag in self.tags, self.chips_host)
            chip.clicked.connect(self._toggle_tag)
            r, c = divmod(i, cols)
            self.chips_layout.addWidget(chip, r, c)

    def _toggle_tag(self, tag: str):
        if tag in self.tags:
            self.tags.remove(tag)
        else:
            self.tags.append(tag)
        self._rebuild_chips()

    def _on_add_tag(self):
        tag = self.tag_entry.text().strip().lower()
        if not tag:
            return
        if tag not in self.all_tags:
            self.all_tags.insert(0, tag)
        if tag not in self.tags:
            self.tags.append(tag)
        self.tag_entry.clear()
        self._rebuild_chips()

    def _save(self):
        new_title = self.title_edit.text()
        new_orig = self.orig_edit.text()
        new_author = self.author_edit.text()
        new_desc = self.desc_edit.toPlainText().strip()
        new_lang = self.lang_combo.currentText().strip() or None
        new_fav = self.fav_check.isChecked()

        self.app._save_tag_history(self.all_tags)

        add_or_update_book(
            new_title,
            new_orig,
            new_author,
            new_desc,
            new_lang,
            new_fav,
            self.bnf_path,
            self.tags,
        )

        if self.bnf_path and os.path.exists(self.bnf_path):
            try:
                with open(self.bnf_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.update(
                    {
                        "title": new_title,
                        "orig_name": new_orig,
                        "author": new_author,
                        "description": new_desc,
                        "lang": new_lang,
                        "favorite": new_fav,
                        "tags": self.tags,
                    }
                )
                with open(self.bnf_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось обновить .bnf: {e}")

        self.app.refresh_book(self.book_id)
        # if DialogManager:
        # DialogManager.show_dialog("Сохранено", "Метаданные успешно сохранены.")
        self.accept()


class ClickableFrame(QFrame):
    """Чипс-кнопка: скруглённая рамка с подписью, реагирует на клик и hover."""

    clicked = Signal()

    def __init__(self, text, colors, active=False, small=False, parent=None):
        super().__init__(parent)
        self.setObjectName("chip")
        self._colors = colors
        self._active = active
        self._small = small
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()

        self._label = QLabel(text, self)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(0)
        lay.addWidget(self._label)

        self.mousePressEvent = lambda e: self.clicked.emit()

    def _apply_style(self, hover=False):
        c = self._colors
        if self._active:
            bg = "#3498db" if hover else c["chip_active"]
            fg = "white"
        else:
            if hover:
                bg = "#c8d0d6" if not self._small else "#e4e9ec"
            else:
                bg = c["chip"]
            fg = c["text"]
        fs = "9pt" if self._small else "10pt"
        self.setStyleSheet(f"""
            #chip {{
                background: {bg};
                border: 1px solid {c["border"]};
                border-radius: 11px;
            }}
            #chip QLabel {{
                color: {fg};
                background: transparent;
                font-size: {fs};
            }}
        """)

    def enterEvent(self, e):
        self._apply_style(hover=True)

    def leaveEvent(self, e):
        self._apply_style(hover=False)


class DetailsPanel(QScrollArea):
    """Панель деталей книги: карточки, заголовки, чипсы, действия."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self._current_book_id = None
        self.setObjectName("detailsPanel")
        self.setWidgetResizable(True)
        self.setMaximumWidth(500)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content.setObjectName("detailsContent")
        self._content.setAttribute(Qt.WA_StyledBackground, True)
        self._content.setStyleSheet("background: transparent;")  # ← добавить
        self._vbox = QVBoxLayout(self._content)
        self._vbox.setContentsMargins(18, 18, 18, 18)
        self._vbox.setSpacing(10)
        self.setWidget(self._content)

        self.show_empty()

    # ---------- цвета ----------
    def _colors(self):
        if self.app.dark_theme:
            return {
                "text": "#dfe6ec",
                "muted": "#8b96a0",
                "accent": "#5dade2",
                "accent_hover": "#7ec8f0",
                "card": "#22282e",  # ← чуть светлее панели (#191d21)
                "border": "#2f363d",
                "chip": "#2a3036",
                "chip_active": "#2980b9",
                "title": "#ffffff",
                "panel_bg": "#191d21",  # ← для справки (не используется в стилях виджетов)
            }
        return {
            "text": "#2c3e50",
            "muted": "#7f8c8d",
            "accent": "#2471a3",
            "accent_hover": "#1b527a",
            "card": "#ffffff",  # ← на светлой панели (#eef1f4) карточка = белая
            "border": "#dfe6ec",
            "chip": "#e4e9ec",
            "chip_active": "#2980b9",
            "title": "#1a252f",
            "panel_bg": "#eef1f4",
        }

    # ---------- служебное ----------
    def _clear(self):
        while self._vbox.count():
            it = self._vbox.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            else:
                sub = it.layout()
                if sub:
                    while sub.count():
                        s = sub.takeAt(0)
                        if s.widget():
                            s.widget().deleteLater()

    def _section_header(self, text):
        c = self._colors()
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {c['muted']};"
            f" letter-spacing: 1.5px; padding-top: 6px; padding-bottom: 2px;"
        )
        return lbl

    def _separator(self):
        c = self._colors()
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background: {c['border']}; max-height: 1px; border: none;")
        return line

    def _link_button(self, text, callback):
        c = self._colors()
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 6px 10px;
                font-size: 11pt;
                color: {c["accent"]};
                background: transparent;
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background: {c["chip"]};
                color: {c["accent_hover"]};
            }}
        """)
        btn.clicked.connect(callback)
        return btn

    # ---------- состояния ----------
    def show_empty(self):
        self._current_book_id = None
        self._clear()
        c = self._colors()
        lbl = QLabel("Выберите книгу, чтобы увидеть подробности")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {c['muted']}; font-size: 12pt;")
        self._vbox.addStretch()
        self._vbox.addWidget(lbl)
        self._vbox.addStretch()

    def refresh_theme(self):
        if self._current_book_id is not None:
            self.show_book(self._current_book_id)
        else:
            self.show_empty()

    def show_book(self, book_id):
        self._clear()
        book = get_book(book_id)
        if not book:
            self.show_empty()
            return

        self._current_book_id = book_id
        _, title, orig_name, author, desc, lang, bnf_path, favorite = book
        tags = get_tags_for_book(book_id)
        folder = os.path.dirname(bnf_path) if bnf_path else None
        base_name = (
            os.path.splitext(os.path.basename(bnf_path))[0] if bnf_path else None
        )
        c = self._colors()

        # ---------- Шапка: заголовок + оригинал ----------
        title_lbl = QLabel(title or "—")
        title_lbl.setWordWrap(True)
        title_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title_lbl.setStyleSheet(
            f"font-size: 18pt; font-weight: bold; color: {c['title']};"
        )
        self._vbox.addWidget(title_lbl)

        if orig_name and orig_name != title:
            orig_lbl = QLabel(orig_name)
            orig_lbl.setWordWrap(True)
            orig_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            orig_lbl.setStyleSheet(
                f"font-size: 12pt; font-style: italic; color: {c['muted']};"
            )
            self._vbox.addWidget(orig_lbl)

        # ---------- Автор + звёздочка ----------
        self._vbox.addWidget(self._section_header("Автор"))
        author_row = QHBoxLayout()
        author_row.setSpacing(8)

        is_fav = (favorite == 1)
        star = QLabel("★" if is_fav else "☆")
        star.setCursor(Qt.PointingHandCursor)
        star.setToolTip("Убрать из избранного" if is_fav else "Добавить в избранное")
        star.setStyleSheet(
            f"font-size: 20pt; color: "
            f"{'#f1c40f' if is_fav else c['muted']};"
            f" padding: 0 4px;"
        )
        star.mousePressEvent = (
            lambda e, bid=book_id: self.app.toggle_favorite(bid)
        )
        author_row.addWidget(star)

        author_lbl = QLabel(author or "Неизвестный автор")
        author_lbl.setWordWrap(True)
        author_lbl.setCursor(Qt.PointingHandCursor)
        author_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        author_lbl.setStyleSheet(
            f"font-size: 13pt; color: {c['accent']}; text-decoration: underline;"
        )
        author_lbl.mousePressEvent = lambda e, a=author: (
            self.app.search_by_author(a) if a else None
        )
        author_row.addWidget(author_lbl, 1)
        self._vbox.addLayout(author_row)

        # ---------- Язык ----------
        if lang:
            self._vbox.addWidget(self._section_header("Язык"))
            lang_flow = QHBoxLayout()
            lang_flow.setSpacing(6)

            if lang in ("ru", "en"):
                chip = ClickableFrame(lang, c, active=True, small=True)
                chip.clicked.connect(
                    lambda _=None, f=folder, b=base_name: open_file(f, b)
                )
                lang_flow.addWidget(chip)
            elif lang == "en-ru":
                specs = [
                    ("ru", False),
                    ("en", False),
                    ("en-ru", True),
                ]
                for code, use_paraline in specs:
                    chip = ClickableFrame(code, c, active=True, small=True)
                    chip.clicked.connect(
                        lambda _=None, cd=code, pp=use_paraline, f=folder, b=base_name: (
                            open_lang_file(f, b, cd, pp)
                        )
                    )
                    lang_flow.addWidget(chip)
            lang_flow.addStretch()
            self._vbox.addLayout(lang_flow)

        # ---------- Теги ----------
        if tags:
            self._vbox.addWidget(self._section_header("Теги"))
            tags_grid = QGridLayout()
            tags_grid.setSpacing(6)
            tags_grid.setContentsMargins(0, 0, 0, 0)
            cols = 4
            for i, t in enumerate(tags):
                chip = ClickableFrame(t, c, active=False, small=True)
                chip.clicked.connect(lambda _=None, tag=t: self.app.search_by_tag(tag))
                tags_grid.addWidget(chip, i // cols, i % cols)
            tags_grid.setColumnStretch(cols, 1)
            self._vbox.addLayout(tags_grid)

        # ---------- Описание ----------
        if desc:
            self._vbox.addWidget(self._section_header("Описание"))

            desc_card = QFrame()
            desc_card.setObjectName("descCard")
            desc_card.setStyleSheet(f"""
                #descCard {{
                    background: {c["card"]};
                    border: 1px solid {c["border"]};
                    border-radius: 8px;
                }}
            """)
            d_lay = QVBoxLayout(desc_card)
            d_lay.setContentsMargins(14, 12, 14, 12)
            d_lbl = QLabel(desc)
            d_lbl.setWordWrap(True)
            d_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            d_lbl.setStyleSheet(
                f"font-size: 11pt; color: {c['text']}; background: transparent;"
            )
            d_lay.addWidget(d_lbl)
            self._vbox.addWidget(desc_card)

        # ---------- Действия ----------
        self._vbox.addWidget(self._separator())
        actions = QVBoxLayout()
        actions.setSpacing(2)

        if bnf_path and os.path.exists(bnf_path):
            actions.addWidget(
                self._link_button(
                    "📂  Открыть папку",
                    lambda _=None, p=bnf_path: open_folder(p),
                )
            )
        actions.addWidget(
            self._link_button(
                "📝  Открыть bnf-файл",
                lambda _=None, b=book_id: self.app.open_metadata_dialog(b),
            )
        )
        self._vbox.addLayout(actions)

        self._vbox.addStretch()


# ---------- Главное окно ----------
class LibraryApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Book Library Manager")
        self.resize(1100, 700)

        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).resolve().parent

        self.library_path = str(base_dir / "library")
        os.makedirs(self.library_path, exist_ok=True)

        # Настройки приложения (тема и т.п.)
        self.settings = QSettings("BookLibrary", "LibraryManager")
        self.dark_theme = self.settings.value("ui/dark_theme", False, type=bool)

        self.history_path = os.path.join(
            os.path.expanduser("~"), ".bnf_tag_history.json"
        )
        self.tag_history = self._load_tag_history()

        self._build_ui()
        check_db_files_exist()
        self.refresh_books()

        # watcher
        self.watcher = WatcherBridge(self.library_path)
        self.watcher.file_changed.connect(self._on_fs_event)
        self.watcher.start()

    # ---------- UI ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Меню
        menubar = self.menuBar()
        view_menu = menubar.addMenu("Вид")
        self.theme_action = QAction("Тёмная тема", self, checkable=True)
        self.theme_action.setShortcut(QKeySequence("Ctrl+T"))
        self.theme_action.setChecked(self.dark_theme)  # ← взять из QSettings
        self.theme_action.toggled.connect(self._toggle_theme)
        view_menu.addAction(self.theme_action)
        self.apply_theme()

        # Панель поиска
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по названию / автору / тегу…")
        self.search_edit.returnPressed.connect(self.refresh_books)
        top.addWidget(self.search_edit, 1)

        self.fav_only = False
        btn_fav = QPushButton("★")
        btn_fav.setFixedWidth(40)
        btn_fav.setCheckable(True)
        btn_fav.toggled.connect(self._toggle_fav_filter)
        top.addWidget(btn_fav)

        btn_search = QPushButton("🔎")
        btn_search.setFixedWidth(40)
        btn_search.clicked.connect(self.refresh_books)
        top.addWidget(btn_search)

        btn_clear = QPushButton("❌")
        btn_clear.setFixedWidth(40)
        btn_clear.clicked.connect(self.reset_search)
        top.addWidget(btn_clear)

        btn_scan = QPushButton("Сканировать папку")
        btn_scan.clicked.connect(self.scan_folder_dialog)
        top.addWidget(btn_scan)

        root.addLayout(top)

        # Основная область: таблица + детали
        body = QHBoxLayout()
        root.addLayout(body, 1)

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            [
                "ID",
                "Изб.",
                "Автор",
                "Название",
                "Оригинальное название",
                "Язык",
                "Описание",
                "Теги",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 40)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 250)
        self.table.setColumnWidth(4, 250)
        self.table.setColumnWidth(5, 60)
        self.table.setColumnWidth(6, 300)
        self.table.setColumnWidth(7, 200)
        self.table.cellDoubleClicked.connect(self._open_from_table)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.table.cellClicked.connect(self._on_cell_clicked)
        body.addWidget(self.table, 3)

        # Панель деталей
        self.details_panel = DetailsPanel(self)
        self.details_panel.setMinimumWidth(380)
        body.addWidget(self.details_panel, 2)

        # статус
        self.status_label = QLabel("Готово")
        self.statusBar().addWidget(self.status_label, 1)

        # Горячие клавиши
        # QShortcut(QKeySequence("Return"), self.table, activated=self._open_from_table)

        # сортировка по клику на заголовок
        self.table.horizontalHeader().sectionClicked.connect(self._sort_column)
        self._sort_orders = {i: True for i in range(8)}

    def _toggle_fav_filter(self, on: bool):
        self.fav_only = on
        self.refresh_books()

    # ---------- Тема ----------

    def _toggle_theme(self, dark: bool):
        self.dark_theme = dark
        self.settings.setValue("ui/dark_theme", dark)
        self.settings.sync()
        self.apply_theme()

    def apply_theme(self):
        if self.dark_theme:
            self.setStyleSheet(DARK_QSS)
            self.theme_action.setText("Светлая тема")
        else:
            self.setStyleSheet(LIGHT_QSS)
            self.theme_action.setText("Тёмная тема")
        if hasattr(self, "details_panel"):
            self.details_panel.refresh_theme()

    def _on_cell_clicked(self, row: int, col: int):
        if col != 1:          # колонка "Изб."
            return
        it = self.table.item(row, 0)
        if not it:
            return
        try:
            book_id = int(it.text())
        except ValueError:
            return
        self.toggle_favorite(book_id)

    # ---------- Теги-история ----------

    def _load_tag_history(self):
        if not os.path.exists(self.history_path):
            return []
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [str(t).strip().lower() for t in data if str(t).strip()]
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save_tag_history(self, all_tags):
        history = sorted(all_tags[:])
        try:
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            self.tag_history = history
        except OSError:
            pass

    # ---------- Данные в таблице ----------

    def _check_favorite(self, fav):
        return "★" if fav == 1 else "☆"

    def _fill_table(self, books):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for book in books:
            book_id, title, orig_name, author, desc, lang, bnf_path, favorite = book
            tags = ", ".join(get_tags_for_book(book_id))
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                str(book_id),
                self._check_favorite(favorite),
                author or "",
                title or "",
                orig_name or "",
                lang or "",
                desc or "",
                tags,
            ]
            for col, val in enumerate(values):
                it = QTableWidgetItem(val)
                if col in (0,):
                    it.setTextAlignment(Qt.AlignCenter)
                if col == 1:
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, it)
        self.table.setSortingEnabled(False)

    def refresh_books(self):
        selected_id = self._selected_book_id()
        books = get_books(self.search_edit.text())
        if self.fav_only:
            books = [b for b in books if b[7] == 1]
        self._fill_table(books)
        if selected_id is not None:
            self._select_by_id(selected_id)
        self.status_label.setText(f"Найдено книг: {len(books)}")

    def refresh_book(self, book_id):
        book = get_book(book_id)
        if not book:
            return
        row = self._row_for_id(book_id)
        if row is None:
            self.refresh_books()
            return
        b_id, title, orig_name, author, desc, lang, bnf_path, favorite = book
        tags = ", ".join(get_tags_for_book(b_id))
        values = [
            str(b_id),
            self._check_favorite(favorite),
            author or "",
            title or "",
            orig_name or "",
            lang or "",
            desc or "",
            tags,
        ]
        for col, val in enumerate(values):
            it = self.table.item(row, col)
            if it is None:
                it = QTableWidgetItem()
                self.table.setItem(row, col, it)
            it.setText(val)
        self.show_details()

    def toggle_favorite(self, book_id: int):
        """Инвертирует флаг избранного у книги и обновляет UI."""
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE books SET favorite = 1 - favorite WHERE id = ?",
            (book_id,),
        )
        conn.commit()
        cur.execute("SELECT favorite FROM books WHERE id = ?", (book_id,))
        row = cur.fetchone()
        conn.close()

        if row is None:
            return

        new_fav = bool(row[0])

        # синхронизируем .bnf, если он есть
        book = get_book(book_id)
        if book:
            bnf_path = book[6]
            if bnf_path and os.path.exists(bnf_path):
                try:
                    with open(bnf_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["favorite"] = new_fav
                    with open(bnf_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Не удалось обновить .bnf: {e}")

        # обновляем строку в таблице и панель деталей
        self.refresh_book(book_id)
        if hasattr(self, "details_panel"):
            self.details_panel.show_book(book_id)

    def _row_for_id(self, book_id):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.text() == str(book_id):
                return r
        return None

    def _selected_book_id(self):
        items = self.table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        it = self.table.item(row, 0)
        return int(it.text()) if it else None

    def _select_by_id(self, book_id):
        row = self._row_for_id(book_id)
        if row is not None:
            self.table.selectRow(row)

    def reset_search(self):
        self.search_edit.clear()
        self.refresh_books()

    # ---------- Сортировка ----------

    def _sort_column(self, col: int):
        n = self.table.rowCount()
        if n == 0:
            return
        ascending = self._sort_orders.get(col, True)

        rows_data = []
        for r in range(n):
            it = self.table.item(r, col)
            text = it.text() if it else ""
            rows_data.append((text, r))

        if col == 0:

            def key(t):
                try:
                    return int(t[0])
                except ValueError:
                    return float("inf")

            rows_data.sort(key=key, reverse=not ascending)
        else:
            rows_data.sort(key=lambda t: t[0].casefold(), reverse=not ascending)

        # перестроить таблицу
        temp = []
        for _, r in rows_data:
            temp.append(
                [
                    self.table.item(r, c).text() if self.table.item(r, c) else ""
                    for c in range(self.table.columnCount())
                ]
            )

        self.table.setRowCount(0)
        for values in temp:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for c, v in enumerate(values):
                self.table.setItem(row, c, QTableWidgetItem(v))

        self._sort_orders[col] = not ascending

    # ---------- Детали ----------

    def show_details(self):
        book_id = self._selected_book_id()
        if book_id is None:
            self.details_panel.show_empty()
            return
        self.details_panel.show_book(book_id)

    def _open_by_lang(self, code):
        bid = self._selected_book_id()
        book = get_book(bid) if bid else None
        if not book:
            return
        _, title, orig_name, author, desc, lang, bnf_path, favorite = book
        folder = os.path.dirname(bnf_path) if bnf_path else None
        base_name = (
            os.path.splitext(os.path.basename(bnf_path))[0] if bnf_path else None
        )
        if code in ("ru", "en"):
            open_file(folder, base_name)
        elif code == "en-ru":
            open_lang_file(folder, base_name, "en-ru", True)

    def search_by_author(self, author):
        self.search_edit.setText(author)
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM books WHERE UNI_LOWER(author) = UNI_LOWER(?)
            ORDER BY UNI_LOWER(title)
        """,
            (author,),
        )
        books = cur.fetchall()
        conn.close()
        self._fill_table(books)
        self.status_label.setText(f"Найдено книг автора '{author}': {len(books)}")

    def search_by_tag(self, tag):
        self.search_edit.setText(tag)
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT books.* FROM books
            JOIN book_tags ON books.id = book_tags.book_id
            JOIN tags ON tags.id = book_tags.tag_id
            WHERE UNI_LOWER(tags.name) = UNI_LOWER(?)
            ORDER BY UNI_LOWER(books.title)
        """,
            (tag,),
        )
        books = cur.fetchall()
        conn.close()
        self._fill_table(books)
        self.status_label.setText(f"Найдено книг с тегом '{tag}': {len(books)}")

    # ---------- Открытие из списка ----------

    def _open_from_table(self, *args):
        book_id = self._selected_book_id()
        if book_id is None:
            return
        book = get_book(book_id)
        if not book:
            return
        _, title, orig_name, author, desc, lang, bnf_path, favorite = book
        folder = os.path.dirname(bnf_path) if bnf_path else None
        base_name = (
            os.path.splitext(os.path.basename(bnf_path))[0] if bnf_path else None
        )
        if lang in ("ru", "en"):
            open_file(folder, base_name)
        elif lang == "en-ru":
            open_lang_file(folder, base_name, "en-ru", True)

    def open_metadata_dialog(self, book_id):
        dlg = MetadataDialog(self, book_id)
        dlg.exec()

    # ---------- Watcher ----------

    def _on_fs_event(self, path: str):
        # обновляем запись, если путь соответствует bnf-файлу в БД
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT id FROM books WHERE bnf_path=?", (path,))
        row = cur.fetchone()
        if row:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                add_or_update_book(
                    data.get("title", ""),
                    data.get("orig_name", ""),
                    data.get("author", ""),
                    data.get("description", ""),
                    data.get("lang"),
                    data.get("favorite", False),
                    path,
                    data.get("tags", []),
                )
            except Exception as e:
                print(f"watcher parse error: {e}")
            self.refresh_book(row[0])
        conn.close()

    # ---------- Сканирование ----------

    def scan_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if not folder:
            return
        self.status_label.setText("Сканирование...")
        threading.Thread(target=self._scan_worker, args=(folder,), daemon=True).start()

    def _scan_worker(self, folder):
        count = 0
        for root, _, files in os.walk(folder):
            for file in files:
                if file.endswith(".bnf"):
                    try:
                        with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                            data = json.load(f)
                        add_or_update_book(
                            data.get("title", ""),
                            data.get("orig_name", ""),
                            data.get("author", ""),
                            data.get("description", ""),
                            data.get("lang"),
                            data.get("favorite", False),
                            os.path.join(root, file),
                            data.get("tags", []),
                        )
                        count += 1
                    except Exception as e:
                        print(f"Ошибка {file}: {e}")
        check_db_files_exist()
        QTimer.singleShot(0, lambda: self._scan_done(count))

    def _scan_done(self, count):
        self.refresh_books()
        QMessageBox.information(
            self, "Сканирование", f"Добавлено или обновлено {count} книг"
        )

    def closeEvent(self, event):
        self.watcher.stop()
        super().closeEvent(event)


# ---------- Точка входа ----------


def main():
    init_db()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = LibraryApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
