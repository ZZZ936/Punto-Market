#!/usr/bin/env python3
"""Punto Market: small, dependency-free event catalog and admin server."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("PUNTO_DATA_DIR", ROOT / "data")).resolve()
UPLOADS = DATA / "uploads"
DB = DATA / "punto_market.sqlite3"
MAX_BODY = 8 * 1024 * 1024
SESSION_DAYS = 7

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, salt TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Otros', price REAL NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '', seller TEXT NOT NULL DEFAULT '', spot_id INTEGER, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, brand TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('sale','purchase')), description TEXT NOT NULL, amount REAL NOT NULL, counterparty TEXT NOT NULL DEFAULT '', date TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS spots(id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', x REAL NOT NULL, y REAL NOT NULL, status TEXT NOT NULL DEFAULT 'available', vendor TEXT NOT NULL DEFAULT '', public_note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    seed(con)
    return con

def seed(con):
    if not con.execute("SELECT 1 FROM settings WHERE key='event_name'").fetchone():
        for key, value in {
            "event_name": "Punto Market", "event_tagline": "Lo local se encuentra aquí.",
            "event_date": "Próximamente", "event_location": "San Salvador, El Salvador",
            "event_hours": "11:00 a. m. – 7:00 p. m.", "event_description": "Un punto de encuentro para descubrir marcas locales, diseño y buena comida.",
        }.items(): con.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, value))
    if con.execute("SELECT count(*) FROM spots").fetchone()[0] == 0:
        for i, (code, name, cat, x, y, status, vendor) in enumerate([
            ("A01","Casa Nómada","Diseño",18,26,"occupied","Casa Nómada"),("A02","Lino Studio","Moda",39,26,"occupied","Lino Studio"),
            ("A03","Tu marca aquí","Disponible",60,26,"available",""),("A04","Tu marca aquí","Disponible",81,26,"available",""),
            ("B01","Café Origen","Comida",18,53,"occupied","Café Origen"),("B02","Flor de Sal","Comida",39,53,"occupied","Flor de Sal"),
            ("B03","Tu marca aquí","Disponible",60,53,"available",""),("B04","Tu marca aquí","Disponible",81,53,"available",""),
            ("C01","Punto de entrada","Acceso",18,80,"info",""),("C02","Punto Market","Información",50,80,"info",""),("C03","Zona lounge","Descanso",81,80,"info","")]):
            con.execute("INSERT INTO spots(code,name,category,x,y,status,vendor) VALUES(?,?,?,?,?,?,?)", (code,name,cat,x,y,status,vendor))
    if con.execute("SELECT count(*) FROM listings").fetchone()[0] == 0:
        for vals in [("Bolso tejido Brisa","Accesorios",28,"Bolso hecho a mano con fibras naturales. Ligero y pensado para el día a día.","","Casa Nómada",1),
                     ("Camisa lino Tierra","Moda",42,"Lino fresco de producción local. Corte relajado, edición limitada.","","Lino Studio",2),
                     ("Cold brew de la casa","Comida",5,"Café de origen salvadoreño, macerado en frío durante 18 horas.","","Café Origen",5)]:
            con.execute("INSERT INTO listings(name,category,price,description,image,seller,spot_id,status,created_at) VALUES(?,?,?,?,?,?,?,'approved',?)", (*vals, now()))
    con.commit()

def rowdict(row): return dict(row) if row else None

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000).hex()
    return hashed, salt

def clean_text(obj, key, limit=2000, default=""):
    value = obj.get(key, default)
    if not isinstance(value, (str, int, float)): raise ValueError(f"Campo inválido: {key}")
    value = str(value).strip()
    if len(value) > limit: raise ValueError(f"El campo {key} supera {limit} caracteres")
    return value

class Handler(BaseHTTPRequestHandler):
    server_version = "PuntoMarket/1.0"
    def log_message(self, fmt, *args): super().log_message(fmt, *args)

    def send_bytes(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        self.send_response(status); self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY"); self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "public, max-age=300")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def json(self, status, obj, headers=None): self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode(), headers=headers)
    def fail(self, status, message): self.json(status, {"error": message})
    def body(self):
        try: size = int(self.headers.get("Content-Length", "0"))
        except ValueError: raise ValueError("Longitud de solicitud inválida")
        if size < 0 or size > MAX_BODY: raise ValueError("La solicitud supera el tamaño permitido")
        raw = self.rfile.read(size)
        try: obj = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError): raise ValueError("JSON inválido")
        if not isinstance(obj, dict): raise ValueError("Se esperaba un objeto JSON")
        return obj

    def session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", "")); morsel = cookie.get("pm_session")
        if not morsel: return None
        token_hash = hashlib.sha256(morsel.value.encode()).hexdigest()
        con = connect()
        row = con.execute("SELECT s.csrf,s.expires_at,a.id,a.email,a.role FROM sessions s JOIN admins a ON a.id=s.admin_id WHERE s.token_hash=?", (token_hash,)).fetchone()
        if not row or row["expires_at"] < now():
            con.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,)); con.commit(); con.close(); return None
        con.close(); return {"id":row["id"],"email":row["email"],"role":row["role"],"csrf":row["csrf"],"token_hash":token_hash}

    def require_admin(self, csrf=False):
        s = self.session()
        if not s or s["role"] not in ("admin", "manager"):
            self.fail(HTTPStatus.UNAUTHORIZED, "Inicia sesión para continuar"); return None
        if csrf and not hmac.compare_digest(str(self.headers.get("X-CSRF-Token", "")), s["csrf"]):
            self.fail(HTTPStatus.FORBIDDEN, "Token de seguridad inválido"); return None
        return s

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/"): return self.api_get(path)
        if path.startswith("/uploads/"):
            name = Path(path).name
            file = UPLOADS / name
            if not file.is_file(): return self.fail(404,"Archivo no encontrado")
            return self.send_bytes(200,file.read_bytes(),mimetypes.guess_type(name)[0] or "application/octet-stream",{"Cache-Control":"public, max-age=31536000, immutable"})
        if path == "/": path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC.resolve()) + os.sep) or not target.is_file(): return self.fail(404,"Página no encontrada")
        return self.send_bytes(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def api_get(self, path):
        con=connect()
        try:
            if path=="/api/public":
                settings={r["key"]:r["value"] for r in con.execute("SELECT key,value FROM settings")}
                listings=[rowdict(r) for r in con.execute("SELECT id,name,category,price,description,image,seller,spot_id FROM listings WHERE status='approved' ORDER BY id DESC")]
                spots=[rowdict(r) for r in con.execute("SELECT id,code,name,category,x,y,status,CASE WHEN status='occupied' THEN vendor ELSE '' END AS vendor,public_note FROM spots ORDER BY code")]
                return self.json(200,{"event":settings,"listings":listings,"spots":spots})
            if path=="/api/session":
                s=self.session(); return self.json(200,{"authenticated":bool(s),"email":s["email"] if s else None,"csrf":s["csrf"] if s else None})
            if path=="/api/admin":
                if not self.require_admin(): return
                data={"listings":[rowdict(r) for r in con.execute("SELECT * FROM listings ORDER BY id DESC")],
                      "applications":[rowdict(r) for r in con.execute("SELECT * FROM applications ORDER BY id DESC")],
                      "transactions":[rowdict(r) for r in con.execute("SELECT * FROM transactions ORDER BY date DESC,id DESC")],
                      "spots":[rowdict(r) for r in con.execute("SELECT * FROM spots ORDER BY code")],
                      "event":{r["key"]:r["value"] for r in con.execute("SELECT key,value FROM settings")}}
                return self.json(200,data)
            return self.fail(404,"Ruta no encontrada")
        finally: con.close()

    def do_POST(self):
        path=urlparse(self.path).path
        try: data=self.body()
        except ValueError as e: return self.fail(400,str(e))
        con=connect()
        try:
            if path=="/api/applications":
                name=clean_text(data,"name",100); email=clean_text(data,"email",254).lower(); brand=clean_text(data,"brand",120)
                if not name or not brand or "@" not in email: return self.fail(400,"Completa nombre, correo válido y marca")
                cur=con.execute("INSERT INTO applications(name,email,brand,category,description,phone,created_at) VALUES(?,?,?,?,?,?,?)",(name,email,brand,clean_text(data,"category",80),clean_text(data,"description",1500),clean_text(data,"phone",40),now())); con.commit()
                return self.json(201,{"ok":True,"id":cur.lastrowid})
            if path=="/api/login":
                email=clean_text(data,"email",254).lower(); password=clean_text(data,"password",300)
                # Simple per-process brake against repeated guessing; passwords never enter logs.
                key=self.client_address[0]; attempts=getattr(self.server,"login_attempts",{}); count,stamp=attempts.get(key,(0,time.time()))
                if time.time()-stamp>900: count=0
                if count>=8: return self.fail(429,"Demasiados intentos. Espera 15 minutos.")
                row=con.execute("SELECT * FROM admins WHERE email=?",(email,)).fetchone()
                valid=False
                if row:
                    computed,_=password_hash(password,row["salt"]); valid=hmac.compare_digest(computed,row["password_hash"])
                if not valid:
                    attempts[key]=(count+1,time.time()); self.server.login_attempts=attempts
                    return self.fail(401,"Correo o contraseña incorrectos")
                self.server.login_attempts.pop(key,None)
                token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24)
                con.execute("INSERT INTO sessions VALUES(?,?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),row["id"],csrf,(datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"))); con.commit()
                secure="; Secure" if os.environ.get("PUNTO_HTTPS")=="1" else ""
                return self.json(200,{"ok":True,"email":email,"csrf":csrf},{"Set-Cookie":f"pm_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_DAYS*86400}{secure}"})
            if path=="/api/logout":
                s=self.require_admin(csrf=True)
                if not s: return
                con.execute("DELETE FROM sessions WHERE token_hash=?",(s["token_hash"],)); con.commit()
                return self.json(200,{"ok":True},{"Set-Cookie":"pm_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            s=self.require_admin(csrf=True)
            if not s:return
            return self.admin_post(path,data,con)
        except ValueError as e: return self.fail(400,str(e))
        except sqlite3.IntegrityError: return self.fail(400,"El registro no se pudo guardar; revisa los datos únicos")
        finally: con.close()

    def do_PATCH(self):
        path=urlparse(self.path).path
        try: data=self.body()
        except ValueError as e: return self.fail(400,str(e))
        s=self.require_admin(csrf=True)
        if not s:return
        con=connect()
        try:
            parts=path.strip("/").split("/")
            if len(parts)!=4 or parts[:2]!=["api","admin"]: return self.fail(404,"Ruta no encontrada")
            table,ident=parts[2],int(parts[3])
            allowed={"applications":{"status":["pending","approved","rejected"]},"listings":{"status":["pending","approved","hidden"]},"spots":{"status":["available","occupied","info"]}}
            if table not in allowed or "status" not in data or data["status"] not in allowed[table]["status"]: return self.fail(400,"Cambio de estado no válido")
            con.execute(f"UPDATE {table} SET status=? WHERE id=?",(data["status"],ident)); con.commit()
            return self.json(200,{"ok":True})
        except (ValueError,TypeError): return self.fail(400,"Identificador no válido")
        finally: con.close()

    def admin_post(self,path,data,con):
        if path=="/api/admin/listings":
            name=clean_text(data,"name",120); price=float(data.get("price",0))
            if not name or not 0<=price<=1000000: return self.fail(400,"Nombre y precio válido requeridos")
            image=clean_text(data,"image",100); spot=int(data["spot_id"]) if str(data.get("spot_id","")).isdigit() else None
            cur=con.execute("INSERT INTO listings(name,category,price,description,image,seller,spot_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(name,clean_text(data,"category",80,"Otros"),price,clean_text(data,"description",2000),image,clean_text(data,"seller",120),spot,data.get("status","approved") if data.get("status") in ("approved","pending","hidden") else "approved",now()));con.commit()
            return self.json(201,{"ok":True,"id":cur.lastrowid})
        if path=="/api/admin/transactions":
            kind=clean_text(data,"kind",20); amount=float(data.get("amount",0)); date=clean_text(data,"date",30,now()[:10])
            if kind not in ("sale","purchase") or amount<=0: return self.fail(400,"Tipo o monto inválido")
            cur=con.execute("INSERT INTO transactions(kind,description,amount,counterparty,date,notes) VALUES(?,?,?,?,?,?)",(kind,clean_text(data,"description",180),amount,clean_text(data,"counterparty",120),date,clean_text(data,"notes",1000)));con.commit()
            return self.json(201,{"ok":True,"id":cur.lastrowid})
        if path=="/api/admin/spots":
            code=clean_text(data,"code",16).upper(); x=float(data.get("x",50));y=float(data.get("y",50))
            if not code or not 0<=x<=100 or not 0<=y<=100:return self.fail(400,"Código y coordenadas entre 0 y 100 requeridos")
            cur=con.execute("INSERT INTO spots(code,name,category,x,y,status,vendor,public_note) VALUES(?,?,?,?,?,?,?,?)",(code,clean_text(data,"name",80,code),clean_text(data,"category",80),x,y,"available",clean_text(data,"vendor",120),clean_text(data,"public_note",300)));con.commit()
            return self.json(201,{"ok":True,"id":cur.lastrowid})
        if path=="/api/admin/event":
            valid={"event_name","event_tagline","event_date","event_location","event_hours","event_description"}
            for k,v in data.items():
                if k in valid: con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,clean_text(data,k,500)))
            con.commit();return self.json(200,{"ok":True})
        if path=="/api/admin/upload":
            b64=clean_text(data,"data",MAX_BODY); mime=clean_text(data,"mime",60)
            if mime not in ("image/jpeg","image/png","image/webp"): return self.fail(400,"Usa una imagen JPEG, PNG o WebP")
            import base64
            try: raw=base64.b64decode(b64,validate=True)
            except ValueError:return self.fail(400,"Imagen inválida")
            if not raw or len(raw)>5*1024*1024:return self.fail(400,"La imagen debe pesar menos de 5 MB")
            sig={"image/jpeg":b"\xff\xd8\xff","image/png":b"\x89PNG\r\n\x1a\n","image/webp":b"RIFF"}
            if not raw.startswith(sig[mime]) or (mime=="image/webp" and raw[8:12]!=b"WEBP"):return self.fail(400,"El contenido no coincide con el formato de imagen")
            ext={"image/jpeg":"jpg","image/png":"png","image/webp":"webp"}[mime];name=secrets.token_hex(16)+"."+ext
            (UPLOADS/name).write_bytes(raw);return self.json(201,{"ok":True,"url":"/uploads/"+name})
        return self.fail(404,"Ruta no encontrada")

    def do_DELETE(self):
        s=self.require_admin(csrf=True)
        if not s:return
        parts=urlparse(self.path).path.strip("/").split("/")
        if len(parts)!=4 or parts[:2]!=["api","admin"] or parts[2] not in ("listings","transactions","spots"):return self.fail(404,"Ruta no encontrada")
        try: ident=int(parts[3])
        except ValueError:return self.fail(400,"Identificador no válido")
        con=connect()
        try: con.execute(f"DELETE FROM {parts[2]} WHERE id=?",(ident,));con.commit();return self.json(200,{"ok":True})
        finally:con.close()

    def do_PUT(self): return self.fail(405,"Método no permitido")

def create_admin(email,password):
    if len(password)<12: raise SystemExit("La contraseña debe tener al menos 12 caracteres.")
    con=connect(); hashed,salt=password_hash(password)
    try: con.execute("INSERT INTO admins(email,password_hash,salt,role,created_at) VALUES(?,?,?,'admin',?)",(email.lower(),hashed,salt,now()));con.commit()
    except sqlite3.IntegrityError: raise SystemExit("Ya existe una cuenta con ese correo.")
    finally:con.close()
    print(f"Administrador creado: {email}")

def main():
    parser=argparse.ArgumentParser(description="Servidor de Punto Market");sub=parser.add_subparsers(dest="command")
    create=sub.add_parser("create-admin",help="Crear una cuenta de administrador");create.add_argument("email");create.add_argument("--password",help="Contraseña (si no se especifica, se pide de forma segura)")
    args=parser.parse_args()
    if args.command=="create-admin":
        import getpass
        password=args.password or getpass.getpass("Contraseña (mínimo 12 caracteres): ");create_admin(args.email,password);return
    con=connect();con.close()
    host=os.environ.get("PUNTO_HOST","127.0.0.1");port=int(os.environ.get("PORT","8000"))
    server=ThreadingHTTPServer((host,port),Handler);server.login_attempts={}
    print(f"Punto Market en http://{host}:{port} — Ctrl+C para salir")
    try:server.serve_forever()
    except KeyboardInterrupt:print("\nServidor detenido.")
    finally:server.server_close()

if __name__=="__main__":main()
