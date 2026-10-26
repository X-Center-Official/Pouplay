
import os
import re
import json
import hmac
import uuid
import hashlib
import secrets
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from email.parser import BytesParser
from email.policy import default
from http.cookies import SimpleCookie

from supabase import create_client

# ============================================================
# POUPLAY - VERSION SUPABASE
# ============================================================

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))

# LÍMITE EXACTO: 50 MiB = 52,428,800 bytes.
# Se acepta exactamente ese tamaño.
# Cualquier archivo mayor se rechaza.
MAX_VIDEO_SIZE = 50 * 1024 * 1024

BUCKET = "pouplay-videos"
SESSION_COOKIE = "pouplay_session"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError(
        "Configura SUPABASE_URL y SUPABASE_SECRET_KEY "
        "en las variables de entorno de Render."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
SESSIONS = {}

logging.basicConfig(level=logging.INFO)


def db(table):
    return supabase.table(table)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        250000
    ).hex()


def public_video_url(path):
    return supabase.storage.from_(BUCKET).get_public_url(path)


def get_user_by_name(username):
    rows = (
        db("app_users")
        .select("*")
        .ilike("username", username)
        .limit(10)
        .execute()
        .data or []
    )

    for user in rows:
        if user["username"].lower() == username.lower():
            return user

    return None


def get_channel(channel_id):
    rows = (
        db("channels")
        .select("*")
        .eq("id", channel_id)
        .limit(1)
        .execute()
        .data or []
    )
    return rows[0] if rows else None


def get_video(video_id):
    rows = (
        db("videos")
        .select("*")
        .eq("id", video_id)
        .limit(1)
        .execute()
        .data or []
    )
    return rows[0] if rows else None


def video_data(video, viewer=None):
    channel = get_channel(video["channel_id"])

    likes = (
        db("video_likes")
        .select("id")
        .eq("video_id", video["id"])
        .execute()
        .data or []
    )

    liked = False

    if viewer:
        liked = bool(
            db("video_likes")
            .select("id")
            .eq("video_id", video["id"])
            .eq("username", viewer)
            .limit(1)
            .execute()
            .data
        )

    return {
        "id": video["id"],
        "title": video["title"],
        "description": video.get("description", ""),
        "url": video["video_url"],
        "likes": len(likes),
        "liked": liked,
        "channel": channel["name"] if channel else "Canal eliminado",
        "channelId": video["channel_id"],
        "channelOwner": channel["owner_username"] if channel else "",
        "createdAt": video.get("created_at")
    }


def channel_data(channel, viewer=None):
    channel_id = channel["id"]

    video_rows = (
        db("videos")
        .select("*")
        .eq("channel_id", channel_id)
        .order("created_at", desc=True)
        .execute()
        .data or []
    )

    subscriptions = (
        db("subscriptions")
        .select("id")
        .eq("channel_id", channel_id)
        .execute()
        .data or []
    )

    subscribed = False

    if viewer:
        subscribed = bool(
            db("subscriptions")
            .select("id")
            .eq("username", viewer)
            .eq("channel_id", channel_id)
            .limit(1)
            .execute()
            .data
        )

    return {
        "id": channel_id,
        "name": channel["name"],
        "description": channel.get("description", ""),
        "owner": channel["owner_username"],
        "subscribers": len(subscriptions),
        "subscribed": subscribed,
        "videos": [video_data(v, viewer) for v in video_rows],
        "createdAt": channel.get("created_at")
    }


