import json
import sqlite3

from watchdog.events import FileSystemEventHandler

DB_FILE = "library.db"


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


def handle_file_event(file):
    if file.endswith(".bnf"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
                print(data)
            add_or_update_book(
                data.get("title", ""),
                data.get("orig_name", ""),
                data.get("author", ""),
                data.get("description", ""),
                lang=data.get("lang"),
                bnf_path=file,
                tags=data.get("tags", []),
            )
        except Exception as e:
            print("error on loading file")
            pass


def add_or_update_book(
    title, orig_name, author, description, lang=None, bnf_path=None, tags=None
):
    book_id = find_book_id(title, author)
    conn = connect()
    cur = conn.cursor()
    if book_id:
        print("updating book id")
        cur.execute(
            """
            UPDATE books SET title=?, orig_name=?, author=?, description=?, lang=?, bnf_path=?
            WHERE id=?
        """,
            (title, orig_name, author, description, lang, bnf_path, book_id),
        )
    else:
        print("creating new book")
        cur.execute(
            """
            INSERT OR IGNORE INTO books (title, orig_name, author, description, lang, bnf_path)
            VALUES (?, ?, ?, ?, ?)
        """,
            (title, orig_name, author, description, lang, bnf_path),
        )
        book_id = cur.lastrowid
    conn.commit()
    conn.close()
    if tags:
        save_tags(book_id, tags)


def find_book_id(title, author):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM books WHERE title=? AND author=?", (title, author))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def save_tags(book_id, tags):
    conn = connect()
    cur = conn.cursor()

    # приводим список тегов к уникальному виду, убираем пробелы
    new_tags = {t.strip() for t in tags}

    # получаем текущие теги книги
    cur.execute(
        """
        SELECT t.name
        FROM tags t
        JOIN book_tags bt ON t.id = bt.tag_id
        WHERE bt.book_id = ?
    """,
        (book_id,),
    )
    current_tags = {row[0] for row in cur.fetchall()}

    # теги для удаления и добавления
    to_delete = current_tags - new_tags
    to_add = new_tags - current_tags

    # удаляем ненужные связи
    if to_delete:
        cur.execute(
            """
            DELETE FROM book_tags
            WHERE book_id = ?
              AND tag_id IN (
                SELECT id FROM tags WHERE name IN ({})
              )
        """.format(",".join("?" * len(to_delete))),
            (book_id, *to_delete),
        )

    # добавляем новые
    for tag in to_add:
        cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
        cur.execute("SELECT id FROM tags WHERE name=?", (tag,))
        tag_id = cur.fetchone()[0]
        cur.execute(
            "INSERT OR IGNORE INTO book_tags (book_id, tag_id) VALUES (?, ?)",
            (book_id, tag_id),
        )

    conn.commit()
    conn.close()


def remove_book_from_db(file):
    """Удалить запись о книге, если удалён .bnf"""
    if file.endswith(".bnf"):
        conn = connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM books WHERE bnf_path=?", (file,))
        conn.commit()
        conn.close()
        print(f"Удалена книга из БД (файл {file})")


class LibraryWatcher(FileSystemEventHandler):
    def __init__(self, queue):
        self.queue = queue

    def on_created(self, event):
        print(f"on_created {event.src_path}")
        if not event.is_directory:
            handle_file_event(event.src_path)
            self.queue.put(("created", event.src_path))

    def on_deleted(self, event):
        print(f"on_deleted {event.src_path}")
        if not event.is_directory:
            remove_book_from_db(event.src_path)
            self.queue.put(("deleted", event.src_path))

    def on_modified(self, event):
        print(f"on_modified {event.src_path}")
        if not event.is_directory:
            handle_file_event(event.src_path)
            self.queue.put(("modified", event.src_path))
