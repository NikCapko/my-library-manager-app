#!/usr/bin/python

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from watchdog.observers import Observer

from dialog_manager import DialogManager
from library_watcher import LibraryWatcher

DB_FILE = "library.db"


# --- Работа с БД ---
def connect():
    conn = sqlite3.connect(DB_FILE)

    # Универсальная регистронезависимая коллация (Unicode)
    def _cmp(a, b):
        a = "" if a is None else str(a)
        b = "" if b is None else str(b)
        aa = a.casefold()
        bb = b.casefold()
        return (aa > bb) - (aa < bb)  # -1, 0, 1

    conn.create_collation("UNI_NOCASE", _cmp)
    # На всякий случай функция для ручного приведения
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
            title TEXT,
            orig_name TEXT,
            author TEXT,
            description TEXT,
            lang TEXT,
            bnf_path TEXT,
            favorite INTEGER DEFAULT 0
        )
        """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_books_author_title ON books (author, title)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS book_tags (
        book_id INTEGER,
        tag_id INTEGER,
        UNIQUE(book_id, tag_id),
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
        SELECT name FROM tags
        JOIN book_tags ON tags.id = book_tags.tag_id
        WHERE book_tags.book_id=?
    """,
        (book_id,),
    )
    tags = [row[0] for row in cur.fetchall()]
    conn.close()
    return tags


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
            UPDATE books SET title=?, orig_name=?, author=?, description=?, lang=?, favorite=?, bnf_path=?
            WHERE id=?
        """,
            (title, orig_name, author, description, lang, favorite, bnf_path, book_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO books (title, orig_name, author, description, lang, favorite, bnf_path)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (title, orig_name, author, description, lang, favorite, bnf_path),
        )
        book_id = cur.lastrowid
    conn.commit()
    conn.close()
    if tags:
        save_tags(book_id, tags)


def get_books(filter_text=""):
    conn = connect()
    cur = conn.cursor()
    if filter_text:
        f = filter_text.casefold()
        cur.execute(
            """
            SELECT DISTINCT books.*
            FROM books
            LEFT JOIN book_tags ON books.id = book_tags.book_id
            LEFT JOIN tags ON tags.id = book_tags.tag_id
            WHERE UNI_LOWER(books.title)  LIKE UNI_LOWER(?)
               OR UNI_LOWER(books.orig_name) LIKE UNI_LOWER(?)
               OR UNI_LOWER(books.author) LIKE UNI_LOWER(?)
               OR UNI_LOWER(tags.name)    LIKE UNI_LOWER(?)
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


# --- GUI ---
def check_db_files_exist():
    """Удаляем из БД записи, у которых нет .bnf файла"""
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


