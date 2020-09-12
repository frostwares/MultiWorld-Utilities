"""Friendly reminder that if you want to host this somewhere on the internet, that it's licensed under MIT Berserker66
So unless you're Berserker you need to include license information."""

import os
import uuid
import base64

from pony.flask import Pony
from flask import Flask, request, redirect, url_for, render_template, Response, session, abort, send_from_directory
from flask_caching import Cache
from flaskext.autoversion import Autoversion
from flask_compress import Compress

from .models import *

UPLOAD_FOLDER = os.path.relpath('uploads')
LOGS_FOLDER = os.path.relpath('logs')
os.makedirs(LOGS_FOLDER, exist_ok=True)

app = Flask(__name__)
Pony(app)

app.jinja_env.filters['any'] = any
app.jinja_env.filters['all'] = all

app.config["SELFHOST"] = True
app.config["GENERATORS"] = 8  # maximum concurrent world gens
app.config["SELFLAUNCH"] = True
app.config["DEBUG"] = False
app.config["PORT"] = 80
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024  # 4 megabyte limit
# if you want persistent sessions on your server, make sure you make this a constant in your config.yaml
app.config["SECRET_KEY"] = os.urandom(32)
# at what amount of worlds should scheduling be used, instead of rolling in the webthread
app.config["JOB_THRESHOLD"] = 2
app.config['SESSION_PERMANENT'] = True

# waitress uses one thread for I/O, these are for processing of views that then get sent
# berserkermulti.world uses gunicorn + nginx; ignoring this option
app.config["WAITRESS_THREADS"] = 10
# a default that just works. berserkermulti.world runs on mariadb
app.config["PONY"] = {
    'provider': 'sqlite',
    'filename': os.path.abspath('db.db3'),
    'create_db': True
}
app.config["MAX_ROLL"] = 20
app.config["CACHE_TYPE"] = "simple"
app.autoversion = True
av = Autoversion(app)
cache = Cache(app)
Compress(app)

from werkzeug.routing import BaseConverter


class B64UUIDConverter(BaseConverter):

    def to_python(self, value):
        return uuid.UUID(bytes=base64.urlsafe_b64decode(value + '=='))

    def to_url(self, value):
        return base64.urlsafe_b64encode(value.bytes).rstrip(b'=').decode('ascii')


# short UUID
app.url_map.converters["suuid"] = B64UUIDConverter
app.jinja_env.filters['suuid'] = lambda value: base64.urlsafe_b64encode(value.bytes).rstrip(b'=').decode('ascii')


@app.before_request
def register_session():
    session.permanent = True  # technically 31 days after the last visit
    if not session.get("_id", None):
        session["_id"] = uuid4()  # uniquely identify each session without needing a login


@app.route('/tutorial')
@app.route('/tutorial/<string:lang>')
def tutorial(lang='en'):
    return render_template(f"tutorial.html", lang=lang)


@app.route('/player-settings')
def player_settings():
    return render_template("player-settings.html")


@app.route('/seed/<suuid:seed>')
def view_seed(seed: UUID):
    seed = Seed.get(id=seed)
    if not seed:
        abort(404)
    return render_template("view_seed.html", seed=seed,
                           rooms=[room for room in seed.rooms if room.owner == session["_id"]])


@app.route('/new_room/<suuid:seed>')
def new_room(seed: UUID):
    seed = Seed.get(id=seed)
    if not seed:
        abort(404)
    room = Room(seed=seed, owner=session["_id"], tracker=uuid4())
    commit()
    return redirect(url_for("host_room", room=room.id))


def _read_log(path: str):
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as log:
            yield from log
    else:
        yield f"Logfile {path} does not exist. " \
              f"Likely a crash during spinup of multiworld instance or it is still spinning up."


@app.route('/log/<suuid:room>')
def display_log(room: UUID):
    # noinspection PyTypeChecker
    return Response(_read_log(os.path.join("logs", str(room) + ".txt")), mimetype="text/plain;charset=UTF-8")


@app.route('/hosted/<suuid:room>', methods=['GET', 'POST'])
def host_room(room: UUID):
    room = Room.get(id=room)
    if room is None:
        return abort(404)
    if request.method == "POST":
        if room.owner == session["_id"]:
            cmd = request.form["cmd"]
            Command(room=room, commandtext=cmd)
            commit()

    with db_session:
        room.last_activity = datetime.utcnow()  # will trigger a spinup, if it's not already running

    return render_template("host_room.html", room=room)


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static/static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


from WebHostLib.customserver import run_server_process
from . import tracker, upload, landing, check, generate, downloads  # to trigger app routing picking up on it
