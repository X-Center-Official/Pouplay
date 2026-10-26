
import os
import re
import json
import hmac
import base64
import hashlib
import secrets
import mimetypes
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

from supabase import create_client


# ============================================================
# CONFIGURACIÓN
# ============================================================

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "10000"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

BUCKET = "pouplay-videos"

# Conserva el límite acordado de 50 MiB.
MAX_VIDEO_SIZE = 50 * 1024 * 1024

# La cookie dura 30 días.
SESSION_DURATION = 30 * 24 * 60 * 60
COOKIE_NAME = "pouplay_session"

if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_SECRET_KEY en Render."
    )

if not SESSION_SECRET:
    raise RuntimeError(
        "Añade SESSION_SECRET en Render con una clave larga y aleatoria."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)


# ============================================================
# SESIONES PERSISTENTES MEDIANTE COOKIE FIRMADA
# ============================================================

def b64encode(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64decode(data):
    return base64.urlsafe_b64decode(
        data + "=" * (-len(data) % 4)
    )


def create_session(username):
    expires = int(datetime.now(timezone.utc).timestamp()) + SESSION_DURATION

    payload = json.dumps(
        {"u": username, "e": expires},
        separators=(",", ":")
    ).encode()

    encoded = b64encode(payload)
    signature = hmac.new(
        SESSION_SECRET.encode(),
        encoded.encode(),
        hashlib.sha256
    ).digest()

    return encoded + "." + b64encode(signature)


def read_session(token):
    try:
        encoded, supplied_signature = token.split(".", 1)

        expected_signature = hmac.new(
            SESSION_SECRET.encode(),
            encoded.encode(),
            hashlib.sha256
        ).digest()

        actual_signature = b64decode(supplied_signature)

        if not hmac.compare_digest(expected_signature, actual_signature):
            return None

        data = json.loads(b64decode(encoded).decode())

        if int(data["e"]) < int(
            datetime.now(timezone.utc).timestamp()
        ):
            return None

        username = data["u"]

        if not isinstance(username, str) or not username:
            return None

        return username

    except Exception:
        return None


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        200_000
    ).hex()


def safe_username(username):
    return (
        isinstance(username, str)
        and re.fullmatch(r"[A-Za-z0-9_]{3,24}", username) is not None
    )


def get_user(username):
    result = (
        supabase.table("app_users")
        .select("id,username,email,password_hash,password_salt,created_at")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "created_at": user.get("created_at")
    }


