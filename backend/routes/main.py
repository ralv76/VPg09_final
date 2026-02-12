"""Main UI routes (frontend pages). ТЗ 3.4, 2.2.7 — защита входа."""
from flask import Blueprint, render_template, request, redirect, url_for, session

from backend.config import LOGIN_USERNAME, LOGIN_PASSWORD

main_bp = Blueprint("main", __name__)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    """Вход по одному логину/паролю (без регистрации). ТЗ 2.2.7."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
            session["logged_in"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("main.index")
            return redirect(next_url)
        return render_template("login.html", error="Неверный логин или пароль")
    return render_template("login.html")


@main_bp.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("main.login"))


@main_bp.route("/")
def index():
    return render_template("index.html")


@main_bp.route("/create")
def create():
    return render_template("create.html")


@main_bp.route("/result/<task_id>")
def result(task_id):
    return render_template("result.html", task_id=task_id)


@main_bp.route("/podcasts")
def podcasts():
    """Страница со списком всех сгенерированных подкастов."""
    return render_template("podcasts.html")
