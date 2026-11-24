"""Acceso desde otros dispositivos (iPhone) a través de Tailscale, protegido con clave.

- Desde el propio computador (127.0.0.1) la app abre sin clave, como siempre.
- Desde otro dispositivo solo se puede entrar si el acceso remoto está activado, la conexión
  llega por la red privada de Tailscale (100.64.0.0/10) y se escribe la clave.
- La clave se guarda como hash scrypt con sal en `data/acceso.json` (no en la base de datos,
  para que no quede en la auditoría ni en las exportaciones). Junto a ella va un secreto
  aleatorio con el que se firman las sesiones; al cambiar la clave se cambia el secreto y
  todas las sesiones abiertas se cierran.
- La clave y la activación solo se cambian desde el propio computador.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

from . import config
from .services.errores import ErrorValidacion

LARGO_MINIMO = 6
DURACION_SESION = 30 * 24 * 3600  # 30 días
RED_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
RED_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
LOCALES = {"127.0.0.1", "::1", "localhost", "testclient"}


def _ruta() -> Path:
    return config.dir_datos() / "acceso.json"


def _leer() -> dict:
    try:
        return json.loads(_ruta().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _escribir(datos: dict) -> None:
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    tmp.replace(ruta)


# ----------------------------------------------------------------- clave

def _hash(clave: str, sal: bytes) -> str:
    return hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=2**14, r=8, p=1, dklen=32).hex()


FACILES = {"123456", "1234567", "12345678", "123456789", "1234567890", "654321", "987654321",
           "abcdef", "abc123", "qwerty", "password", "contraseña", "contrasena", "finanzas", "111111"}


def demasiado_facil(clave: str) -> bool:
    """Todo el mismo carácter, una secuencia (123456, 654321) o una clave muy común."""
    c = clave.lower()
    if len(set(c)) == 1 or c in FACILES:
        return True
    if c.isdigit():
        pasos = {int(b) - int(a) for a, b in zip(c, c[1:])}
        return pasos in ({1}, {-1})
    return False


def tiene_clave() -> bool:
    return "clave" in _leer()


def fijar_clave(nueva: str, confirmacion: str, actual: str | None = None) -> None:
    errores = []
    if tiene_clave() and not verificar(actual or ""):
        errores.append("La clave actual no es correcta.")
    if len(nueva or "") < LARGO_MINIMO:
        errores.append(f"La clave debe tener al menos {LARGO_MINIMO} caracteres.")
    elif nueva != confirmacion:
        errores.append("La confirmación no coincide con la clave nueva.")
    elif demasiado_facil(nueva):
        errores.append("Esa clave es demasiado fácil de adivinar.")
    if errores:
        raise ErrorValidacion(errores)
    datos = _leer()
    sal = secrets.token_bytes(16)
    datos["clave"] = {"sal": sal.hex(), "hash": _hash(nueva, sal)}
    datos["secreto"] = secrets.token_hex(32)  # invalida las sesiones abiertas
    _escribir(datos)


def verificar(clave: str) -> bool:
    c = _leer().get("clave")
    if not c or not clave:
        return False
    return hmac.compare_digest(_hash(clave, bytes.fromhex(c["sal"])), c["hash"])


# ----------------------------------------------------------------- activación

def remoto_activo() -> bool:
    datos = _leer()
    return bool(datos.get("remoto")) and "clave" in datos


def activar_remoto(activo: bool) -> None:
    if activo and not tiene_clave():
        raise ErrorValidacion("Primero crea una clave de acceso.")
    datos = _leer()
    datos["remoto"] = bool(activo)
    if not activo:
        datos["secreto"] = secrets.token_hex(32)  # cierra las sesiones del iPhone
    _escribir(datos)


# ----------------------------------------------------------------- sesiones

def _secreto() -> bytes:
    datos = _leer()
    if "secreto" not in datos:
        datos["secreto"] = secrets.token_hex(32)
        _escribir(datos)
    return bytes.fromhex(datos["secreto"])


def crear_sesion(ahora: float | None = None) -> str:
    vence = int((ahora or time.time()) + DURACION_SESION)
    firma = hmac.new(_secreto(), str(vence).encode(), hashlib.sha256).hexdigest()
    return f"{vence}.{firma}"


def sesion_valida(token: str | None, ahora: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    vence, firma = token.split(".", 1)
    if not vence.isdigit() or int(vence) < (ahora or time.time()):
        return False
    esperada = hmac.new(_secreto(), vence.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma, esperada)


# ----------------------------------------------------------------- red

def es_local(ip: str | None) -> bool:
    return ip in LOCALES


def es_tailscale(ip: str | None) -> bool:
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return a in RED_TAILSCALE if a.version == 4 else a in RED_TAILSCALE_V6


def host_permitido(host: str) -> bool:
    """Nombre con el que se pide la página (protege contra 'DNS rebinding')."""
    nombre = host.rsplit(":", 1)[0].strip("[]").lower() if host else ""
    return nombre in LOCALES or nombre == "testserver" or nombre.endswith(".ts.net") or es_tailscale(nombre)


class Intentos:
    """Limita los intentos fallidos de clave: 5 por dispositivo y 20 en total, bloqueo de 15 min."""

    def __init__(self, por_ip: int = 5, total: int = 20, bloqueo: int = 15 * 60):
        self.por_ip, self.total, self.bloqueo = por_ip, total, bloqueo
        self.fallos: dict[str, list[float]] = {}

    def _recientes(self, ip: str, ahora: float) -> list[float]:
        return [t for t in self.fallos.get(ip, []) if ahora - t < self.bloqueo]

    def bloqueado(self, ip: str, ahora: float | None = None) -> bool:
        ahora = ahora or time.time()
        total = sum(len(self._recientes(i, ahora)) for i in self.fallos)
        return len(self._recientes(ip, ahora)) >= self.por_ip or total >= self.total

    def fallo(self, ip: str, ahora: float | None = None) -> None:
        ahora = ahora or time.time()
        self.fallos[ip] = self._recientes(ip, ahora) + [ahora]

    def exito(self, ip: str) -> None:
        self.fallos.pop(ip, None)


# ----------------------------------------------------------------- Tailscale

def _ejecutable_tailscale() -> str | None:
    for c in (shutil.which("tailscale"), r"C:\Program Files\Tailscale\tailscale.exe",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if c and Path(c).exists():
            return c
    return None


def datos_tailscale() -> dict:
    """IP y nombre de este equipo en la red de Tailscale (vacío si no está instalado o conectado)."""
    res: dict = {"instalado": False, "ip": None, "nombre": None}
    exe = _ejecutable_tailscale()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if exe:
        res["instalado"] = True
        try:
            salida = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=8,
                                    creationflags=flags, check=False).stdout
            yo = json.loads(salida or "{}").get("Self") or {}
            ips = [i for i in yo.get("TailscaleIPs") or [] if es_tailscale(i) and ":" not in i]
            res["ip"] = ips[0] if ips else None
            res["nombre"] = (yo.get("DNSName") or "").rstrip(".") or None
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if not res["ip"]:  # respaldo: buscar una IP 100.64.0.0/10 entre las del equipo
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                if es_tailscale(info[4][0]):
                    res["ip"] = info[4][0]
                    break
        except OSError:
            pass
    return res