def get_channel(channel_id):
    result = (
        supabase.table("channels")
        .select("*")
        .eq("id", channel_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_video(video_id):
    result = (
        supabase.table("videos")
        .select("*")
        .eq("id", video_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_cookie_value(handler):
    raw = handler.headers.get("Cookie", "")
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(COOKIE_NAME + "="):
            return part[len(COOKIE_NAME) + 1:]
    return ""


def get_logged_user(handler):
    token = get_cookie_value(handler)
    username = read_session(token) if token else None
    return get_user(username) if username else None


# ============================================================
# SERVIDOR HTTP
# ============================================================

class PouPlayHandler(BaseHTTPRequestHandler):

    server_version = "PouPlay/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, status, data, extra_headers=None):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def send_cookie(self, token):
        # Render normalmente termina HTTPS delante de la aplicación.
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "")
        secure = (
            forwarded_proto.lower() == "https"
            or self.headers.get("Origin", "").startswith("https://")
        )

        value = (
            f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_DURATION}; "
            "HttpOnly; SameSite=Lax"
        )
        if secure:
            value += "; Secure"

        return ("Set-Cookie", value)

    def clear_cookie(self):
        secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        value = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
        if secure:
            value += "; Secure"
        return ("Set-Cookie", value)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("La solicitud es demasiado grande.")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def require_user(self):
        user = get_logged_user(self)
        if not user:
            self.send_json(401, {"error": "Inicia sesión para continuar."})
            return None
        return user

    # --------------------------------------------------------
    # PÁGINA PRINCIPAL
    # --------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        try:
            if path == "/":
                self.send_html()
                return

            if path == "/api/me":
                user = get_logged_user(self)
                self.send_json(200, {
                    "user": public_user(user)
                })
                return

            if path == "/api/channels":
                result = (
                    supabase.table("channels")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                )
                self.send_json(200, {"channels": result.data or []})
                return

            if path == "/api/my-channels":
                user = self.require_user()
                if not user:
                    return

                result = (
                    supabase.table("channels")
                    .select("*")
                    .eq("owner_username", user["username"])
                    .order("created_at", desc=True)
                    .execute()
                )
                self.send_json(200, {"channels": result.data or []})
                return

            if path == "/api/channel":
                channel_id = params.get("id", [""])[0]
                channel = get_channel(channel_id)

                if not channel:
                    self.send_json(404, {"error": "Canal no encontrado."})
                    return

                result = (
                    supabase.table("videos")
                    .select("*")
                    .eq("channel_id", channel_id)
                    .order("created_at", desc=True)
                    .execute()
                )

                self.send_json(200, {
                    "channel": channel,
                    "videos": result.data or []
                })
                return

            if path == "/api/videos":
                result = (
                    supabase.table("videos")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                )
                self.send_json(200, {"videos": result.data or []})
                return

            if path == "/api/video":
                video_id = params.get("id", [""])[0]
                video = get_video(video_id)

                if not video:
                    self.send_json(404, {"error": "Video no encontrado."})
                    return

                self.send_json(200, {"video": video})
                return

            self.send_json(404, {"error": "Ruta no encontrada."})

        except Exception as exc:
            print("GET ERROR:", repr(exc))
            self.send_json(500, {"error": "Error interno del servidor."})

    # --------------------------------------------------------
    # ACCIONES
    # --------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            if path == "/api/register":
                data = self.read_json()
                username = str(data.get("username", "")).strip()
                email = str(data.get("email", "")).strip().lower()
                password = str(data.get("password", ""))

                if not safe_username(username):
                    self.send_json(400, {
                        "error": "El usuario debe tener entre 3 y 24 caracteres: letras, números o _."
                    })
                    return

                if (
                    len(email) > 254
                    or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
                ):
                    self.send_json(400, {"error": "Correo electrónico no válido."})
                    return

                if len(password) < 8 or len(password) > 256:
                    self.send_json(400, {
                        "error": "La contraseña debe tener al menos 8 caracteres."
                    })
                    return

                existing_username = (
                    supabase.table("app_users")
                    .select("id")
                    .eq("username", username)
                    .limit(1)
                    .execute()
                )
                existing_email = (
                    supabase.table("app_users")
                    .select("id")
                    .eq("email", email)
                    .limit(1)
                    .execute()
                )

                if existing_username.data:
                    self.send_json(409, {"error": "Ese nombre de usuario ya existe."})
                    return

                if existing_email.data:
                    self.send_json(409, {"error": "Ese correo ya está registrado."})
                    return

                salt = secrets.token_hex(16)
                user = {
                    "id": str(uuid4()),
                    "username": username,
                    "email": email,
                    "password_salt": salt,
                    "password_hash": password_hash(password, salt),
                    "created_at": now_iso()
                }

                supabase.table("app_users").insert(user).execute()

                token = create_session(username)
                self.send_json(
                    201,
                    {"user": public_user(user)},
                    [self.send_cookie(token)]
                )
                return

            if path == "/api/login":
                data = self.read_json()
                username = str(data.get("username", "")).strip()
                password = str(data.get("password", ""))

                user = get_user(username)

                if (
                    not user
                    or not hmac.compare_digest(
                        password_hash(password, user["password_salt"]),
                        user["password_hash"]
                    )
                ):
                    self.send_json(401, {
                        "error": "Usuario o contraseña incorrectos."
                    })
                    return

                token = create_session(user["username"])
                self.send_json(
                    200,
                    {"user": public_user(user)},
                    [self.send_cookie(token)]
                )
                return

            if path == "/api/logout":
                self.send_json(
                    200,
                    {"ok": True},
                    [self.clear_cookie()]
                )
                return

            if path == "/api/create-channel":
                user = self.require_user()
                if not user:
                    return

                data = self.read_json()
                name = str(data.get("name", "")).strip()
                description = str(data.get("description", "")).strip()

                if not name or len(name) > 80:
                    self.send_json(400, {
                        "error": "El nombre del canal debe tener entre 1 y 80 caracteres."
                    })
                    return

                channel = {
                    "id": str(uuid4()),
                    "name": name,
                    "owner_username": user["username"],
                    "description": description[:2000],
                    "created_at": now_iso()
                }

                supabase.table("channels").insert(channel).execute()
                self.send_json(201, {"channel": channel})
                return

            if path == "/api/toggle-like":
                user = self.require_user()
                if not user:
                    return

                data = self.read_json()
                video_id = str(data.get("video_id", ""))

                if not get_video(video_id):
                    self.send_json(404, {"error": "Video no encontrado."})
                    return

                existing = (
                    supabase.table("video_likes")
                    .select("id")
                    .eq("username", user["username"])
                    .eq("video_id", video_id)
                    .limit(1)
                    .execute()
                )

                if existing.data:
                    supabase.table("video_likes").delete().eq(
                        "id", existing.data[0]["id"]
                    ).execute()
                    liked = False
                else:
                    supabase.table("video_likes").insert({
                        "id": str(uuid4()),
                        "username": user["username"],
                        "video_id": video_id,
                        "created_at": now_iso()
                    }).execute()
                    liked = True

                self.send_json(200, {"liked": liked})
                return

            if path == "/api/toggle-subscribe":
                user = self.require_user()
                if not user:
                    return

                data = self.read_json()
                channel_id = str(data.get("channel_id", ""))

                if not get_channel(channel_id):
                    self.send_json(404, {"error": "Canal no encontrado."})
                    return

                existing = (
                    supabase.table("subscriptions")
                    .select("id")
                    .eq("username", user["username"])
                    .eq("channel_id", channel_id)
                    .limit(1)
                    .execute()
                )

                if existing.data:
                    supabase.table("subscriptions").delete().eq(
                        "id", existing.data[0]["id"]
                    ).execute()
                    subscribed = False
                else:
                    supabase.table("subscriptions").insert({
                        "id": str(uuid4()),
                        "username": user["username"],
                        "channel_id": channel_id,
                        "created_at": now_iso()
                    }).execute()
                    subscribed = True

                self.send_json(200, {"subscribed": subscribed})
                return

            if path == "/api/upload":
                self.handle_upload()
                return

            self.send_json(404, {"error": "Ruta no encontrada."})

        except Exception as exc:
            print("POST ERROR:", repr(exc))
            self.send_json(500, {
                "error": "Error interno del servidor. Revisa los registros de Render."
            })

    # --------------------------------------------------------
    # SUBIDA DE VIDEOS
    # --------------------------------------------------------

    def handle_upload(self):
        user = self.require_user()
        if not user:
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            self.send_json(400, {"error": "Se esperaba una subida multipart/form-data."})
            return

        try:
            request_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Tamaño de solicitud no válido."})
            return

        # Permite un margen para los campos del formulario y el límite multipart.
        if request_length <= 0 or request_length > MAX_VIDEO_SIZE + 1024 * 1024:
            self.send_json(413, {
                "error": "La solicitud supera el límite de subida de PouPlay."
            })
            return

        body = self.rfile.read(request_length)
        message = (
            b"Content-Type: " + content_type.encode("utf-8") +
            b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        parsed = BytesParser(policy=default).parsebytes(message)

        fields = {}
        file_bytes = None
        filename = "video.mp4"
        mime_type = "video/mp4"

        for part in parsed.iter_parts():
            name = part.get_param("name", header="content-disposition")
            raw = part.get_payload(decode=True) or b""

            if name == "file":
                file_bytes = raw
                filename = part.get_filename() or "video.mp4"
                mime_type = part.get_content_type() or "video/mp4"
            elif name:
                fields[name] = raw.decode("utf-8", errors="replace")

        if file_bytes is None:
            self.send_json(400, {"error": "No se encontró el archivo de video."})
            return

        if len(file_bytes) > MAX_VIDEO_SIZE:
            self.send_json(413, {
                "error": (
                    f"El video tiene {len(file_bytes):,} bytes. "
                    f"El máximo permitido es {MAX_VIDEO_SIZE:,} bytes."
                )
            })
            return

        title = fields.get("title", "").strip()
        description = fields.get("description", "").strip()
        channel_id = fields.get("channel_id", "").strip()

        if not title or len(title) > 150:
            self.send_json(400, {
                "error": "El título debe tener entre 1 y 150 caracteres."
            })
            return

        channel = get_channel(channel_id)
        if not channel:
            self.send_json(404, {"error": "Canal no encontrado."})
            return

        if channel["owner_username"] != user["username"]:
            self.send_json(403, {
                "error": "Solo puedes subir videos a tu propio canal."
            })
            return

        if not mime_type.startswith("video/"):
            self.send_json(400, {
                "error": "El archivo debe ser un video compatible."
            })
            return

        extension = os.path.splitext(filename)[1].lower()
        if not extension or len(extension) > 10:
            extension = mimetypes.guess_extension(mime_type) or ".mp4"

        video_id = str(uuid4())
        storage_path = f"{channel_id}/{video_id}{extension}"

        try:
            supabase.storage.from_(BUCKET).upload(
                storage_path,
                file_bytes,
                {
                    "content-type": mime_type,
                    "cache-control": "3600",
                    "upsert": "false"
                }
            )

            video_url = supabase.storage.from_(BUCKET).get_public_url(
                storage_path
            )

            video = {
                "id": video_id,
                "channel_id": channel_id,
                "title": title,
                "description": description[:5000],
                "video_url": video_url,
                "created_at": now_iso()
            }

            supabase.table("videos").insert(video).execute()
            self.send_json(201, {"video": video})

        except Exception as exc:
            print("UPLOAD ERROR:", repr(exc))
            self.send_json(500, {
                "error": (
                    "No se pudo guardar el video. Revisa el bucket de "
                    "Supabase y el límite de almacenamiento."
                )
            })

    # --------------------------------------------------------
    # INTERFAZ WEB
    # --------------------------------------------------------

    def send_html(self):
        html = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#111827">
<title>PouPlay</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#111827;color:#f9fafb;font:15px Arial,sans-serif}
header{background:#1f2937;padding:15px 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header strong{font-size:24px;color:#a78bfa}
main{max-width:1100px;margin:auto;padding:18px}
button,input,textarea,select{font:inherit;border-radius:8px;padding:10px;border:1px solid #4b5563}
input,textarea,select{background:#111827;color:white;width:100%;margin:5px 0 12px}
button{background:#7c3aed;color:white;border:0;cursor:pointer}
button:hover{background:#6d28d9}
button.secondary{background:#374151}
section{background:#1f2937;border-radius:12px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.row>*{flex:1}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.item{background:#111827;border-radius:10px;padding:12px;min-width:0}
.item video{width:100%;max-height:180px;background:black;border-radius:8px}
.muted{color:#9ca3af}
.hidden{display:none!important}
#message{padding:10px;color:#fde68a}
a{color:#c4b5fd;overflow-wrap:anywhere}
label{display:block}
h2{margin-top:0}
</style>
</head>
<body>
<header>
<strong>PouPlay</strong>
<button class="secondary" onclick="loadHome()">Inicio</button>
<button class="secondary" onclick="loadChannels()">Canales</button>
<span style="flex:1"></span>
<span id="who" class="muted">Cargando...</span>
<button id="logout" class="secondary hidden" onclick="logout()">Cerrar sesión</button>
</header>
<main>
<div id="message"></div>

<section id="auth">
<h2>Iniciar sesión o registrarse</h2>
<div class="row">
<div>
<h3>Iniciar sesión</h3>
<label>Usuario<input id="loginUser" autocomplete="username"></label>
<label>Contraseña<input id="loginPass" type="password" autocomplete="current-password"></label>
<button onclick="login()">Entrar</button>
</div>
<div>
<h3>Crear cuenta</h3>
<label>Usuario (3–24 caracteres)<input id="regUser" autocomplete="username"></label>
<label>Correo electrónico<input id="regEmail" type="email" autocomplete="email"></label>
<label>Contraseña (mínimo 8 caracteres)<input id="regPass" type="password" autocomplete="new-password"></label>
<button onclick="register()">Registrarse</button>
</div>
</div>
</section>

<section id="channelForm" class="hidden">
<h2>Crear un canal</h2>
<label>Nombre<input id="channelName" maxlength="80"></label>
<label>Descripción<textarea id="channelDescription" maxlength="2000"></textarea></label>
<button onclick="createChannel()">Crear canal</button>
</section>

<section id="uploadForm" class="hidden">
<h2>Subir video</h2>
<p class="muted">Tamaño máximo: 50 MiB (52,428,800 bytes).</p>
<label>Tu canal<select id="uploadChannel"></select></label>
<label>Título<input id="videoTitle" maxlength="150"></label>
<label>Descripción<textarea id="videoDescription" maxlength="5000"></textarea></label>
<label>Archivo de video<input id="videoFile" type="file" accept="video/*"></label>
<p id="fileInfo" class="muted"></p>
<button id="uploadButton" onclick="uploadVideo()">Subir video</button>
</section>

<section>
<h2 id="contentTitle">Videos recientes</h2>
<div id="content" class="grid"></div>
</section>
</main>
<script>
const MAX_VIDEO_SIZE = 50 * 1024 * 1024;
let currentUser = null;
let myChannels = [];

async function api(path, options={}) {
  const config = {...options, credentials:"same-origin"};
  if (config.body && !(config.body instanceof FormData)) {
    config.headers = {...config.headers, "Content-Type":"application/json"};
  }
  const response = await fetch(path, config);
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.error || "Ocurrió un error.");
  return data;
}

function msg(text) {
  document.getElementById("message").textContent = text || "";
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[c]);
}

function showUser() {
  document.getElementById("who").textContent =
    currentUser ? "Conectado como " + currentUser.username : "No has iniciado sesión";
  document.getElementById("auth").classList.toggle("hidden", !!currentUser);
  document.getElementById("logout").classList.toggle("hidden", !currentUser);
  document.getElementById("channelForm").classList.toggle("hidden", !currentUser);
  document.getElementById("uploadForm").classList.toggle("hidden", !currentUser);
}

async function refreshUser() {
  try {
    const data = await api("/api/me");
    currentUser = data.user;
    showUser();
    if (currentUser) await loadMyChannels();
  } catch {
    currentUser = null;
    showUser();
  }
}

async function login() {
  try {
    const data = await api("/api/login", {
      method:"POST",
      body:JSON.stringify({
        username:document.getElementById("loginUser").value,
        password:document.getElementById("loginPass").value
      })
    });
    currentUser = data.user;
    showUser();
    await loadMyChannels();
    msg("¡Has iniciado sesión!");
    await loadHome();
  } catch(e) { msg(e.message); }
}

async function register() {
  try {
    const data = await api("/api/register", {
      method:"POST",
      body:JSON.stringify({
        username:document.getElementById("regUser").value,
        email:document.getElementById("regEmail").value,
        password:document.getElementById("regPass").value
      })
    });
    currentUser = data.user;
    showUser();
    await loadMyChannels();
    msg("¡Cuenta creada!");
    await loadHome();
  } catch(e) { msg(e.message); }
}

async function logout() {
  try { await api("/api/logout", {method:"POST"}); }
  catch {}
  currentUser = null;
  myChannels = [];
  showUser();
  msg("Has cerrado sesión.");
  await loadHome();
}

async function loadMyChannels() {
  if (!currentUser) return;
  const data = await api("/api/my-channels");
  myChannels = data.channels || [];
  const select = document.getElementById("uploadChannel");
  select.innerHTML = myChannels.map(c =>
    `<option value="${escapeHTML(c.id)}">${escapeHTML(c.name)}</option>`
  ).join("");
}

async function createChannel() {
  try {
    await api("/api/create-channel", {
      method:"POST",
      body:JSON.stringify({
        name:document.getElementById("channelName").value,
        description:document.getElementById("channelDescription").value
      })
    });
    document.getElementById("channelName").value = "";
    document.getElementById("channelDescription").value = "";
    msg("¡Canal creado!");
    await loadMyChannels();
    await loadChannels();
  } catch(e) { msg(e.message); }
}

document.getElementById("videoFile").addEventListener("change", () => {
  const file = document.getElementById("videoFile").files[0];
  const info = document.getElementById("fileInfo");
  const button = document.getElementById("uploadButton");
  if (!file) { info.textContent = ""; button.disabled = false; return; }

  info.textContent = `${file.name} — ${file.size.toLocaleString()} bytes`;
  if (file.size > MAX_VIDEO_SIZE) {
    info.textContent += " — EXCEDE EL LÍMITE DE 50 MiB";
    button.disabled = true;
  } else {
    button.disabled = false;
  }
});

async function uploadVideo() {
  const file = document.getElementById("videoFile").files[0];
  if (!file) { msg("Selecciona un video."); return; }
  if (file.size > MAX_VIDEO_SIZE) {
    msg("El video supera el máximo de 50 MiB.");
    return;
  }
  if (!document.getElementById("uploadChannel").value) {
    msg("Primero crea un canal.");
    return;
  }

  const form = new FormData();
  form.append("file", file);
  form.append("title", document.getElementById("videoTitle").value);
  form.append("description", document.getElementById("videoDescription").value);
  form.append("channel_id", document.getElementById("uploadChannel").value);

  const button = document.getElementById("uploadButton");
  button.disabled = true;
  msg("Subiendo video...");

  try {
    const response = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.withCredentials = true;
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch {}
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data.error || "No se pudo subir el video."));
      };
      xhr.onerror = () => reject(new Error("Error de conexión."));
      xhr.send(form);
    });

    document.getElementById("videoTitle").value = "";
    document.getElementById("videoDescription").value = "";
    document.getElementById("videoFile").value = "";
    document.getElementById("fileInfo").textContent = "";
    msg("¡Video subido!");
    await loadHome();
  } catch(e) { msg(e.message); }
  finally { button.disabled = false; }
}

function videoCard(v) {
  return `<article class="item">
    <video controls preload="metadata" src="${escapeHTML(v.video_url)}"></video>
    <h3>${escapeHTML(v.title)}</h3>
    <p class="muted">${escapeHTML(v.description || "")}</p>
    <button onclick="likeVideo('${escapeHTML(v.id)}')">Me gusta</button>
    <button class="secondary" onclick="openChannel('${escapeHTML(v.channel_id)}')">Ver canal</button>
  </article>`;
}

async function loadHome() {
  document.getElementById("contentTitle").textContent = "Videos recientes";
  try {
    const data = await api("/api/videos");
    document.getElementById("content").innerHTML =
      (data.videos || []).map(videoCard).join("") || "<p>Aún no hay videos.</p>";
  } catch(e) { msg(e.message); }
}

async function loadChannels() {
  document.getElementById("contentTitle").textContent = "Canales";
  try {
    const data = await api("/api/channels");
    document.getElementById("content").innerHTML = (data.channels || []).map(c =>
      `<article class="item">
        <h3>${escapeHTML(c.name)}</h3>
        <p class="muted">${escapeHTML(c.description || "")}</p>
        <p>Creado por ${escapeHTML(c.owner_username)}</p>
        <button onclick="openChannel('${escapeHTML(c.id)}')">Ver canal</button>
        <button class="secondary" onclick="subscribe('${escapeHTML(c.id)}')">Suscribirme</button>
      </article>`
    ).join("") || "<p>Aún no hay canales.</p>";
  } catch(e) { msg(e.message); }
}

async function openChannel(id) {
  try {
    const data = await api("/api/channel?id=" + encodeURIComponent(id));
    document.getElementById("contentTitle").textContent = data.channel.name;
    document.getElementById("content").innerHTML =
      `<p>${escapeHTML(data.channel.description || "")}</p>` +
      (data.videos || []).map(videoCard).join("");
  } catch(e) { msg(e.message); }
}

async function likeVideo(id) {
  try {
    const data = await api("/api/toggle-like", {
      method:"POST", body:JSON.stringify({video_id:id})
    });
    msg(data.liked ? "Te gusta este video." : "Quitaste tu me gusta.");
  } catch(e) { msg(e.message); }
}

async function subscribe(id) {
  try {
    const data = await api("/api/toggle-subscribe", {
      method:"POST", body:JSON.stringify({channel_id:id})
    });
    msg(data.subscribed ? "Te has suscrito al canal." : "Te has desuscrito.");
  } catch(e) { msg(e.message); }
}

(async function init() {
  await refreshUser();
  await loadHome();
})();
</script>
</body>
</html>"""
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    print("========================================")
    print("PouPlay - Servidor")
    print("Sesiones persistentes mediante cookies firmadas.")
    print("Límite de video: 50 MiB.")
    print("========================================")
    ThreadingHTTPServer((HOST, PORT), PouPlayHandler).serve_forever()
