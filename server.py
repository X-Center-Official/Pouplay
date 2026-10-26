import json
import re
import hmac
import hashlib
import secrets
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote
from email.parser import BytesParser
from email.policy import default

BASE = Path(__file__).resolve().parent
CHANNELS = BASE / "Channels"
USERS_FILE = BASE / "users.json"
HOST = "0.0.0.0"
PORT = 8000
MAX_VIDEO_SIZE = 500 * 1024 * 1024

CHANNELS.mkdir(exist_ok=True)

HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PouPlay</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#101010;color:#f5f5f5;font:15px Arial}
header{position:sticky;top:0;background:#191919;padding:14px;display:flex;gap:10px;align-items:center;z-index:5;border-bottom:1px solid #333}
.logo{font-size:23px;font-weight:bold;color:#ff4545;cursor:pointer;white-space:nowrap}
main{max-width:1050px;margin:auto;padding:18px}
button,input,textarea{font:inherit;border-radius:9px;padding:11px;border:1px solid #444}
button{background:#292929;color:white;cursor:pointer}
button:hover{background:#414141}
button:disabled{opacity:.5;cursor:not-allowed}
input,textarea{background:#171717;color:white;width:100%;margin:5px 0 12px}
textarea{resize:vertical;min-height:100px}
.panel{background:#1c1c1c;border-radius:14px;padding:18px;margin:15px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:15px}
.video{background:#202020;border-radius:12px;overflow:hidden;cursor:pointer}
.video video{width:100%;aspect-ratio:16/9;object-fit:cover;background:#000}
.info{padding:12px}
.muted{color:#aaa;font-size:13px}
.hidden{display:none!important}
.error{color:#ff7979}
.success{color:#8be6a2}
.spacer{flex:1}
video.player{width:100%;max-height:65vh;background:#000;border-radius:12px}
#fileName{overflow-wrap:anywhere}
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}
.subscribe-button{background:#ff3030;color:white;border-color:#ff3030}
.subscribe-button.subscribed{background:#333;color:white;border-color:#555}
#who{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}
.primary{background:#ff3030;border-color:#ff3030}
.row{display:flex;gap:8px;flex-wrap:wrap}
@media(max-width:520px){
 header{padding:9px;gap:5px}
 .logo{font-size:18px}
 header button{padding:9px 8px;font-size:13px}
 #who{max-width:95px;font-size:12px}
 main{padding:10px}
 .panel{padding:13px}
}
</style>
</head>
<body>
<header>
  <div class="logo" onclick="home()">▶ PouPlay</div>
  <div class="spacer"></div>
  <span id="who" class="muted">Sin iniciar sesión</span>
  <button id="createButton" class="hidden primary" onclick="openCreate()">Crear</button>
  <button id="authButton" onclick="showAuth()">Entrar</button>
  <button id="logoutButton" class="hidden" onclick="logout()">Salir</button>
</header>

<main>
<section id="auth" class="panel hidden">
  <h2 id="authTitle">Crear cuenta</h2>
  <label>Nombre de usuario</label>
  <input id="username" maxlength="24" autocomplete="username" placeholder="Tu usuario">
  <label>Contraseña</label>
  <input id="password" type="password" maxlength="128" autocomplete="current-password" placeholder="Tu contraseña">
  <div class="row">
    <button class="primary" onclick="submitAuth()">Continuar</button>
    <button id="modeButton" onclick="toggleAuthMode()">Ya tengo cuenta</button>
    <button onclick="cancelScreen()">Cancelar</button>
  </div>
  <p id="authMessage" class="muted"></p>
</section>

<section id="createPanel" class="panel hidden">
  <h2>Crear mi canal</h2>
  <p class="muted">Antes de subir videos, necesitas crear tu canal.</p>
  <label>Nombre de tu canal</label>
  <input id="channelName" maxlength="40" placeholder="Nombre de tu canal">
  <div class="row">
    <button class="primary" onclick="createChannel()">Crear canal</button>
    <button onclick="cancelScreen()">Cancelar</button>
  </div>
  <p id="channelMessage" class="muted"></p>
</section>

<section id="uploadPanel" class="panel hidden">
  <h2>Subir video</h2>
  <p class="muted">Elige un archivo MP4, ponle título y escribe su descripción.</p>
  <label>Archivo de video (.mp4)</label>
  <input id="videoFile" type="file" accept=".mp4,video/mp4">
  <p id="fileName" class="muted">No has seleccionado ningún video.</p>
  <label>Título</label>
  <input id="uploadTitle" maxlength="120" placeholder="Título del video">
  <label>Descripción</label>
  <textarea id="uploadDescription" maxlength="5000" placeholder="¿De qué trata tu video?"></textarea>
  <div class="row">
    <button id="uploadButton" class="primary" onclick="uploadVideo()">Subir video</button>
    <button id="cancelUploadButton" onclick="cancelScreen()">Cancelar</button>
  </div>
  <p id="uploadMessage" class="muted"></p>
  <progress id="uploadProgress" class="hidden" max="100" value="0" style="width:100%"></progress>
</section>

<section id="homePage">
  <h2>Videos</h2>
  <div id="videos" class="grid"></div>
  <h2>Canales</h2>
  <div id="channels" class="grid"></div>
  <p id="empty" class="muted hidden">Todavía no hay videos ni canales.</p>
</section>

<section id="channelPage" class="hidden">
  <button onclick="home()">← Volver</button>
  <div class="panel">
    <h2 id="channelTitle"></h2>
    <p id="channelMeta" class="muted"></p>
    <div class="actions">
      <button id="channelSubscribeButton" class="subscribe-button hidden"
        onclick="toggleSubscribeFromChannel()">Suscribirse</button>
      <span id="channelSubscribeMessage" class="muted"></span>
    </div>
  </div>
  <div id="channelVideos" class="grid"></div>
</section>

<section id="videoPage" class="hidden">
  <button onclick="home()">← Volver</button>
  <div class="panel">
    <video id="player" class="player" controls playsinline></video>
    <h2 id="videoTitle"></h2>
    <p id="videoCreator" class="muted"></p>
    <p id="videoDescription"></p>
    <div class="actions">
      <button id="likeButton" onclick="toggleLike()">♡ Me gusta</button>
      <span id="likeCount" class="muted"></span>
    </div>
    <div class="actions">
      <button id="videoSubscribeButton" class="subscribe-button"
        onclick="toggleSubscribeFromVideo()">Suscribirse</button>
      <span id="videoSubscribeCount" class="muted"></span>
    </div>
    <p id="videoActionMessage" class="muted"></p>
  </div>
</section>
</main>

<script>
let user=null;
let registerMode=true;
let currentVideo=null;
let currentChannel=null;
let uploadBusy=false;
let screenMode="home";

const $=id=>document.getElementById(id);

async function api(path,options={}){
  const headers={...(options.headers||{})};
  if(!(options.body instanceof FormData)){
    headers["Content-Type"]="application/json";
  }
  const r=await fetch(path,{
    credentials:"same-origin",
    ...options,
    headers
  });
  const d=await r.json().catch(()=>({error:"Respuesta no válida"}));
  if(!r.ok)throw new Error(d.error||"Ocurrió un error");
  return d;
}

function hideAllContent(){
  ["auth","createPanel","uploadPanel","homePage","channelPage","videoPage"]
    .forEach(id=>$(id).classList.add("hidden"));
}

function showScreen(mode){
  hideAllContent();
  screenMode=mode;

  if(mode==="auth")$("auth").classList.remove("hidden");
  else if(mode==="create")$("createPanel").classList.remove("hidden");
  else if(mode==="upload")$("uploadPanel").classList.remove("hidden");
  else if(mode==="channel")$("channelPage").classList.remove("hidden");
  else if(mode==="video")$("videoPage").classList.remove("hidden");
  else $("homePage").classList.remove("hidden");
}

function showAuth(){
  registerMode=true;
  $("authTitle").textContent="Crear cuenta";
  $("modeButton").textContent="Ya tengo cuenta";
  $("authMessage").textContent="";
  $("password").value="";
  showScreen("auth");
}

function hideAuth(){
  showScreen("home");
}

function cancelScreen(){
  if(uploadBusy)return;
  $("authMessage").textContent="";
  $("channelMessage").textContent="";
  $("uploadMessage").textContent="";
  $("password").value="";
  showHome();
  history.pushState({page:"home"},"","/");
}

function toggleAuthMode(){
  registerMode=!registerMode;
  $("authTitle").textContent=registerMode?"Crear cuenta":"Iniciar sesión";
  $("modeButton").textContent=registerMode?"Ya tengo cuenta":"Crear cuenta nueva";
  $("authMessage").textContent="";
  $("password").value="";
}

async function submitAuth(){
  const msg=$("authMessage");
  msg.className="muted";
  try{
    const d=await api(registerMode?"/api/register":"/api/login",{
      method:"POST",
      body:JSON.stringify({
        username:$("username").value.trim(),
        password:$("password").value
      })
    });
    user=d.user;
    $("password").value="";
    updateUser();
    history.pushState({page:"home"},"","/");
    await showHome();
  }catch(e){
    msg.textContent=e.message;
    msg.className="error";
  }
}

async function logout(){
  try{
    await api("/api/logout",{method:"POST",body:"{}"});
  }catch(e){}

  user=null;
  currentVideo=null;
  currentChannel=null;

  const player=$("player");
  player.pause();
  player.removeAttribute("src");
  player.load();

  updateUser();
  showScreen("home");
  history.pushState({page:"home"},"","/");
  await showHome();
}

function updateUser(){
  $("who").textContent=user?"@"+user.username:"Sin iniciar sesión";
  $("authButton").classList.toggle("hidden",!!user);
  $("logoutButton").classList.toggle("hidden",!user);
  $("createButton").classList.toggle("hidden",!user);
}

function openCreate(){
  if(!user){
    showAuth();
    return;
  }
  $("channelMessage").textContent="";
  $("uploadMessage").textContent="";

  if(!user.channel){
    showScreen("create");
  }else{
    showScreen("upload");
  }
}

async function loadMe(){
  try{
    user=(await api("/api/me")).user;
  }catch(e){
    user=null;
  }
  updateUser();
}

async function createChannel(){
  const msg=$("channelMessage");
  try{
    const d=await api("/api/create-channel",{
      method:"POST",
      body:JSON.stringify({name:$("channelName").value.trim()})
    });
    user=d.user;
    updateUser();
    msg.textContent="¡Tu canal está listo!";
    msg.className="success";
    await showHome();
    history.pushState({page:"home"},"","/");
  }catch(e){
    msg.textContent=e.message;
    msg.className="error";
  }
}

$("videoFile").addEventListener("change",()=>{
  const f=$("videoFile").files[0];
  $("fileName").textContent=f?
    f.name+" ("+(f.size/1024/1024).toFixed(1)+" MB)":
    "No has seleccionado ningún video.";
});

async function uploadVideo(){
  if(uploadBusy)return;
  if(!user){showAuth();return}
  if(!user.channel){
    showScreen("create");
    return;
  }

  const file=$("videoFile").files[0];
  const title=$("uploadTitle").value.trim();
  const description=$("uploadDescription").value.trim();
  const msg=$("uploadMessage");
  const progress=$("uploadProgress");
  const btn=$("uploadButton");

  if(!file){msg.textContent="Selecciona un video MP4.";msg.className="error";return}
  if(!file.name.toLowerCase().endsWith(".mp4")){
    msg.textContent="El archivo debe terminar en .mp4.";
    msg.className="error";return;
  }
  if(file.size===0){msg.textContent="El archivo está vacío.";msg.className="error";return}
  if(file.size>500*1024*1024){
    msg.textContent="El límite de tamaño es 500 MB.";
    msg.className="error";return;
  }
  if(!title){msg.textContent="Escribe el título.";msg.className="error";return}

  uploadBusy=true;
  btn.disabled=true;
  $("cancelUploadButton").disabled=true;
  progress.classList.remove("hidden");
  progress.value=0;
  msg.textContent="Subiendo video...";
  msg.className="muted";

  const form=new FormData();
  form.append("video",file);
  form.append("title",title);
  form.append("description",description);

  try{
    const result=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();
      xhr.open("POST","/api/upload");
      xhr.withCredentials=true;
      xhr.upload.onprogress=e=>{
        if(e.lengthComputable)progress.value=Math.round(e.loaded/e.total*100);
      };
      xhr.onload=()=>{
        let d={};
        try{d=JSON.parse(xhr.responseText)}catch(e){}
        if(xhr.status>=200&&xhr.status<300)resolve(d);
        else reject(new Error(d.error||"No se pudo subir el video."));
      };
      xhr.onerror=()=>reject(new Error("Error de conexión durante la subida."));
      xhr.send(form);
    });

    $("videoFile").value="";
    $("uploadTitle").value="";
    $("uploadDescription").value="";
    $("fileName").textContent="No has seleccionado ningún video.";
    msg.textContent="¡Video subido correctamente!";
    msg.className="success";

    await showHome();
    await openVideo(result.video.channelFolder,result.video.folder);
  }catch(e){
    msg.textContent=e.message;
    msg.className="error";
  }finally{
    uploadBusy=false;
    btn.disabled=false;
    $("cancelUploadButton").disabled=false;
    setTimeout(()=>progress.classList.add("hidden"),1200);
  }
}

function home(){
  if(uploadBusy)return;
  history.pushState({page:"home"},"","/");
  showHome();
}

async function showHome(){
  showScreen("home");
  const vids=$("videos");
  const chans=$("channels");
  vids.innerHTML="";
  chans.innerHTML="";
  try{
    const [vd,cd]=await Promise.all([
      api("/api/videos"),
      api("/api/channels")
    ]);

    for(const v of vd.videos){
      const el=document.createElement("div");
      el.className="video";
      const thumb=document.createElement("video");
      thumb.src=v.url;
      thumb.muted=true;
      thumb.preload="metadata";
      const info=document.createElement("div");
      info.className="info";
      const title=document.createElement("strong");
      title.textContent=v.title;
      const creator=document.createElement("p");
      creator.className="muted";
      creator.textContent=v.channel+" · "+v.likes+" Me gusta";
      info.append(title,creator);
      el.append(thumb,info);
      el.onclick=()=>openVideo(v.channelFolder,v.folder);
      vids.append(el);
    }

    for(const c of cd.channels){
      const el=document.createElement("div");
      el.className="panel";
      const title=document.createElement("strong");
      title.textContent=c.name;
      const meta=document.createElement("p");
      meta.className="muted";
      meta.textContent=c.subscribers+" suscriptores";
      const btn=document.createElement("button");
      btn.textContent="Ver canal";
      btn.onclick=()=>openChannel(c.folder);
      el.append(title,meta,btn);
      chans.append(el);
    }

    $("empty").classList.toggle("hidden",vd.videos.length+cd.channels.length>0);
  }catch(e){
    vids.textContent="Error: "+e.message;
  }
  updateUser();
}

function setSubscribeButton(button,subscribed){
  button.textContent=subscribed?"Suscrito":"Suscribirse";
  button.classList.toggle("subscribed",subscribed);
}

async function openChannel(folder,push=true){
  if(push){
    history.pushState({page:"channel",folder},"","/"+encodeURIComponent(folder));
  }
  showScreen("channel");
  currentChannel=folder;

  const subButton=$("channelSubscribeButton");
  const subMessage=$("channelSubscribeMessage");
  subMessage.textContent="";
  subButton.classList.add("hidden");

  try{
    const d=await api("/api/channel?name="+encodeURIComponent(folder));
    currentChannel=d.channel.folder;
    $("channelTitle").textContent=d.channel.name;
    $("channelMeta").textContent=d.channel.subscribers+" suscriptores";

    if(user && d.channel.owner!==user.username){
      subButton.classList.remove("hidden");
      setSubscribeButton(subButton,d.channel.subscribed);
    }

    const box=$("channelVideos");
    box.innerHTML="";
    for(const v of d.videos){
      const el=document.createElement("div");
      el.className="video";
      const thumb=document.createElement("video");
      thumb.src=v.url;
      thumb.muted=true;
      thumb.preload="metadata";
      const info=document.createElement("div");
      info.className="info";
      const title=document.createElement("strong");
      title.textContent=v.title;
      const desc=document.createElement("p");
      desc.className="muted";
      desc.textContent=v.description;
      info.append(title,desc);
      el.append(thumb,info);
      el.onclick=()=>openVideo(v.channelFolder,v.folder);
      box.append(el);
    }
  }catch(e){
    $("channelTitle").textContent=e.message;
  }
}

async function toggleSubscribe(channelFolder,button,countElement,messageElement){
  if(!user){showAuth();return}
  if(!channelFolder)return;

  button.disabled=true;
  if(messageElement)messageElement.textContent="";
  try{
    const d=await api("/api/toggle-subscribe",{
      method:"POST",
      body:JSON.stringify({channel:channelFolder})
    });
    setSubscribeButton(button,d.subscribed);
    if(countElement)countElement.textContent=d.subscribers+" suscriptores";

    if(currentChannel===channelFolder){
      $("channelMeta").textContent=d.subscribers+" suscriptores";
    }

    if(currentVideo && currentVideo.channelFolder===channelFolder){
      currentVideo.subscribed=d.subscribed;
      currentVideo.subscribers=d.subscribers;
      $("videoSubscribeCount").textContent=d.subscribers+" suscriptores";
    }

    if(messageElement){
      messageElement.textContent=d.subscribed?
        "¡Te has suscrito al canal!":"Has cancelado la suscripción.";
      messageElement.className="muted success";
    }
  }catch(e){
    if(messageElement){
      messageElement.textContent=e.message;
      messageElement.className="error";
    }else{
      alert(e.message);
    }
  }finally{
    button.disabled=false;
  }
}

function toggleSubscribeFromChannel(){
  toggleSubscribe(currentChannel,$("channelSubscribeButton"),null,$("channelSubscribeMessage"));
}

function toggleSubscribeFromVideo(){
  if(!currentVideo)return;
  toggleSubscribe(
    currentVideo.channelFolder,
    $("videoSubscribeButton"),
    $("videoSubscribeCount"),
    $("videoActionMessage")
  );
}

async function openVideo(channel,folder,push=true){
  if(push){
    history.pushState(
      {page:"video",channel,folder},
      "",
      "/"+encodeURIComponent(channel)+"/"+encodeURIComponent(folder)
    );
  }
  showScreen("video");
  $("videoActionMessage").textContent="";
  currentVideo=null;

  try{
    const d=await api(
      "/api/video?channel="+encodeURIComponent(channel)+
      "&video="+encodeURIComponent(folder)
    );
    currentVideo=d.video;
    $("player").src=d.video.url;
    $("videoTitle").textContent=d.video.title;
    $("videoCreator").textContent=d.video.channel;
    $("videoDescription").textContent=d.video.description||"";
    $("likeCount").textContent=d.video.likes+" Me gusta";
    $("likeButton").textContent=d.video.liked?"♥ Te gusta":"♡ Me gusta";

    const subButton=$("videoSubscribeButton");
    $("videoSubscribeCount").textContent=d.video.subscribers+" suscriptores";

    if(user && d.video.channelOwner===user.username){
      subButton.classList.add("hidden");
    }else{
      subButton.classList.remove("hidden");
      setSubscribeButton(subButton,d.video.subscribed);
    }
  }catch(e){
    $("videoTitle").textContent=e.message;
  }
}

async function toggleLike(){
  if(!user){showAuth();return}
  if(!currentVideo)return;
  const btn=$("likeButton");
  btn.disabled=true;
  try{
    const d=await api("/api/toggle-like",{
      method:"POST",
      body:JSON.stringify({
        channel:currentVideo.channelFolder,
        video:currentVideo.folder
      })
    });
    currentVideo.likes=d.likes;
    currentVideo.liked=d.liked;
    $("likeCount").textContent=d.likes+" Me gusta";
    btn.textContent=d.liked?"♥ Te gusta":"♡ Me gusta";
  }catch(e){
    alert(e.message);
  }finally{
    btn.disabled=false;
  }
}

window.addEventListener("popstate",()=>{
  const p=decodeURIComponent(location.pathname).split("/").filter(Boolean);
  if(!p.length)showHome();
  else if(p.length===1)openChannel(p[0],false);
  else openVideo(p[0],p.slice(1).join("/"),false);
});

(async()=>{
  await loadMe();
  const p=decodeURIComponent(location.pathname).split("/").filter(Boolean);
  if(!p.length)await showHome();
  else if(p.length===1)await openChannel(p[0],false);
  else await openVideo(p[0],p.slice(1).join("/"),false);
})();
</script>
</body>
</html>
"""

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}

def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def load_users():
    data = read_json(USERS_FILE, {})
    return data if isinstance(data, dict) else {}

def safe_name(name):
    name = str(name or "").strip()
    if not name or len(name) > 40 or name in (".", ".."):
        return None
    if not re.fullmatch(r"[\w .-]+", name, flags=re.UNICODE):
        return None
    return name

def folder_for_channel(name):
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    p = (CHANNELS / name).resolve()
    return p if p.parent == CHANNELS.resolve() else None

def find_video(channel_name, video_name):
    channel = folder_for_channel(channel_name)
    if channel is None or not channel.is_dir():
        return None, None
    if not video_name or "/" in video_name or "\\" in video_name or video_name in (".", ".."):
        return None, None
    folder = (channel / video_name).resolve()
    if folder.parent != channel.resolve() or not folder.is_dir():
        return None, None
    if not (folder / "info.json").is_file() or not (folder / "video.mp4").is_file():
        return None, None
    return folder, read_json(folder / "info.json", {})

def subscriber_list(info):
    users = info.get("_subscribed_by", [])
    if not isinstance(users, list):
        users = []
    return list(dict.fromkeys(str(u) for u in users if u))

def video_data(channel_name, video_name):
    folder, info = find_video(channel_name, video_name)
    if folder is None:
        return None

    channel_folder = folder_for_channel(channel_name)
    channel_info = read_json(channel_folder / "info.json", {}) if channel_folder else {}

    return {
        "title": str(info.get("title", folder.name)),
        "description": str(info.get("description", "")),
        "likes": max(0, int(info.get("likes", 0) or 0)),
        "channel": str(channel_info.get("name", channel_name)),
        "channelFolder": channel_name,
        "channelOwner": channel_info.get("owner"),
        "subscribers": len(subscriber_list(channel_info)),
        "folder": folder.name,
        "url": "/media/" + quote(channel_name, safe="") + "/" +
               quote(folder.name, safe="") + "/video.mp4"
    }

def list_channels():
    result = []
    for p in sorted(CHANNELS.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir():
            info = read_json(p / "info.json", {})
            result.append({
                "name": str(info.get("name", p.name)),
                "folder": p.name,
                "subscribers": len(subscriber_list(info))
            })
    return result

def list_videos(channel_filter=None):
    result = []
    for c in sorted(CHANNELS.iterdir(), key=lambda x: x.name.lower()):
        if not c.is_dir() or (channel_filter and c.name != channel_filter):
            continue
        for v in sorted(c.iterdir(), key=lambda x: x.name.lower()):
            if v.is_dir():
                data = video_data(c.name, v.name)
                if data:
                    result.append(data)
    return result

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 250000
    )
    return salt, hashed.hex()

def public_user(username, data):
    return {"username": username, "channel": data.get("channel")}

SESSIONS = {}

class Handler(BaseHTTPRequestHandler):
    server_version = "PouPlay/2.3"

    def log_message(self, fmt, *args):
        print("[PouPlay]", fmt % args)

    def send_bytes(self, code, data, content_type="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, code, obj, extra=None):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), extra=extra)

    def body_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 100000:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def current_user(self):
        token = None
        for part in self.headers.get("Cookie", "").split(";"):
            if part.strip().startswith("pouplay_session="):
                token = part.strip().split("=", 1)[1]
        username = SESSIONS.get(token)
        users = load_users()
        if username and username in users:
            return username, users[username]
        return None, None

    def require_user(self):
        username, account = self.current_user()
        if not username:
            self.json_response(401, {"error": "Inicia sesión primero."})
            return None, None
        return username, account

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path == "/api/me":
            u, a = self.current_user()
            return self.json_response(200, {"user": public_user(u, a) if u else None})

        if path == "/api/channels":
            return self.json_response(200, {"channels": list_channels()})

        if path == "/api/videos":
            return self.json_response(200, {"videos": list_videos()})

        if path == "/api/channel":
            name = query.get("name", [""])[0]
            folder = folder_for_channel(name)
            if folder is None or not folder.is_dir():
                return self.json_response(404, {"error": "Canal no encontrado."})

            info = read_json(folder / "info.json", {})
            username, _ = self.current_user()
            subs = subscriber_list(info)

            return self.json_response(200, {
                "channel": {
                    "name": str(info.get("name", folder.name)),
                    "folder": folder.name,
                    "owner": info.get("owner"),
                    "subscribers": len(subs),
                    "subscribed": bool(username and username in subs)
                },
                "videos": list_videos(folder.name)
            })

        if path == "/api/video":
            channel = query.get("channel", [""])[0]
            video = query.get("video", [""])[0]
            data = video_data(channel, video)
            if not data:
                return self.json_response(404, {"error": "Video no encontrado."})

            username, _ = self.current_user()
            _, info = find_video(channel, video)
            liked_by = info.get("_liked_by", [])
            data["liked"] = bool(username and isinstance(liked_by, list) and username in liked_by)

            channel_folder = folder_for_channel(channel)
            channel_info = read_json(channel_folder / "info.json", {}) if channel_folder else {}
            subs = subscriber_list(channel_info)
            data["subscribed"] = bool(username and username in subs)

            return self.json_response(200, {"video": data})

        if path.startswith("/media/"):
            parts = path[len("/media/"):].split("/")
            if len(parts) != 3 or parts[2] != "video.mp4":
                return self.json_response(404, {"error": "Archivo no encontrado."})

            folder, _ = find_video(parts[0], parts[1])
            if folder is None:
                return self.json_response(404, {"error": "Video no encontrado."})

            media = folder / "video.mp4"
            size = media.stat().st_size
            start, end, status = 0, size - 1, 200
            rh = self.headers.get("Range")

            if rh:
                m = re.match(r"bytes=(\d*)-(\d*)$", rh.strip())
                if m:
                    if m.group(1):
                        start = int(m.group(1))
                    if m.group(2):
                        end = int(m.group(2))
                    if not m.group(1) and m.group(2):
                        start = max(0, size - int(m.group(2)))
                    end = min(end, size - 1)
                    if start >= size or start > end:
                        return self.send_bytes(416, b"", extra={"Content-Range": f"bytes */{size}"})
                    status = 206

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            with open(media, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return

        self.send_bytes(200, HTML.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/upload":
            username, account = self.require_user()
            if not username:
                return

            channel_name = account.get("channel")
            channel = folder_for_channel(channel_name)
            if not channel_name or channel is None or not channel.is_dir():
                return self.json_response(400, {"error": "Primero crea tu canal."})

            info = read_json(channel / "info.json", {})
            if info.get("owner") != username:
                return self.json_response(403, {"error": "Este canal no pertenece a tu cuenta."})

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0

            if length <= 0:
                return self.json_response(400, {"error": "No se recibió ningún archivo."})
            if length > MAX_VIDEO_SIZE + 1024 * 1024:
                return self.json_response(413, {"error": "El límite de subida es 500 MB."})

            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type.lower():
                return self.json_response(400, {"error": "Solicitud de subida no válida."})

            raw = self.rfile.read(length)
            try:
                message = BytesParser(policy=default).parsebytes(
                    b"Content-Type: " + content_type.encode("ascii") +
                    b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
                )
                fields = {}
                uploaded = None
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    payload = part.get_payload(decode=True) or b""
                    if name == "video":
                        uploaded = (part.get_filename() or "", payload)
                    elif name in ("title", "description"):
                        fields[name] = payload.decode("utf-8", errors="replace")
            except Exception:
                return self.json_response(400, {"error": "No se pudo leer el formulario."})

            if not uploaded:
                return self.json_response(400, {"error": "Selecciona un archivo MP4."})

            filename, video_bytes = uploaded
            title = fields.get("title", "").strip()
            description = fields.get("description", "").strip()

            if not filename.lower().endswith(".mp4"):
                return self.json_response(400, {"error": "Solo se aceptan archivos .mp4."})
            if not video_bytes:
                return self.json_response(400, {"error": "El video está vacío."})
            if len(video_bytes) > MAX_VIDEO_SIZE:
                return self.json_response(413, {"error": "El límite de subida es 500 MB."})
            if not title or len(title) > 120:
                return self.json_response(400, {"error": "El título es obligatorio y debe tener máximo 120 caracteres."})
            if len(description) > 5000:
                return self.json_response(400, {"error": "La descripción supera los 5000 caracteres."})

            slug = re.sub(r"[^\w-]+", "-", title, flags=re.UNICODE).strip("-")[:45] or "video"
            video_folder = channel / (slug + "-" + secrets.token_hex(4))
            video_folder.mkdir()

            try:
                (video_folder / "video.mp4").write_bytes(video_bytes)
                write_json(video_folder / "info.json", {
                    "title": title,
                    "description": description,
                    "likes": 0,
                    "_liked_by": [],
                    "owner": username
                })
            except Exception:
                import shutil
                shutil.rmtree(video_folder, ignore_errors=True)
                return self.json_response(500, {"error": "No se pudo guardar el video."})

            return self.json_response(201, {"ok": True, "video": video_data(channel_name, video_folder.name)})

        data = self.body_json()

        if path == "/api/register":
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))

            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", username):
                return self.json_response(400, {"error": "El usuario debe tener entre 3 y 24 caracteres."})
            if len(password) < 6 or len(password) > 128:
                return self.json_response(400, {"error": "La contraseña debe tener entre 6 y 128 caracteres."})

            users = load_users()
            if any(u.lower() == username.lower() for u in users):
                return self.json_response(409, {"error": "Ese usuario ya existe."})

            salt, hashed = hash_password(password)
            users[username] = {"salt": salt, "password_hash": hashed, "channel": None}
            write_json(USERS_FILE, users)

            token = secrets.token_urlsafe(32)
            SESSIONS[token] = username
            return self.json_response(
                201, {"user": public_user(username, users[username])},
                {"Set-Cookie": f"pouplay_session={token}; HttpOnly; SameSite=Lax; Path=/"}
            )

        if path == "/api/login":
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            users = load_users()
            real_name = next((u for u in users if u.lower() == username.lower()), None)

            if not real_name:
                return self.json_response(401, {"error": "Usuario o contraseña incorrectos."})

            account = users[real_name]
            _, hashed = hash_password(password, account["salt"])
            if not hmac.compare_digest(hashed, account["password_hash"]):
                return self.json_response(401, {"error": "Usuario o contraseña incorrectos."})

            token = secrets.token_urlsafe(32)
            SESSIONS[token] = real_name
            return self.json_response(
                200, {"user": public_user(real_name, account)},
                {"Set-Cookie": f"pouplay_session={token}; HttpOnly; SameSite=Lax; Path=/"}
            )

        if path == "/api/logout":
            token = None
            for part in self.headers.get("Cookie", "").split(";"):
                if part.strip().startswith("pouplay_session="):
                    token = part.strip().split("=", 1)[1]
            if token:
                SESSIONS.pop(token, None)
            return self.json_response(
                200, {"ok": True},
                {"Set-Cookie": "pouplay_session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/"}
            )

        if path == "/api/create-channel":
            username, account = self.require_user()
            if not username:
                return
            if account.get("channel"):
                return self.json_response(409, {"error": "Tu cuenta ya tiene un canal."})

            name = safe_name(data.get("name"))
            if not name:
                return self.json_response(400, {"error": "Nombre de canal no válido."})

            folder = folder_for_channel(name)
            if folder is None:
                return self.json_response(400, {"error": "Nombre no válido."})
            if folder.exists():
                return self.json_response(409, {"error": "Ya existe un canal con ese nombre."})

            folder.mkdir()
            write_json(folder / "info.json", {
                "name": name,
                "subscribers": 0,
                "_subscribed_by": [],
                "owner": username
            })

            users = load_users()
            users[username]["channel"] = folder.name
            write_json(USERS_FILE, users)
            return self.json_response(201, {"user": public_user(username, users[username])})

        if path == "/api/toggle-like":
            username, _ = self.require_user()
            if not username:
                return

            folder, info = find_video(str(data.get("channel", "")), str(data.get("video", "")))
            if folder is None:
                return self.json_response(404, {"error": "Video no encontrado."})

            liked_by = info.get("_liked_by", [])
            if not isinstance(liked_by, list):
                liked_by = []
            liked_by = list(dict.fromkeys(liked_by))

            if username in liked_by:
                liked_by.remove(username)
                liked = False
            else:
                liked_by.append(username)
                liked = True

            info["likes"] = len(liked_by)
            info["_liked_by"] = liked_by
            write_json(folder / "info.json", info)
            return self.json_response(200, {"likes": len(liked_by), "liked": liked})

        if path == "/api/toggle-subscribe":
            username, _ = self.require_user()
            if not username:
                return

            channel_name = str(data.get("channel", ""))
            channel = folder_for_channel(channel_name)
            if channel is None or not channel.is_dir():
                return self.json_response(404, {"error": "Canal no encontrado."})

            info_path = channel / "info.json"
            info = read_json(info_path, {})
            if info.get("owner") == username:
                return self.json_response(400, {"error": "No puedes suscribirte a tu propio canal."})

            subscribed_by = subscriber_list(info)
            if username in subscribed_by:
                subscribed_by.remove(username)
                subscribed = False
            else:
                subscribed_by.append(username)
                subscribed = True

            info["_subscribed_by"] = subscribed_by
            info["subscribers"] = len(subscribed_by)
            write_json(info_path, info)
            return self.json_response(200, {
                "ok": True,
                "subscribed": subscribed,
                "subscribers": len(subscribed_by)
            })

        return self.json_response(404, {"error": "Ruta no encontrada."})

if __name__ == "__main__":
    print("=" * 38)
    print("PouPlay - Servidor local")
    print("=" * 38)
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except OSError as e:
        print("No se pudo iniciar el servidor:", e)
