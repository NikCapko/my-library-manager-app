import json
import os
import queue
import re
import sqlite3
import sys
import threading
from pathlib import Path

import markdown
from flask import (
    Flask,
    abort,
    make_response,
    redirect,
    render_template_string,
    request,
    url_for,
)
from markdown import Extension
from markdown.blockprocessors import HashHeaderProcessor
from markdown.extensions.toc import TocExtension
from watchdog.observers import Observer

from library_watcher import LibraryWatcher

DB_FILE = "library.db"

app = Flask(__name__)

# --- HTML шаблоны ---
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Библиотека</title>
    <style>
        body { font-family: sans-serif; margin: 20px; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; }
        th { background: #f2f2f2; }
        a { text-decoration: none; color: blue; }
        .tag { color: blue; cursor: pointer; }
        .lang-btn { margin-right: 10px; padding: 4px 8px; border: 1px solid #ccc; background: #f8f8f8; display: inline-block; }
    </style>
</head>
<body>
    <h1>Библиотека</h1>
    <a href="/update_books" style="margin-left:10px;">Обновить библиотеку</a>
    <br>
    <form method="get">
        <input type="search" name="q" placeholder="Поиск..." value="{{ query }}">
        <button type="submit">Искать</button>
        {% if query or tag or author or favorite %}
        <a href="/" style="margin-left:10px;">Сброс</a>
        {% endif %}
        <a href="/?favorite=1" style="margin-left:10px;">Избранное</a>
    </form>
    <br>
    {% if tags %}
    <div style="margin:10px 0;">
      {% for t in tags %}
        <span style="display:inline-block; background:#e0e0e0; padding:5px 10px; margin:3px; border-radius:15px; font-size:14px;">
          {{ t }}
          <a href="/?{% for tt in tags if tt != t %}tag={{tt}}&{% endfor %}"
             style="margin-left:5px; color:red; text-decoration:none;">✕</a>
        </span>
      {% endfor %}
    </div>
    {% endif %}
    <table>
        <tr>
            <th><a href="/?sort=id{% if query %}&q={{ query }}{% endif %}{% if tag %}&tag={{ tag }}{% endif %}{% if author %}&author={{ author }}{% endif %}">ID</a></th>
            <th>★</th>
            <th><a href="/?sort=author{% if query %}&q={{ query }}{% endif %}{% if tag %}&tag={{ tag }}{% endif %}{% if author %}&author={{ author }}{% endif %}">Автор</a></th>
            <th><a href="/?sort=title{% if query %}&q={{ query }}{% endif %}{% if tag %}&tag={{ tag }}{% endif %}{% if author %}&author={{ author }}{% endif %}">Название</a></th>
            <th>Оригинальное название</th>
            <th>Язык</th>
            <th>Описание</th>
            <th>Теги</th>
        </tr>
        {% for book in books %}
        <tr>
            <td>{{ book['id'] }}</td>
            <td>
              {% if book['favorite'] %}
                <a href="/toggle_fav/{{ book['id'] }}">⭐</a>
              {% else %}
                <a href="/toggle_fav/{{ book['id'] }}">☆</a>
              {% endif %}
            </td>
            <td><a href="/?author={{ book['author'] }}" style="color:blue">{{ book['author'] }}</a></td>
            <td><a href="/book/{{ book['id'] }}">{{ book['title'] }}</a></td>
            <td>{{ book['orig_name'] }}</td>
            <td>{{ book['lang'] }}</td>
            <td>{{ book['description'] }}</td>
            <td>
                {% for tag in book['tags'] %}
                    <a
                    style="display:inline-block; background:#e0e0e0; padding:5px 10px; margin:3px; border-radius:15px; font-size:14px;"
                     href="/?{% for t in request.args.getlist('tag') %}tag={{t}}&{% endfor %}tag={{ tag }}" style="color:blue">{{ tag }}</a>
                {% endfor %}
            </td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

BOOK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
        <p><a href="/">Назад к списку</a></p>
        <p><a href="/edit/{{ book['id'] }}">Редактировать</a></p>
    <title>{{ book['title'] }}</title>
    <style>
        body { font-family: sans-serif; margin: 20px; font-size: 16px; }
        .markdown-body h1, .markdown-body h2, .markdown-body h3 { margin-top: 20px; }
        .markdown-body p { margin: 10px 0; }
        .markdown-body code { background: #f4f4f4; padding: 2px 4px; border-radius: 4px; }
        .markdown-body pre { background: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; }
        .markdown-body table { border-collapse: collapse; width: 100%; }
        .markdown-body th, .markdown-body td { border: 1px solid #ccc; padding: 5px; }

        /* Кнопка скролла в начало */
        .scroll-top-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 50px;
            height: 50px;
            background: #333;
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transition: opacity 0.3s ease, visibility 0.3s ease;
        }

        .scroll-top-btn:hover {
            background: #555;
        }

        .scroll-top-btn:active {
            background: #777;
        }

        .scroll-top-btn.show {
            opacity: 1;
            visibility: visible;
        }

        .scroll-top-btn i {
            font-size: 24px;
        }

        pre { white-space: pre-wrap; word-wrap: break-word; border: 1px solid #ccc; padding: 10px; background: #fafafa; font-size: 16px; }
        .lang-btn {
            margin-right: 10px;
            padding: 6px 10px;
            border: 1px solid #ccc;
            background: #f8f8f8;
            display: inline-block;
            text-decoration: none;
            color: black;
            font-size: 16px;
        }
        table { border-collapse: collapse; width: 100%; font-size: 16px; }
        th, td { border: 1px solid #ccc; padding: 5px; vertical-align: top; }
        th { background: #f2f2f2; }
    </style>
</head>
<body>
    <div class="scroll-top-btn" id="scrollTopBtn">
        <i>↑</i>
    </div>

    <h1>{{ book['title'] }}</h1>

    {% if book['orig_name'] %}
        <h2>{{ book['orig_name'] }}</h2>
    {% endif %}

     <script>
        // Показываем кнопку, когда пользователь прокрутил более чем на половину страницы
        const scrollBtn = document.getElementById('scrollTopBtn');
        window.addEventListener('scroll', () => {
            if (document.body.scrollTop > window.innerHeight / 2 ||
                document.documentElement.scrollTop > window.innerHeight / 2) {
                scrollBtn.classList.add('show');
            } else {
                scrollBtn.classList.remove('show');
            }
        });

        // Возвращаем пользователя в начало страницы при клике на кнопку
        scrollBtn.addEventListener('click', () => {
            window.scrollTo({
                top: 0,
                behavior: 'smooth'
            });
        });
    </script>

    <p><b>Автор:</b> <a href="/?author={{ book['author'] }}" style="color:blue">{{ book['author'] }}</a></p>
    <p><b>Теги:</b>
      {% for tag in book['tags'] %}
        <a style="display:inline-block; background:#e0e0e0; padding:5px 10px; margin:3px; border-radius:15px; font-size:14px;"
        href="/?{% for t in tags %}tag={{t}}&{% endfor %}tag={{ tag }}" style="color:blue">{{ tag }}</a>
      {% endfor %}
    </p>
    <p><b>Описание:</b></p>
    <div style="margin:10px 0; padding:10px; border:1px solid #ccc; background:#fafafa;">
        {{ book['description'] }}
    </div>
    <p><b>Язык:</b> {{ book['lang'] }}</p>
    <p><b>Избранное:</b>
        {% if book['favorite'] %}
            <a href="/toggle_fav/{{ book['id'] }}?from=book" style="font-size:22px; text-decoration:none;">⭐</a>
        {% else %}
            <a href="/toggle_fav/{{ book['id'] }}?from=book" style="font-size:22px; text-decoration:none;">☆</a>
        {% endif %}
    </p>


    {% if book['lang'] == "en-ru" %}
        <div>
            <a href="/book/{{ book['id'] }}?ver=ru" class="lang-btn">RU</a>
            <a href="/book/{{ book['id'] }}?ver=en" class="lang-btn">EN</a>
            <a href="/book/{{ book['id'] }}?ver=en-ru" class="lang-btn">EN-RU</a>
        </div>
    {% endif %}
    <hr>

    {% if parallel %}
    {{ content|safe }}
    {% else %}
        <div class="markdown-body">{{ content|safe }}</div>
    {% endif %}
</body>
</html>
"""

EDIT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Редактировать {{ book['title'] }}</title>
    <style>
        body { font-family: sans-serif; margin: 20px; font-size: 18px; }
        label { display: block; margin-top: 10px; font-weight: bold; }
        input, textarea, select { width: 100%; padding: 8px; margin-top: 5px; font-size: 16px; }
        button { margin-top: 15px; padding: 8px 12px; font-size: 16px; }
    </style>
</head>
<body>
    <h1>Редактировать книгу</h1>
    <form method="post">
        <label>Название:</label>
        <input type="text" name="title" value="{{ book['title'] }}">

        {% if book['lang']=="en-ru" %}
            <label>Оригинальное название:</label>
            <input type="text" name="orig_name" value="{{ book['orig_name'] }}">
        {% endif %}

        <label>Автор:</label>
        <input type="text" name="author" value="{{ book['author'] }}">

        <label>Описание:</label>
        <textarea name="description" rows="6">{{ book['description'] }}</textarea>

        <label>Теги (через запятую):</label>
        <input type="text" name="tags" value="{{ tags }}">

        <label>Язык:</label>
        <select name="lang">
            <option value="ru" {% if book['lang']=="ru" %}selected{% endif %}>ru</option>
            <option value="en" {% if book['lang']=="en" %}selected{% endif %}>en</option>
            <option value="en-ru" {% if book['lang']=="en-ru" %}selected{% endif %}>en-ru</option>
        </select>

        <button type="submit">Сохранить</button>
    </form>
    <p><a href="/book/{{ book['id'] }}">Назад</a></p>
</body>
</html>
"""

UPDATE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
        <p><a href="/">Назад к списку</a></p>
    <title>Обновление библиотеки</title>
</head>
<body>
<h1>Обновление библиотеки</h1>
</body>
</html>
"""


# --- БД ---
def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # ✅ строки как словари

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


def get_books(query=None, tags=None, author=None, sort="title", favorite=False):
    if sort not in ("title", "author", "id"):
        sort = "title"

    conn = connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if tags and len(tags) > 0:
        placeholders = ",".join("?" for _ in tags)
        sql = f"""
            SELECT books.* FROM books
            JOIN book_tags ON books.id = book_tags.book_id
            JOIN tags ON tags.id = book_tags.tag_id
            WHERE tags.name IN ({placeholders})
            GROUP BY books.id
            HAVING COUNT(DISTINCT tags.name) = ?
            ORDER BY books.{sort} COLLATE UNI_NOCASE
        """
        params = list(tags) + [len(tags)]
        cur.execute(sql, tuple(params))

    else:
        # старый код для остальных случаев
        sql = "SELECT DISTINCT books.* FROM books "
        joins = []
        where = []
        params = []

        if author:
            where.append("books.author = ? COLLATE UNI_NOCASE")
            params.append(author)
        if query:
            joins.append(
                "LEFT JOIN book_tags ON books.id = book_tags.book_id LEFT JOIN tags ON tags.id = book_tags.tag_id"
            )
            where.append(
                "(UNI_LOWER(books.title) LIKE UNI_LOWER(?) "
                + "OR UNI_LOWER(books.orig_name) LIKE UNI_LOWER(?) "
                + "OR UNI_LOWER(books.author) LIKE UNI_LOWER(?) "
                + "OR UNI_LOWER(tags.name) LIKE UNI_LOWER(?)) "
                + "OR UNI_LOWER(books.description) LIKE UNI_LOWER(?)"
            )
            params += [
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
            ]
        if favorite:
            where.append("books.favorite=1")

        if joins:
            sql += " " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)

        sql += f" ORDER BY books.{sort} COLLATE UNI_NOCASE"
        cur.execute(sql, tuple(params))

    rows = cur.fetchall()
    conn.close()

    books = []
    for row in rows:
        books.append(
            {
                "id": row["id"],
                "title": row["title"],
                "orig_name": row["orig_name"],
                "description": row["description"],
                "author": row["author"],
                "lang": row["lang"],
                "tags": get_tags_for_book(row["id"]),
                "favorite": row["favorite"],
            }
        )
    return books


def get_book(id):
    conn = connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM books WHERE id=?", (id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "orig_name": row["orig_name"],
        "author": row["author"],
        "description": row["description"],
        "lang": row["lang"],
        "bnf_path": row["bnf_path"],
        "favorite": row["favorite"],
        "tags": get_tags_for_book(row["id"]),
    }


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
    tags = [r[0] for r in cur.fetchall()]
    conn.close()
    return tags


# --- Маршруты ---
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    author = request.args.get("author", "").strip()
    sort = request.args.get("sort", "title")
    favorite = request.args.get("favorite", "")

    tags = request.args.getlist("tag")  # список выбранных тегов
    books = get_books(
        query=q if q else None,
        tags=tags if tags else None,
        author=author if author else None,
        sort=sort,
        favorite=(favorite == "1"),
    )

    return make_response(
        render_template_string(
            BASE_HTML,
            books=books,
            query=q,
            tags=tags,
            author=author,
            sort=sort,
            favorite=(favorite == "1"),
        )
    )


@app.route("/book/<int:book_id>")
def view_book(book_id):
    ver = request.args.get("ver")
    book = get_book(book_id)
    if not book:
        abort(404)

    folder = os.path.dirname(book["bnf_path"])
    base_name = os.path.splitext(os.path.basename(book["bnf_path"]))[0]

    content = ""
    parallel = False

    if book["lang"] in ("ru", "en"):
        file_path = os.path.join(folder, f"{base_name}.md")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                md_text = f.read()
            md = markdown.Markdown(
                extensions=[StrictHeadersExtension(), TocExtension(), "nl2br"]
            )
            html_content = md.convert(md_text)
            toc_html = md.toc
            content = f"""
                                        <div class="toc">{toc_html}</div>
                                        {html_content}
                                    """
        else:
            content = f"[Файл {file_path} не найден]"

    elif book["lang"] == "en-ru":
        if ver == "ru":
            file_path = os.path.join(folder, f"{base_name}.ru.md")
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    if file_path.endswith(".md"):
                        with open(file_path, "r", encoding="utf-8") as f:
                            md_text = f.read()
                        md = markdown.Markdown(
                            extensions=[
                                StrictHeadersExtension(),
                                TocExtension(),
                                "nl2br",
                            ]
                        )
                        html_content = md.convert(md_text)
                        toc_html = md.toc
                        content = f"""
                            <div class="toc">{toc_html}</div>
                            {html_content}
                        """
                    else:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f"<pre>{f.read()}</pre>"
            else:
                content = f"[Файл {file_path} не найден]"
        elif ver == "en":
            file_path = os.path.join(folder, f"{base_name}.en.md")
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    if file_path.endswith(".md"):
                        with open(file_path, "r", encoding="utf-8") as f:
                            md_text = f.read()
                        md = markdown.Markdown(
                            extensions=[
                                StrictHeadersExtension(),
                                TocExtension(),
                                "nl2br",
                            ]
                        )
                        html_content = md.convert(md_text)
                        toc_html = md.toc
                        content = f"""
                                          <div class="toc">{toc_html}</div>
                                          {html_content}
                                          """
                    else:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f"<pre>{f.read()}</pre>"
            else:
                content = f"[Файл {file_path} не найден]"
        else:  # ver == "en-ru"
            en_file = os.path.join(folder, f"{base_name}.en.md")
            ru_file = os.path.join(folder, f"{base_name}.ru.md")
            if os.path.exists(en_file) and os.path.exists(ru_file):
                with open(en_file, "r", encoding="utf-8") as f:
                    en_lines = [line.strip() for line in f.readlines()]
                with open(ru_file, "r", encoding="utf-8") as f:
                    ru_lines = [line.strip() for line in f.readlines()]

                max_len = max(len(en_lines), len(ru_lines))
                en_lines += [""] * (max_len - len(en_lines))
                ru_lines += [""] * (max_len - len(ru_lines))

                # Формируем HTML-таблицу параллельно
                table_html = "<table border='1' cellpadding='5' style='border-collapse: collapse; width:100%;'>"
                table_html += "<tr><th style='width:50%;'>EN</th><th style='width:50%;'>RU</th></tr>"
                for en_line, ru_line in zip(en_lines, ru_lines):
                    table_html += f"<tr><td>{en_line}</td><td>{ru_line}</td></tr>"
                table_html += "</table>"
                content = table_html
                parallel = True
            else:
                content = "[Файлы EN и RU не найдены]"

    return render_template_string(
        BOOK_HTML, book=book, content=content, parallel=parallel
    )


@app.route("/edit/<int:book_id>", methods=["GET", "POST"])
def edit_book(book_id):
    book = get_book(book_id)
    if not book:
        abort(404)

    if request.method == "POST":
        title = request.form["title"].strip()
        orig_name = request.form.get("orig_name", "").strip()
        author = request.form["author"].strip()
        description = request.form["description"].strip()
        lang = request.form["lang"].strip()
        tags = [t.strip().lower() for t in request.form["tags"].split(",") if t.strip()]

        # --- обновляем в БД ---
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE books SET title=?, orig_name=?, author=?, description=?, lang=?
            WHERE id=?
        """,
            (title, orig_name, author, description, lang, book_id),
        )
        cur.execute("DELETE FROM book_tags WHERE book_id=?", (book_id,))
        conn.commit()
        conn.close()

        conn = connect()
        try:
            cur = conn.cursor()
            for tag in tags:
                cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
                cur.execute("SELECT id FROM tags WHERE name=?", (tag,))
                tag_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO book_tags (book_id, tag_id) VALUES (?, ?)",
                    (book_id, tag_id),
                )

            conn.commit()
        except Exception as e:
            return f"<p>Ошибка при обновлении БД: {e}</p>"
        finally:
            conn.close()

        # --- обновляем .bnf файл ---
        bnf_path = book["bnf_path"]
        try:
            import json

            if os.path.exists(bnf_path):
                with open(bnf_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            data["title"] = title
            data["orig_name"] = orig_name
            data["author"] = author
            data["description"] = description
            data["lang"] = lang
            data["tags"] = tags
            with open(bnf_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            return f"<p>Ошибка при обновлении BNF: {e}</p>"

        return f"<meta http-equiv='refresh' content='0; url=/book/{book_id}'>"

    tags = ", ".join(book["tags"])
    return render_template_string(EDIT_HTML, book=book, tags=tags)


@app.route("/toggle_fav/<int:book_id>")
def toggle_fav(book_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT favorite FROM books WHERE id=?", (book_id,))
    row = cur.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        cur.execute("UPDATE books SET favorite=? WHERE id=?", (new_val, book_id))
        conn.commit()
    conn.close()

    # куда вернуться
    back = request.args.get("from", "list")
    if back == "book":
        return redirect(url_for("view_book", book_id=book_id))
    else:
        params = {
            "q": request.args.get("q"),
            "tag": request.args.get("tag"),
            "author": request.args.get("author"),
            "favorite": request.args.get("favorite"),
        }
        # оставляем только непустые параметры
        params = {k: v for k, v in params.items() if v}

        return redirect(url_for("index", **params))


@app.route("/update_books")
def scan_folder_async():
    thread = threading.Thread(
        target=scan_folder_worker, args=(get_library_path(),), daemon=True
    )
    thread.start()

    return render_template_string(UPDATE_HTML)


def scan_folder_worker(folder):
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
                        bnf_path=os.path.join(root, file),
                        tags=data.get("tags", []),
                    )
                    count += 1
                except Exception as e:
                    print(f"Ошибка {file}: {e}")

    check_db_files_exist()


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


def add_or_update_book(
    title, orig_name, author, description, lang=None, bnf_path=None, tags=None
):
    book_id = find_book_id(title, author)
    conn = connect()
    cur = conn.cursor()
    if book_id:
        cur.execute(
            """
            UPDATE books SET title=?, orig_name=?, author=?, description=?, lang=?, bnf_path=?
            WHERE id=?
        """,
            (title, orig_name, author, description, lang, bnf_path, book_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO books (title, orig_name, author, description, lang, bnf_path)
            VALUES (?, ?, ?, ?, ?, ?)
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


class StrictHeaderProcessor(HashHeaderProcessor):
    """Обрабатывает только заголовки с пробелом после #"""

    RE = re.compile(r"(?:^|\n)(?P<level>#{1,6})\s+(?P<header>.*?)\s*#*(\n|$)")


class StrictHeadersExtension(Extension):
    """Расширение для строгих заголовков"""

    def extendMarkdown(self, md):
        md.parser.blockprocessors.register(
            StrictHeaderProcessor(md.parser), "strict_hashheader", 70
        )
        # Удаляем стандартный процессор заголовков
        md.parser.blockprocessors.deregister("hashheader")


def start_watcher():
    event_handler = LibraryWatcher(event_queue)
    observer = Observer()
    observer.schedule(event_handler, library_path, recursive=True)
    observer.daemon = True
    observer.start()
    print(f"Запущено наблюдение за {library_path}")

    # чтобы не блокировать Flask — в отдельном потоке
    t = threading.Thread(target=lambda: observer.join())
    t.daemon = True
    t.start()


def get_library_path():
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent

    return str(base_dir / "library")


if __name__ == "__main__":
    library_path = get_library_path()
    os.makedirs(library_path, exist_ok=True)

    # event_queue = queue.Queue()
    # start_watcher()
    app.run(host="0.0.0.0", port=5050, debug=True)