def find_book_id(title, author):
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM books
        WHERE UNI_LOWER(title)  = UNI_LOWER(?)
          AND UNI_LOWER(author) = UNI_LOWER(?)
    """,
        (title, author),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def open_folder(file_path):
    folder = os.path.dirname(file_path)
    try:
        if os.name == "nt":  # Windows
            subprocess.Popen(["explorer", "/select,", file_path])
        elif sys.platform == "darwin":  # macOS
            subprocess.Popen(["open", "-R", file_path])
        else:  # Linux
            # пытаемся разные менеджеры
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
            # fallback — просто открыть папку
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось открыть папку: {e}")


def open_lang_file(folder, base_name, lang_code, paraline=False):
    if lang_code in ("ru", "en"):
        file_name = f"{base_name}.{lang_code}.md"
    elif lang_code == "en-ru":
        file_name = f"{base_name}.en.md"
    else:
        file_name = f"{base_name}.md"

    file_path = os.path.join(folder, file_name)
    if not os.path.exists(file_path):
        messagebox.showerror("Ошибка", f"Файл {file_path} не найден")
        return

    try:
        if paraline:
            subprocess.Popen(
                ["/home/nikolay/bin/paraline_qt", file_path],
                cwd="/home/nikolay/Projects/parallel_editor_qt",
            )
        else:
            subprocess.Popen(["ghostwriter", file_path])
    except Exception as e:
        messagebox.showerror("Ошибка", str(e))


def open_file(folder, base_name):
    file_name = f"{base_name}.md"
    file_path = os.path.join(folder, file_name)

    if not os.path.exists(file_path):
        messagebox.showerror("Ошибка", f"Файл {file_path} не найден")
        return

    try:
        subprocess.Popen(
            ["/home/nikolay/bin/md_editor_qt", file_path],
            cwd="/home/nikolay/Projects/md_editor_qt",
        )
    except Exception as e:
        messagebox.showerror("Ошибка", str(e))


class LibraryApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Book Library Manager")
        self.geometry("900x600")

        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).resolve().parent

        self.library_path = str(base_dir / "library")
        os.makedirs(self.library_path, exist_ok=True)

        self.create_widgets()
        check_db_files_exist()
        self.refresh_books()

        self.event_queue = queue.Queue()
        self.start_watcher()
        self.process_fs_events()

        self.history_path = os.path.join(
            os.path.expanduser("~"), ".bnf_tag_history.json"
        )
        self.tag_history = self._load_tag_history()

    def create_widgets(self):
        # Панель поиска
        top_frame = ttk.Frame(self)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(top_frame, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search_entry.bind("<Return>", lambda e: self.refresh_books())

        ttk.Button(top_frame, text="🔎", width=3, command=self.refresh_books).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top_frame, text="❌", width=3, command=self.reset_search).pack(
            side=tk.LEFT, padx=2
        )
        # ttk.Button(top_frame, text="Импорт .bnf", command=self.import_bnf).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            top_frame, text="Сканировать папку", command=self.scan_folder_dialog
        ).pack(side=tk.LEFT, padx=2)

        # Основная область
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Список книг
        column_widths = {
            "id": 50,
            "favorite": 30,
            "author": 100,
            "title": 300,
            "orig_name": 300,
            "lang": 50,
            "description": 500,
            "tags": 300,
        }

        self.tree = ttk.Treeview(
            main_frame,
            columns=(
                "id",
                "favorite",
                "author",
                "title",
                "orig_name",
                "lang",
                "description",
                "tags",
            ),
            show="headings",
        )
        self.sort_orders = {
            "id": True,
            "favorite": True,
            "author": True,
            "title": True,
            "orig_name": True,
            "lang": True,
        }

        self.tree.heading("id", text="ID", command=lambda: self.sort_column("id"))
        self.tree.heading(
            "favorite", text="Изб.", command=lambda: self.sort_column("favorite")
        )
        self.tree.heading(
            "author", text="Автор", command=lambda: self.sort_column("author")
        )
        self.tree.heading(
            "title", text="Название", command=lambda: self.sort_column("title")
        )
        self.tree.heading(
            "orig_name",
            text="Оригинальное название",
            command=lambda: self.sort_column("orig_name"),
        )
        self.tree.heading(
            "lang",
            text="Язык",
            command=lambda: self.sort_column("lang"),
        )
        self.tree.heading("description", text="Описание")
        self.tree.heading("tags", text="Теги")
        for col in self.tree["columns"]:
            width = column_widths.get(col, 10)
            self.tree.column(col, width=width)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_details)
        self.tree.bind("<Double-1>", self.open_file_from_list)
        self.tree.bind("<Return>", self.open_file_from_list)

        # Скроллбар
        scrollbar = ttk.Scrollbar(
            main_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)

        # Панель деталей
        self.details_text = tk.Text(main_frame, wrap=tk.WORD, width=50)
        self.details_text.pack(side=tk.RIGHT, fill=tk.BOTH)

        # Статус-бар
        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(
            fill=tk.X, side=tk.BOTTOM
        )

    def start_watcher(self):
        """Запуск watchdog в отдельном потоке"""
        event_handler = LibraryWatcher(self.event_queue)
        observer = Observer()
        observer.schedule(event_handler, self.library_path, recursive=True)
        observer_thread = threading.Thread(target=observer.start, daemon=True)
        observer_thread.start()

    def process_fs_events(self):
        """Вызывается ТОЛЬКО в главном потоке"""
        try:
            while True:
                # event_type, path = self.event_queue.get_nowait()
                sel = self.tree.selection()
                if not sel:
                    return
                book_id = self.tree.item(sel[0])["values"][0]
                self.refresh_book(book_id)
        except queue.Empty:
            pass

        self.after(200, self.process_fs_events)  # polling

    def sort_column(self, col):
        # получаем все элементы
        data = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        # сортируем
        # Определяем ключ сортировки в зависимости от имени колонки
        if col == "id":
            # Сортировка как ЧИСЛА
            def num_key(t):
                try:
                    return int(t[0])
                except ValueError:
                    # Если вдруг попалась пустая строка или не число, отправляем в конец/начало
                    return float("inf") if self.sort_orders[col] else float("-inf")

            data.sort(key=num_key, reverse=not self.sort_orders[col])
        else:
            # Сортировка как СТРОКИ
            data.sort(reverse=not self.sort_orders[col])

        for index, (val, k) in enumerate(data):
            self.tree.move(k, "", index)
        # меняем направление сортировки на противоположное
        self.sort_orders[col] = not self.sort_orders[col]

    def reset_search(self):
        self.search_var.set("")
        self.refresh_books()

    def search_by_author(self, author):
        self.search_var.set(author)
        self.tree.delete(*self.tree.get_children())
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM books
            WHERE UNI_LOWER(author) = UNI_LOWER(?)
            ORDER BY UNI_LOWER(title)
        """,
            (author,),
        )
        books = cur.fetchall()
        conn.close()

        for book in books:
            book_id, title, orig_name, author, desc, lang, bnf_path, favorite = book
            tags = ", ".join(get_tags_for_book(book_id))
            self.tree.insert(
                "",
                tk.END,
                values=(
                    book_id,
                    self.check_favorite(favorite),
                    author,
                    title,
                    orig_name,
                    lang,
                    desc,
                    tags,
                ),
            )

        self.status_var.set(f"Найдено книг автора '{author}': {len(books)}")

    def check_favorite(self, favorite):
        return "★" if favorite == 1 else "☆"

    def search_by_tag(self, tag):
        self.search_var.set(tag)
        self.tree.delete(*self.tree.get_children())
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

        for book in books:
            book_id, title, orig_name, author, desc, lang, bnf_path, favorite = book
            tags = ", ".join(get_tags_for_book(book_id))
            self.tree.insert(
                "",
                tk.END,
                values=(
                    book_id,
                    self.check_favorite(favorite),
                    author,
                    title,
                    orig_name,
                    lang,
                    desc,
                    tags,
                ),
            )

        self.status_var.set(f"Найдено книг с тегом '{tag}': {len(books)}")

    def refresh_books(self):
        selected = self.tree.selection()
        selected_book_id = None

        if selected:
            values = self.tree.item(selected[0], "values")
            selected_book_id = values[0]  # book_id

        self.tree.delete(*self.tree.get_children())
        books = get_books(self.search_var.get())
        restored_item = None

        for book in books:
            book_id, title, orig_name, author, desc, lang, bnf_path, favorite = book
            tags = ", ".join(get_tags_for_book(book_id))
            item = self.tree.insert(
                "",
                tk.END,
                values=(
                    book_id,
                    self.check_favorite(favorite),
                    author,
                    title,
                    orig_name,
                    lang,
                    desc,
                    tags,
                ),
            )
            if selected_book_id == str(book_id):
                restored_item = item
        if restored_item:
            self.tree.selection_set(restored_item)
            self.tree.focus(restored_item)
            self.tree.see(restored_item)
        self.status_var.set(f"Найдено книг: {len(books)}")

    def refresh_book(self, book_id):
        selected = self.tree.selection()
        children = self.tree.get_children()
        position = children[self.tree.index(selected[0])]
        book = get_book(book_id)
        book_id, title, orig_name, author, desc, lang, bnf_path, favorite = book
        tags = ", ".join(get_tags_for_book(book_id))
        self.tree.item(
            position,
            values=(
                book_id,
                self.check_favorite(favorite),
                author,
                title,
                orig_name,
                lang,
                desc,
                tags,
            ),
        )
        self.show_details()

    def show_details(self, *args):
        sel = self.tree.selection()
        if not sel:
            return
        book_id = self.tree.item(sel[0])["values"][0]
        book = get_book(book_id)
        if book:
            _, title, orig_name, author, desc, lang, bnf_path, favorite = book
            tags = get_tags_for_book(book_id)
            folder = os.path.dirname(bnf_path) if bnf_path else None
            base_name = (
                os.path.splitext(os.path.basename(bnf_path))[0] if bnf_path else None
            )

            self.details_text.config(state="normal")
            self.details_text.delete(1.0, tk.END)

            # Стили
            self.details_text.tag_configure(
                "label", font=("TkDefaultFont", 10, "bold"), spacing3=5
            )
            self.details_text.tag_configure("value", spacing3=5)
            self.details_text.tag_configure(
                "taglink", foreground="blue", underline=True
            )

            # Название
            self.details_text.insert(tk.END, "Название: ", "label")
            self.details_text.insert(tk.END, f"\n{title}\n", "value")

            # Оригинальное название
            self.details_text.insert(tk.END, "\nОригинальное название: ", "label")
            self.details_text.insert(tk.END, f"\n{orig_name}\n", "value")

            # Автор (ссылка)
            self.details_text.insert(tk.END, "\nАвтор: ", "label")
            start_index = self.details_text.index(tk.INSERT)
            self.details_text.insert(tk.END, f"\n{author}\n", "taglink")
            tag_name = f"authorlink_{book_id}"
            self.details_text.tag_add(
                tag_name, start_index, f"{start_index}+{len(author)}c"
            )
            self.details_text.tag_config(tag_name, foreground="blue", underline=True)
            self.details_text.tag_bind(
                tag_name, "<Button-1>", lambda e, a=author: self.search_by_author(a)
            )

            # Описание
            self.details_text.insert(tk.END, "\nОписание:\n", "label")
            self.details_text.insert(tk.END, desc, "value")

            # Избранное
            self.details_text.insert(tk.END, "\nИзбранное:\n", "label")
            self.details_text.insert(tk.END, self.check_favorite(favorite), "value")

            # Теги
            self.details_text.insert(tk.END, "\n\nТеги: ", "label")
            for i, tag in enumerate(tags):
                tag_name = f"taglink_{book_id}_{i}"
                self.details_text.insert(tk.END, tag, tag_name)
                self.details_text.tag_add(tag_name, f"end-{len(tag)}c", "end")
                self.details_text.tag_config(
                    tag_name, foreground="blue", underline=True
                )
                self.details_text.tag_bind(
                    tag_name, "<Button-1>", lambda e, t=tag: self.search_by_tag(t)
                )
                if i != len(tags) - 1:
                    self.details_text.insert(tk.END, ", ", "value")
            self.details_text.insert(tk.END, "\n", "value")

            # Язык
            self.details_text.insert(tk.END, "\nЯзык: ", "label")
            if lang in ("ru", "en"):
                langlink_name = f"langlink_{lang}"
                self.details_text.insert(tk.END, lang, langlink_name)
                self.details_text.tag_add(langlink_name, f"end-{len(lang)}c", "end")
                self.details_text.tag_config(
                    langlink_name, foreground="blue", underline=True
                )
                self.details_text.tag_bind(
                    langlink_name,
                    "<Button-1>",
                    lambda e, l=lang: open_file(folder, base_name),
                )
            elif lang == "en-ru":
                langs = [("ru", False), ("en", False), ("en-ru", True)]
                for i, (l, use_paraline) in enumerate(langs):
                    langlink_name = f"lang_link_{i}"
                    self.details_text.insert(tk.END, l, langlink_name)
                    self.details_text.tag_config(
                        langlink_name, foreground="blue", underline=True
                    )
                    self.details_text.tag_bind(
                        langlink_name,
                        "<Button-1>",
                        lambda e, ll=l, pp=use_paraline: open_lang_file(
                            folder, base_name, ll, pp
                        ),
                    )
                    if i != len(langs) - 1:
                        self.details_text.insert(tk.END, ", ", "value")
            self.details_text.insert(tk.END, "\n\n", "value")

            # --- Новое: кнопка "Открыть папку" ---
            if bnf_path and os.path.exists(bnf_path):
                folder_name = f"open_folder_{book_id}"
                text = "Открыть папку"
                self.details_text.insert(tk.END, text, folder_name)
                self.details_text.tag_add(folder_name, f"end-{len(text)}c", "end")
                self.details_text.tag_config(
                    folder_name, foreground="blue", underline=True
                )
                self.details_text.tag_bind(
                    folder_name, "<Button-1>", lambda e, f=bnf_path: open_folder(f)
                )
            # Редактировать bnf файл
            open_bnf = f"open_bnf_{book_id}"
            text = "Открыть bnf-файл"
            self.details_text.insert(tk.END, f"\n\n{text}", open_bnf)
            self.details_text.tag_add(open_bnf, f"end-{len(text)}c", "end")
            self.details_text.tag_config(open_bnf, foreground="blue", underline=True)
            self.details_text.tag_bind(
                open_bnf,
                "<Button-1>",
                lambda e, f=book_id: self.open_metadata_dialog(f),
            )

            self.details_text.config(state="disabled")

    def open_file_from_list(self, *args):
        sel = self.tree.selection()
        if not sel:
            return
        book_id = self.tree.item(sel[0])["values"][0]
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

    def open_metadata_dialog(self, book_id):
        sel = self.tree.selection()
        if not sel:
            return

        book = get_book(book_id)
        if not book:
            return

        _, title, orig_name, author, desc, lang, bnf_path, favorite = book
        tags = get_tags_for_book(book_id)

        # Переменные
        title_var = tk.StringVar(value=title)
        orig_name_var = tk.StringVar(value=orig_name)
        author_var = tk.StringVar(value=author)
        lang_var = tk.StringVar(value=lang)
        favorite_var = tk.BooleanVar(value=favorite)
        tag_entry_var = tk.StringVar()

        all_tags = sorted(self.tag_history[:])
        current_tags = tags

        dialog = tk.Toplevel(self)
        dialog.title("Метаданные книги")
        dialog.geometry("1100x750")
        dialog.resizable(False, False)
        dialog.bind("<Control-s>", lambda e: save_changes())

        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        row = 0

        # поля
        ttk.Label(main_frame, text="Название:", font=("Arial", 10, "bold")).grid(
            row=row, column=0, sticky="w", pady=5
        )
        ttk.Entry(main_frame, textvariable=title_var, width=52).grid(
            row=row, column=1, sticky="ew", padx=5, pady=5
        )
        row += 1

        if lang != "ru":
            ttk.Label(
                main_frame, text="Оригинальное\nназвание:", font=("Arial", 10, "bold")
            ).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(main_frame, textvariable=orig_name_var, width=52).grid(
                row=row, column=1, sticky="ew", padx=5, pady=5
            )
            row += 1

        ttk.Label(main_frame, text="Автор:", font=("Arial", 10, "bold")).grid(
            row=row, column=0, sticky="w", pady=5
        )
        ttk.Entry(main_frame, textvariable=author_var, width=52).grid(
            row=row, column=1, sticky="ew", padx=5, pady=5
        )
        row += 1

        ttk.Label(main_frame, text="Язык:", font=("Arial", 10, "bold")).grid(
            row=row, column=0, sticky="w", pady=5
        )
        ttk.Combobox(
            main_frame,
            textvariable=lang_var,
            values=("en-ru", "ru"),
            state="readonly",
            width=50,
        ).grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        row += 1

        ttk.Label(main_frame, text="Избранное:", font=("Arial", 10, "bold")).grid(
            row=row, column=0, sticky="w", pady=5
        )
        ttk.Checkbutton(
            main_frame,
            variable=favorite,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="ew", padx=5, pady=(8, 4))
        row += 1

        # ===== Теги =====
        ttk.Label(main_frame, text="Теги:", font=("Arial", 10, "bold")).grid(
            row=row, column=0, sticky="nw", pady=(8, 4)
        )

        tags_container = ttk.Frame(main_frame)
        tags_container.grid(row=row, column=1, sticky="ew", padx=5, pady=(8, 4))

        # Поле ввода нового тега
        tag_entry = ttk.Entry(tags_container, textvariable=tag_entry_var, width=52)
        tag_entry.pack(fill=tk.X, pady=(0, 8))

        # Контейнер для чипсов с переносом строк
        chips_wrapper = ttk.Frame(tags_container)
        chips_wrapper.pack(fill=tk.BOTH, expand=True)

        # Text-виджет позволяет чипсам переноситься на новую строку
        tags_display = tk.Text(
            chips_wrapper,
            height=16,  # примерно 16 строк чипсов
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=5,
            pady=2,
            spacing2=2,
            cursor="arrow",
            state="normal",
        )
        tags_display.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Вертикальный скролл, если чипсов очень много
        chips_scroll = ttk.Scrollbar(
            chips_wrapper, orient="vertical", command=tags_display.yview
        )
        chips_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tags_display.configure(yscrollcommand=chips_scroll.set)

        def refresh_tags_display():
            tags_display.configure(state="normal")
            tags_display.delete("1.0", tk.END)

            for tag in all_tags:
                is_active = tag in current_tags

                if is_active:
                    bg = "#2980b9"
                    fg = "white"
                else:
                    bg = "#ecf0f1"
                    fg = "#2c3e50"

                chip = tk.Frame(
                    tags_display,
                    bg=bg,
                    cursor="hand2",
                    highlightbackground="#bdc3c7",
                    highlightthickness=1,
                )

                lbl = tk.Label(
                    chip,
                    text=tag,
                    bg=bg,
                    fg=fg,
                    padx=5,
                    pady=3,
                    font=("Arial", 9),
                    cursor="hand2",
                )
                lbl.pack()

                def make_toggle(t):
                    def toggle(event=None):
                        if t in current_tags:
                            current_tags.remove(t)
                        else:
                            current_tags.append(t)
                        refresh_tags_display()

                    return toggle

                toggle_fn = make_toggle(tag)
                chip.bind("<Button-1>", toggle_fn)
                lbl.bind("<Button-1>", toggle_fn)

                # Подсветка при наведении
                def on_enter(e, c=chip, active=is_active):
                    new_bg = "#3498db" if active else "#d5dbdb"
                    c.configure(bg=new_bg)
                    for child in c.winfo_children():
                        child.configure(bg=new_bg)

                def on_leave(e, c=chip, b=bg):
                    c.configure(bg=b)
                    for child in c.winfo_children():
                        child.configure(bg=b)

                chip.bind("<Enter>", on_enter)
                chip.bind("<Leave>", on_leave)
                lbl.bind("<Enter>", on_enter)
                lbl.bind("<Leave>", on_leave)

                # Вставляем чип + маленький пробел
                tags_display.window_create(tk.END, window=chip)
                tags_display.insert(tk.END, " ")

            tags_display.configure(state="disabled")

        def on_return(event):
            tag = tag_entry_var.get().strip().lower()
            if not tag:
                return "break"

            if tag not in all_tags:
                all_tags.insert(0, tag)

            if tag not in current_tags:
                current_tags.append(tag)

            tag_entry_var.set("")
            refresh_tags_display()
            return "break"

        tag_entry.bind("<Return>", on_return)

        # Первоначальная отрисовка
        refresh_tags_display()
        row += 1

        ttk.Label(main_frame, text="Описание:", font=("Arial", 10, "bold")).grid(
            row=row, column=0, sticky="nw", pady=5
        )
        desc_frame = ttk.Frame(main_frame)
        desc_frame.grid(row=row, column=1, sticky="nsew", padx=5, pady=5)

        desc_text = tk.Text(desc_frame, height=5, wrap=tk.WORD, undo=True)
        desc_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        desc_scroll = ttk.Scrollbar(desc_frame, command=desc_text.yview)
        desc_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        desc_text.configure(yscrollcommand=desc_scroll.set)
        desc_text.insert("1.0", desc)

        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(row, weight=1)

        def save_changes():
            new_title = title_var.get()
            new_orig_name = orig_name_var.get()
            new_author = author_var.get()
            new_desc = desc_text.get("1.0", "end").strip()
            new_lang = lang_var.get().strip() or None
            new_favorite = favorite_var.get()

            self._save_tag_history(all_tags)

            # обновляем в БД
            add_or_update_book(
                new_title,
                new_orig_name,
                new_author,
                new_desc,
                new_lang,
                new_favorite,
                bnf_path,
                current_tags,
            )

            # обновляем bnf-файл
            if bnf_path and os.path.exists(bnf_path):
                try:
                    with open(bnf_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data.update(
                        {
                            "title": new_title,
                            "orig_name": new_orig_name,
                            "author": new_author,
                            "description": new_desc,
                            "lang": new_lang,
                            "favorite": new_favorite,
                            "tags": current_tags,
                        }
                    )
                    with open(bnf_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось обновить .bnf: {e}")

            self.refresh_book(book_id)
            dialog.destroy()
            DialogManager.show_dialog("Сохранено", "Метаданные успешно сохранены.")

        # Кнопки
        buttons_frame = ttk.Frame(dialog)
        buttons_frame.pack(side=tk.BOTTOM, pady=10)

        save_button = ttk.Button(buttons_frame, text="Сохранить", command=save_changes)
        save_button.pack(side=tk.LEFT, padx=5)

        cancel_button = ttk.Button(buttons_frame, text="Отмена", command=dialog.destroy)
        cancel_button.pack(side=tk.LEFT, padx=5)

    def import_bnf(self):
        filepath = filedialog.askopenfilename(filetypes=[("Info files", "*.bnf")])
        if filepath:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                add_or_update_book(
                    data.get("title", ""),
                    data.get("orig_name", ""),
                    data.get("author", ""),
                    data.get("description", ""),
                    lang=data.get("lang"),
                    favorite=data.get("favorite", False),
                    bnf_path=filepath,
                    tags=data.get("tags", []),
                )
                self.refresh_books()
                messagebox.showinfo("Импорт", f"Импортировано: {data.get('title')}")
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))

    def scan_folder_dialog(self):
        folder = filedialog.askdirectory()
        self.status_var.set("Сканирование...")
        self.scan_folder_async(folder)

    def scan_folder_async(self, folder):
        if not folder:
            return

        thread = threading.Thread(
            target=self._scan_folder_worker, args=(folder,), daemon=True
        )
        thread.start()

    def _scan_folder_worker(self, folder):
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
                            lang=data.get("lang"),
                            favorite=data.get("favorite", False),
                            bnf_path=os.path.join(root, file),
                            tags=data.get("tags", []),
                        )
                        count += 1
                    except Exception as e:
                        print(f"Ошибка {file}: {e}")

        check_db_files_exist()

        # передаём результат в главный поток
        self.after(0, self._scan_folder_done, count)

    def _scan_folder_done(self, count):
        self.refresh_books()
        messagebox.showinfo("Сканирование", f"Добавлено или обновлено {count} книг")


if __name__ == "__main__":
    init_db()
    app = LibraryApp()
    app.mainloop()
    style = ttk.Style()
    style.theme_use("clam")