HTML = r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#151923">
<title>PouPlay</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#10131a;color:#f3f4f8;font:15px Arial,sans-serif}
header{position:sticky;top:0;z-index:5;background:#171b25;
display:flex;align-items:center;gap:12px;padding:12px 16px;
border-bottom:1px solid #303646}
.logo{font-size:24px;font-weight:bold;color:#72b7ff;cursor:pointer}
button,input,textarea,select{font:inherit}
button{border:0;border-radius:9px;padding:10px 13px;
background:#30394b;color:white;cursor:pointer}
button:hover{filter:brightness(1.15)}
button:disabled{opacity:.55;cursor:not-allowed}
.primary{background:#3478f6}
input,textarea,select{width:100%;padding:12px;border-radius:9px;
border:1px solid #343b4d;background:#1a1f2b;color:white}
textarea{min-height:90px;resize:vertical}
.grow{flex:1;min-width:150px}
main{max-width:1100px;margin:auto;padding:18px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
.card{background:#1a1f2b;border:1px solid #2c3343;border-radius:13px;overflow:hidden}
.card video{width:100%;aspect-ratio:16/9;object-fit:contain;background:#080a0f}
.cardbody{padding:13px}
.muted{color:#aab3c5;font-size:13px}
h1,h2,h3{margin-top:8px}
section{margin:20px 0}
.hidden{display:none!important}
.panel{max-width:520px;margin:25px auto;padding:20px;
background:#1a1f2b;border-radius:14px}
.panel input,.panel textarea,.panel select{margin:7px 0}
video.big{width:100%;max-height:65vh;background:#050609;border-radius:12px}
#status{color:#ffd37c;overflow-wrap:anywhere}
a{color:#83baff}
progress{height:16px}
@media(max-width:600px){
 header{flex-wrap:wrap}
 main{padding:12px}
 .grid{grid-template-columns:1fr}
}
</style>
</head>
<body>
<header>
  <div class="logo" onclick="home()">🐾 PouPlay</div>
  <input id="search" class="grow" placeholder="Buscar videos o canales..." oninput="render()">
  <button onclick="home()">Inicio</button>
  <button onclick="showPage('channels')">Canales</button>
  <button onclick="showPage('upload')">Subir video</button>
  <button id="accountBtn" onclick="showPage('account')">Entrar</button>
</header>

<main>
<div id="status" role="status"></div>

<section id="page-home">
  <h2>Videos recientes</h2>
  <div id="videos" class="grid"></div>
</section>

<section id="page-channels" class="hidden">
  <div class="row">
    <h2 class="grow">Canales</h2>
    <button class="primary" onclick="showPage('create-channel')">Crear canal</button>
  </div>
  <div id="channels" class="grid"></div>
</section>

<section id="page-channel" class="hidden">
  <button onclick="showPage('channels')">← Canales</button>
  <div id="channelInfo"></div>
  <div id="channelVideos" class="grid"></div>
</section>

<section id="page-video" class="hidden">
  <button onclick="home()">← Volver</button>
  <div id="videoInfo"></div>
</section>

<section id="page-account" class="hidden">
  <div class="panel">
    <h2 id="accountTitle">Iniciar sesión</h2>
    <input id="username" placeholder="Nombre de usuario" maxlength="24" autocomplete="username">
    <input id="email" class="hidden" placeholder="Correo electrónico (opcional)" type="email">
    <input id="password" placeholder="Contraseña" type="password" maxlength="128" autocomplete="current-password">
    <button class="primary" id="authBtn" onclick="auth()">Entrar</button>
    <button id="switchAuth" onclick="switchAuth()">Crear cuenta</button>
    <div id="accountMessage" class="muted"></div>
    <div id="loggedIn" class="hidden">
      <p id="userLabel"></p>
      <button onclick="logout()">Cerrar sesión</button>
    </div>
  </div>
</section>

<section id="page-upload" class="hidden">
  <div class="panel">
    <h2>Subir video</h2>
    <p class="muted">
      Formato MP4. Tamaño máximo: 50 MiB
      (52,428,800 bytes) por video.
    </p>
    <input id="videoTitle" placeholder="Título" maxlength="120">
    <textarea id="videoDescription" placeholder="Descripción" maxlength="5000"></textarea>
    <select id="channelSelect"></select>
    <input id="videoFile" type="file" accept="video/mp4,.mp4">
    <p id="fileSize" class="muted">No has seleccionado ningún video.</p>
    <button class="primary" id="uploadBtn" onclick="uploadVideo()">Subir video</button>
    <p id="uploadMessage" class="muted"></p>
    <progress id="uploadProgress" class="hidden" max="100" value="0" style="width:100%"></progress>
  </div>
</section>

<section id="page-create-channel" class="hidden">
  <div class="panel">
    <h2>Crear canal</h2>
    <input id="channelName" placeholder="Nombre del canal" maxlength="40">
    <textarea id="channelDescription" placeholder="Descripción" maxlength="1000"></textarea>
    <button class="primary" onclick="createChannel()">Crear canal</button>
  </div>
</section>
</main>

<script>
const MAX_VIDEO_SIZE = 50 * 1024 * 1024;
let me = null;
let videos = [];
let channels = [];
let registering = false;
let activeChannel = null;
let uploading = false;

async function api(path, options={}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body instanceof FormData ? {} : {"Content-Type":"application/json"})
    }
  });

  let data = {};
  try { data = await response.json(); } catch {}

  if (!response.ok) {
    throw new Error(data.error || "Error del servidor.");
  }

  return data;
}

function msg(text) {
  document.getElementById("status").textContent = text || "";
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;",
    "<":"&lt;",
    ">":"&gt;",
    '"':"&quot;",
    "'":"&#39;"
  }[c]));
}

function showPage(name) {
  document.querySelectorAll("main>section").forEach(section => {
    section.classList.add("hidden");
  });

  const page = document.getElementById("page-" + name);
  if (page) page.classList.remove("hidden");

  msg("");

  if (name === "channels") loadChannels();
  if (name === "upload") fillChannelSelect();
  if (name === "account") updateAccount();
}

function home() {
  showPage("home");
  loadVideos();
}

function videoCard(v) {
  return `<article class="card">
    <video controls preload="metadata" src="${esc(v.url)}"></video>
    <div class="cardbody">
      <h3><a href="#" onclick="openVideo('${esc(v.id)}');return false">${esc(v.title)}</a></h3>
      <p class="muted">
        <a href="#" onclick="openChannel('${esc(v.channelId)}');return false">${esc(v.channel)}</a>
      </p>
      <div class="row">
        <button onclick="likeVideo('${esc(v.id)}')">${v.liked ? "♥" : "♡"} ${v.likes}</button>
        <span class="muted">${v.createdAt ? new Date(v.createdAt).toLocaleDateString() : ""}</span>
      </div>
    </div>
  </article>`;
}

async function loadVideos() {
  try {
    videos = (await api("/api/videos")).videos || [];
    render();
  } catch (e) {
    msg(e.message);
  }
}

function render() {
  const q = (document.getElementById("search").value || "").toLowerCase();
  const filtered = videos.filter(v =>
    (v.title + " " + v.channel + " " + v.description).toLowerCase().includes(q)
  );

  document.getElementById("videos").innerHTML =
    filtered.map(videoCard).join("") ||
    '<p class="muted">Todavía no hay videos.</p>';
}

async function loadChannels() {
  try {
    channels = (await api("/api/channels")).channels || [];

    document.getElementById("channels").innerHTML = channels.map(c =>
      `<article class="card"><div class="cardbody">
        <h3>${esc(c.name)}</h3>
        <p class="muted">${esc(c.description)}</p>
        <p class="muted">Creado por ${esc(c.owner)} · ${c.subscribers} suscriptores</p>
        <button class="primary" onclick="openChannel('${esc(c.id)}')">Ver canal</button>
      </div></article>`
    ).join("") || '<p class="muted">Aún no hay canales.</p>';

    fillChannelSelect();
  } catch (e) {
    msg(e.message);
  }
}

async function openChannel(id) {
  try {
    const c = (await api("/api/channel?id=" + encodeURIComponent(id))).channel;
    activeChannel = c;
    showPage("channel");

    document.getElementById("channelInfo").innerHTML =
      `<h2>${esc(c.name)}</h2>
       <p>${esc(c.description)}</p>
       <p class="muted">Propietario: ${esc(c.owner)} · ${c.subscribers} suscriptores</p>
       <button class="primary" onclick="subscribe('${esc(c.id)}')">
       ${c.subscribed ? "Cancelar suscripción" : "Suscribirse"}</button>`;

    document.getElementById("channelVideos").innerHTML =
      (c.videos || []).map(videoCard).join("") ||
      '<p class="muted">Este canal aún no tiene videos.</p>';
  } catch (e) {
    msg(e.message);
  }
}

async function openVideo(id) {
  try {
    const v = (await api("/api/video?id=" + encodeURIComponent(id))).video;
    showPage("video");

    document.getElementById("videoInfo").innerHTML =
      `<h2>${esc(v.title)}</h2>
       <video class="big" controls autoplay src="${esc(v.url)}"></video>
       <p><a href="#" onclick="openChannel('${esc(v.channelId)}');return false">${esc(v.channel)}</a></p>
       <p>${esc(v.description)}</p>
       <button onclick="likeVideo('${esc(v.id)}')">${v.liked ? "♥" : "♡"} ${v.likes} Me gusta</button>`;
  } catch (e) {
    msg(e.message);
  }
}

async function likeVideo(id) {
  if (!me) {
    showPage("account");
    msg("Inicia sesión para dar Me gusta.");
    return;
  }

  try {
    await api("/api/toggle-like", {
      method: "POST",
      body: JSON.stringify({videoId:id})
    });

    await loadVideos();

    if (activeChannel) await openChannel(activeChannel.id);
  } catch (e) {
    msg(e.message);
  }
}

async function subscribe(id) {
  if (!me) {
    showPage("account");
    msg("Inicia sesión para suscribirte.");
    return;
  }

  try {
    await api("/api/toggle-subscribe", {
      method: "POST",
      body: JSON.stringify({channelId:id})
    });

    await openChannel(id);
    await loadChannels();
  } catch (e) {
    msg(e.message);
  }
}

function switchAuth() {
  registering = !registering;

  document.getElementById("accountTitle").textContent =
    registering ? "Crear cuenta" : "Iniciar sesión";

  document.getElementById("authBtn").textContent =
    registering ? "Registrarse" : "Entrar";

  document.getElementById("switchAuth").textContent =
    registering ? "Ya tengo cuenta" : "Crear cuenta";

  document.getElementById("email").classList.toggle("hidden", !registering);
  document.getElementById("password").autocomplete =
    registering ? "new-password" : "current-password";
}

async function auth() {
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const email = document.getElementById("email").value.trim();

  try {
    const result = await api(registering ? "/api/register" : "/api/login", {
      method: "POST",
      body: JSON.stringify({username, password, email})
    });

    me = result.user;
    updateAccount();
    msg("Sesión iniciada.");
    home();
  } catch (e) {
    document.getElementById("accountMessage").textContent = e.message;
  }
}

function updateAccount() {
  document.getElementById("accountBtn").textContent = me ? me.username : "Entrar";
  document.getElementById("loggedIn").classList.toggle("hidden", !me);
  document.getElementById("userLabel").textContent =
    me ? "Sesión iniciada como " + me.username : "";

  document.getElementById("authBtn").classList.toggle("hidden", !!me);
  document.getElementById("switchAuth").classList.toggle("hidden", !!me);
  document.getElementById("username").classList.toggle("hidden", !!me);
  document.getElementById("password").classList.toggle("hidden", !!me);
  document.getElementById("email").classList.add("hidden");
  document.getElementById("accountMessage").textContent = "";
}

async function logout() {
  try {
    await api("/api/logout", {method:"POST", body:"{}"});
    me = null;
    updateAccount();
    home();
  } catch (e) {
    msg(e.message);
  }
}

async function createChannel() {
  try {
    const result = await api("/api/create-channel", {
      method:"POST",
      body:JSON.stringify({
        name:document.getElementById("channelName").value.trim(),
        description:document.getElementById("channelDescription").value.trim()
      })
    });

    msg("Canal creado.");
    await loadChannels();
    await openChannel(result.channel.id);
  } catch (e) {
    msg(e.message);
  }
}

async function fillChannelSelect() {
  const select = document.getElementById("channelSelect");

  if (!me) {
    select.innerHTML = '<option value="">Inicia sesión primero</option>';
    return;
  }

  try {
    channels = (await api("/api/my-channels")).channels || [];

    select.innerHTML = channels.map(c =>
      `<option value="${esc(c.id)}">${esc(c.name)}</option>`
    ).join("");

    if (!channels.length) {
      select.innerHTML = '<option value="">Primero crea un canal</option>';
    }
  } catch (e) {
    msg(e.message);
  }
}

/* Mostrar tamaño del archivo y comprobar el límite al seleccionarlo. */
document.getElementById("videoFile").addEventListener("change", function() {
  const file = this.files[0];
  const output = document.getElementById("fileSize");
  const uploadBtn = document.getElementById("uploadBtn");

  if (!file) {
    output.textContent = "No has seleccionado ningún video.";
    uploadBtn.disabled = false;
    return;
  }

  output.textContent =
    "Tamaño: " + file.size.toLocaleString("es-MX") +
    " bytes (" + (file.size / (1024 * 1024)).toFixed(2) + " MiB).";

  if (file.size > MAX_VIDEO_SIZE) {
    output.textContent += " RECHAZADO: supera el máximo de 50 MiB.";
    uploadBtn.disabled = true;
  } else {
    output.textContent += " Tamaño permitido.";
    uploadBtn.disabled = false;
  }
});

async function uploadVideo() {
  if (uploading) return;

  const file = document.getElementById("videoFile").files[0];
  const title = document.getElementById("videoTitle").value.trim();
  const description = document.getElementById("videoDescription").value.trim();
  const channelId = document.getElementById("channelSelect").value;
  const output = document.getElementById("uploadMessage");
  const progress = document.getElementById("uploadProgress");
  const button = document.getElementById("uploadBtn");

  if (!me) {
    output.textContent = "Inicia sesión primero.";
    return;
  }

  if (!channelId) {
    output.textContent = "Crea un canal primero.";
    return;
  }

  if (!file || !title) {
    output.textContent = "Escribe un título y selecciona un MP4.";
    return;
  }

  if (!file.name.toLowerCase().endsWith(".mp4")) {
    output.textContent = "Solo se permiten archivos MP4.";
    return;
  }

  /*
   * VALIDACIÓN EXACTA EN EL NAVEGADOR:
   * 52,428,800 bytes: permitido.
   * 52,428,801 bytes: rechazado.
   */
  if (file.size > MAX_VIDEO_SIZE) {
    output.textContent =
      "No se puede subir: el máximo es 50 MiB (52,428,800 bytes).";
    button.disabled = true;
    return;
  }

  const data = new FormData();
  data.append("title", title);
  data.append("description", description);
  data.append("channelId", channelId);
  data.append("video", file);

  uploading = true;
  button.disabled = true;
  output.textContent = "Preparando subida...";
  progress.classList.remove("hidden");
  progress.value = 0;

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.withCredentials = true;

  xhr.upload.onprogress = event => {
    if (event.lengthComputable) {
      progress.value = event.loaded / event.total * 100;
      output.textContent =
        "Subiendo video: " + Math.round(progress.value) + "%";
    }
  };

  xhr.onload = async () => {
    let result = {};
    try {
      result = JSON.parse(xhr.responseText);
    } catch {}

    uploading = false;
    button.disabled = false;
    progress.classList.add("hidden");

    if (xhr.status >= 200 && xhr.status < 300) {
      output.textContent = "¡Video subido correctamente!";
      document.getElementById("videoTitle").value = "";
      document.getElementById("videoDescription").value = "";
      document.getElementById("videoFile").value = "";
      document.getElementById("fileSize").textContent =
        "No has seleccionado ningún video.";

      await loadVideos();
      showPage("home");
    } else {
      output.textContent = result.error || "No se pudo subir el video.";
    }
  };

  xhr.onerror = () => {
    uploading = false;
    button.disabled = false;
    progress.classList.add("hidden");
    output.textContent = "Error de conexión al subir el video.";
  };

  xhr.send(data);
}

async function start() {
  try {
    const result = await api("/api/me");
    me = result.user || null;
  } catch {
    me = null;
  }

  updateAccount();
  await loadVideos();
}

start();
</script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "PouPlay/3.0"

    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)

    def send_json(self, status, data, extra_headers=None):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")

        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)

        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))

        if length > 1024 * 1024:
            raise ValueError("Solicitud demasiado grande.")

        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def current_username(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)

        if not morsel:
            return None

        return SESSIONS.get(morsel.value)

    def require_user(self):
        username = self.current_username()

        if not username:
            self.send_json(401, {"error": "Inicia sesión primero."})
            return None

        return username

    def cookie_header(self, token, max_age):
        secure = (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

        value = (
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
            f"SameSite=Lax; Max-Age={max_age}"
        )

        if secure:
            value += "; Secure"

        return value

    def do_GET(self):
        path = urlparse(self.path).path
        username = self.current_username()

        try:
            if path in ("/", "/index.html"):
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/me":
                user = get_user_by_name(username) if username else None

                return self.send_json(200, {
                    "user": {"username": user["username"]} if user else None
                })

            if path == "/api/channels":
                rows = (
                    db("channels")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                    .data or []
                )

                result = []

                for channel in rows:
                    subs = (
                        db("subscriptions")
                        .select("id")
                        .eq("channel_id", channel["id"])
                        .execute()
                        .data or []
                    )

                    result.append({
                        "id": channel["id"],
                        "name": channel["name"],
                        "description": channel.get("description", ""),
                        "owner": channel["owner_username"],
                        "subscribers": len(subs)
                    })

                return self.send_json(200, {"channels": result})

            if path == "/api/my-channels":
                if not username:
                    return self.send_json(401, {
                        "error": "Inicia sesión."
                    })

                rows = (
                    db("channels")
                    .select("*")
                    .eq("owner_username", username)
                    .order("created_at", desc=True)
                    .execute()
                    .data or []
                )

                return self.send_json(200, {"channels": rows})

            if path == "/api/channel":
                channel_id = parse_qs(
                    urlparse(self.path).query
                ).get("id", [""])[0]

                channel = get_channel(channel_id)

                if not channel:
                    return self.send_json(404, {
                        "error": "Canal no encontrado."
                    })

                return self.send_json(200, {
                    "channel": channel_data(channel, username)
                })

            if path == "/api/videos":
                rows = (
                    db("videos")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                    .data or []
                )

                return self.send_json(200, {
                    "videos": [video_data(v, username) for v in rows]
                })

            if path == "/api/video":
                video_id = parse_qs(
                    urlparse(self.path).query
                ).get("id", [""])[0]

                video = get_video(video_id)

                if not video:
                    return self.send_json(404, {
                        "error": "Video no encontrado."
                    })

                return self.send_json(200, {
                    "video": video_data(video, username)
                })

            return self.send_json(404, {"error": "Ruta no encontrada."})

        except Exception:
            logging.exception("Error en GET %s", path)

            return self.send_json(500, {
                "error": "Error interno. Revisa los registros de Render."
            })

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            if path == "/api/register":
                data = self.read_json()

                username = str(data.get("username", "")).strip()
                password = str(data.get("password", ""))
                email = str(data.get("email", "")).strip() or None

                if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", username):
                    return self.send_json(400, {
                        "error": "El usuario debe tener entre 3 y 24 caracteres."
                    })

                if not 6 <= len(password) <= 128:
                    return self.send_json(400, {
                        "error": "La contraseña debe tener entre 6 y 128 caracteres."
                    })

                if email and (
                    len(email) > 254 or not re.fullmatch(
                        r"[^@\s]+@[^@\s]+\.[^@\s]+", email
                    )
                ):
                    return self.send_json(400, {
                        "error": "Correo electrónico no válido."
                    })

                if get_user_by_name(username):
                    return self.send_json(409, {
                        "error": "Ese usuario ya existe."
                    })

                salt = secrets.token_hex(16)

                record = {
                    "username": username,
                    "email": email,
                    "password_hash": password_hash(password, salt),
                    "password_salt": salt
                }

                db("app_users").insert(record).execute()

                token = secrets.token_urlsafe(32)
                SESSIONS[token] = username

                return self.send_json(201, {
                    "user": {"username": username}
                }, {
                    "Set-Cookie": self.cookie_header(
                        token, 60 * 60 * 24 * 30
                    )
                })

            if path == "/api/login":
                data = self.read_json()

                username = str(data.get("username", "")).strip()
                password = str(data.get("password", ""))

                user = get_user_by_name(username)

                if not user or not hmac.compare_digest(
                    user["password_hash"],
                    password_hash(password, user["password_salt"])
                ):
                    return self.send_json(401, {
                        "error": "Usuario o contraseña incorrectos."
                    })

                token = secrets.token_urlsafe(32)
                SESSIONS[token] = user["username"]

                return self.send_json(200, {
                    "user": {"username": user["username"]}
                }, {
                    "Set-Cookie": self.cookie_header(
                        token, 60 * 60 * 24 * 30
                    )
                })

            if path == "/api/logout":
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                morsel = cookie.get(SESSION_COOKIE)

                if morsel:
                    SESSIONS.pop(morsel.value, None)

                return self.send_json(200, {"ok": True}, {
                    "Set-Cookie": self.cookie_header("", 0)
                })

            if path == "/api/create-channel":
                username = self.require_user()

                if not username:
                    return

                data = self.read_json()
                name = str(data.get("name", "")).strip()
                description = str(data.get("description", "")).strip()

                if not 2 <= len(name) <= 40:
                    return self.send_json(400, {
                        "error": "El nombre del canal debe tener entre 2 y 40 caracteres."
                    })

                if len(description) > 1000:
                    return self.send_json(400, {
                        "error": "Descripción demasiado larga."
                    })

                existing = (
                    db("channels")
                    .select("id,name")
                    .execute()
                    .data or []
                )

                if any(c["name"].lower() == name.lower() for c in existing):
                    return self.send_json(409, {
                        "error": "Ese canal ya existe."
                    })

                result = (
                    db("channels")
                    .insert({
                        "name": name,
                        "owner_username": username,
                        "description": description
                    })
                    .execute()
                    .data
                )

                return self.send_json(201, {"channel": result[0]})

            if path == "/api/toggle-like":
                username = self.require_user()

                if not username:
                    return

                video_id = str(self.read_json().get("videoId", ""))

                if not get_video(video_id):
                    return self.send_json(404, {
                        "error": "Video no encontrado."
                    })

                existing = (
                    db("video_likes")
                    .select("id")
                    .eq("username", username)
                    .eq("video_id", video_id)
                    .execute()
                    .data or []
                )

                if existing:
                    (
                        db("video_likes")
                        .delete()
                        .eq("id", existing[0]["id"])
                        .execute()
                    )
                    liked = False
                else:
                    db("video_likes").insert({
                        "username": username,
                        "video_id": video_id
                    }).execute()
                    liked = True

                return self.send_json(200, {"liked": liked})

            if path == "/api/toggle-subscribe":
                username = self.require_user()

                if not username:
                    return

                channel_id = str(self.read_json().get("channelId", ""))

                if not get_channel(channel_id):
                    return self.send_json(404, {
                        "error": "Canal no encontrado."
                    })

                existing = (
                    db("subscriptions")
                    .select("id")
                    .eq("username", username)
                    .eq("channel_id", channel_id)
                    .execute()
                    .data or []
                )

                if existing:
                    (
                        db("subscriptions")
                        .delete()
                        .eq("id", existing[0]["id"])
                        .execute()
                    )
                    subscribed = False
                else:
                    db("subscriptions").insert({
                        "username": username,
                        "channel_id": channel_id
                    }).execute()
                    subscribed = True

                return self.send_json(200, {"subscribed": subscribed})

            if path == "/api/upload":
                username = self.require_user()

                if not username:
                    return

                content_type = self.headers.get("Content-Type", "")

                if "multipart/form-data" not in content_type.lower():
                    return self.send_json(400, {
                        "error": "La solicitud debe ser multipart/form-data."
                    })

                length_header = self.headers.get("Content-Length")

                if not length_header:
                    return self.send_json(411, {
                        "error": "Falta el tamaño de la solicitud."
                    })

                try:
                    request_length = int(length_header)
                except ValueError:
                    return self.send_json(400, {
                        "error": "Tamaño de solicitud no válido."
                    })

                # El formulario tiene algunos bytes adicionales
                # además del archivo. Este límite solo evita cuerpos
                # absurdamente grandes; el archivo se valida abajo.
                if request_length <= 0 or request_length > MAX_VIDEO_SIZE + 1024 * 1024:
                    return self.send_json(413, {
                        "error": "Solicitud demasiado grande."
                    })

                body = self.rfile.read(request_length)

                mime = (
                    f"Content-Type: {content_type}\r\n"
                    "MIME-Version: 1.0\r\n\r\n"
                ).encode("utf-8") + body

                message = BytesParser(policy=default).parsebytes(mime)

                fields = {}
                file_bytes = None
                file_name = ""

                for part in message.iter_parts():
                    field_name = part.get_param(
                        "name", header="content-disposition"
                    )

                    payload = part.get_payload(decode=True) or b""

                    if field_name == "video":
                        file_bytes = payload
                        file_name = part.get_filename() or ""
                    elif field_name:
                        fields[field_name] = payload.decode(
                            "utf-8", "replace"
                        )

                # Comprobar el tamaño REAL del archivo, en bytes.
                # No usar el tamaño de todo el formulario multipart.
                if file_bytes is None:
                    return self.send_json(400, {
                        "error": "No se recibió ningún archivo de video."
                    })

                actual_size = len(file_bytes)

                # REGLA EXACTA:
                # 52,428,800 bytes: permitido.
                # 52,428,801 bytes: rechazado.
                if actual_size > MAX_VIDEO_SIZE:
                    return self.send_json(413, {
                        "error": (
                            "Video rechazado. El máximo permitido es "
                            "50 MiB (52,428,800 bytes). "
                            f"Tu archivo tiene {actual_size:,} bytes."
                        )
                    })

                title = fields.get("title", "").strip()
                description = fields.get("description", "").strip()
                channel_id = fields.get("channelId", "").strip()

                channel = get_channel(channel_id)

                if not channel:
                    return self.send_json(400, {
                        "error": "Selecciona un canal válido."
                    })

                if channel["owner_username"] != username:
                    return self.send_json(403, {
                        "error": "Solo puedes subir videos a tu propio canal."
                    })

                if not title or len(title) > 120:
                    return self.send_json(400, {
                        "error": "El título es obligatorio y debe tener máximo 120 caracteres."
                    })

                if len(description) > 5000:
                    return self.send_json(400, {
                        "error": "La descripción es demasiado larga."
                    })

                if not file_name.lower().endswith(".mp4"):
                    return self.send_json(400, {
                        "error": "Solo se permiten archivos MP4."
                    })

                video_id = str(uuid.uuid4())
                object_path = f"{channel_id}/{video_id}.mp4"

                # Solo se intenta subir a Storage después de validar
                # el tamaño real del archivo.
                supabase.storage.from_(BUCKET).upload(
                    object_path,
                    file_bytes,
                    {
                        "content-type": "video/mp4",
                        "cache-control": "3600",
                        "upsert": "false"
                    }
                )

                url = public_video_url(object_path)

                inserted = (
                    db("videos")
                    .insert({
                        "id": video_id,
                        "channel_id": channel_id,
                        "title": title,
                        "description": description,
                        "video_url": url
                    })
                    .execute()
                    .data
                )

                return self.send_json(201, {
                    "ok": True,
                    "sizeBytes": actual_size,
                    "video": video_data(inserted[0], username)
                })

            return self.send_json(404, {"error": "Ruta no encontrada."})

        except Exception:
            logging.exception("Error en POST %s", path)

            return self.send_json(500, {
                "error": (
                    "Error interno. Revisa los registros de Render, "
                    "las tablas de Supabase y el bucket de videos."
                )
            })


if __name__ == "__main__":
    logging.info("Iniciando PouPlay con Supabase...")
    logging.info("Límite por video: %s bytes", MAX_VIDEO_SIZE)

    server = ThreadingHTTPServer((HOST, PORT), Handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
