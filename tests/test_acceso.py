"""Acceso desde el iPhone: solo por Tailscale, con clave, y configurable solo desde el computador."""
import pytest
from fastapi.testclient import TestClient

from finanzas import acceso
from finanzas.services.errores import ErrorValidacion
from finanzas.web.app import COOKIE_SESION, crear_app

IP_IPHONE = ("100.101.102.103", 50000)    # dirección de Tailscale
IP_WIFI = ("192.168.1.50", 50000)         # otro equipo de la red de la casa


@pytest.fixture
def app(con):
    return crear_app()


def _cliente(app, ip=IP_IPHONE, host="nombre-pc.tail1234.ts.net:8765"):
    return TestClient(app, client=ip, base_url=f"http://{host}")


def _activar(clave="Clave2026"):
    acceso.fijar_clave(clave, clave)
    acceso.activar_remoto(True)


# ------------------------------------------------------------------ clave

def test_clave_se_guarda_cifrada_y_fuera_de_la_base(con):
    acceso.fijar_clave("Clave2026", "Clave2026")
    texto = acceso._ruta().read_text(encoding="utf-8")
    assert "Clave2026" not in texto and acceso.verificar("Clave2026") and not acceso.verificar("clave2026")
    assert con.execute("SELECT COUNT(*) FROM configuracion WHERE clave LIKE 'acceso%'").fetchone()[0] == 0


@pytest.mark.parametrize("nueva,confirmacion,error", [
    ("abc", "abc", "al menos 6"),
    ("Clave2026", "Clave2027", "no coincide"),
    ("123456", "123456", "fácil"),
    ("000000", "000000", "fácil"),
    ("987654", "987654", "fácil"),
])
def test_validaciones_clave(con, nueva, confirmacion, error):
    with pytest.raises(ErrorValidacion, match=error):
        acceso.fijar_clave(nueva, confirmacion)


def test_cambiar_clave_pide_la_actual_y_cierra_sesiones(con):
    acceso.fijar_clave("Clave2026", "Clave2026")
    sesion = acceso.crear_sesion()
    assert acceso.sesion_valida(sesion)
    with pytest.raises(ErrorValidacion, match="actual no es correcta"):
        acceso.fijar_clave("OtraClave9", "OtraClave9", "equivocada")
    acceso.fijar_clave("OtraClave9", "OtraClave9", "Clave2026")
    assert not acceso.sesion_valida(sesion)


def test_sesion_vence_y_no_se_falsifica(con):
    acceso.fijar_clave("Clave2026", "Clave2026")
    s = acceso.crear_sesion(ahora=1_000)
    assert acceso.sesion_valida(s, ahora=2_000)
    assert not acceso.sesion_valida(s, ahora=1_000 + acceso.DURACION_SESION + 1)
    vence, firma = s.split(".")
    assert not acceso.sesion_valida(f"{int(vence) + 999}.{firma}", ahora=2_000)
    assert not acceso.sesion_valida("basura") and not acceso.sesion_valida(None)


def test_activar_exige_clave(con):
    with pytest.raises(ErrorValidacion, match="Primero crea"):
        acceso.activar_remoto(True)


def test_intentos_bloquean():
    i = acceso.Intentos(por_ip=3, total=5, bloqueo=60)
    for _ in range(3):
        i.fallo("a", ahora=100)
    assert i.bloqueado("a", ahora=110) and not i.bloqueado("b", ahora=110)
    assert not i.bloqueado("a", ahora=200)  # pasó el bloqueo
    i.fallo("b", ahora=300), i.fallo("c", ahora=300), i.fallo("d", ahora=300)
    i.fallo("e", ahora=300), i.fallo("f", ahora=300)
    assert i.bloqueado("z", ahora=301)       # límite total


def test_redes():
    assert acceso.es_tailscale("100.101.102.103") and not acceso.es_tailscale("192.168.1.5")
    assert acceso.es_local("127.0.0.1") and not acceso.es_local("100.101.102.103")
    assert acceso.host_permitido("mi-pc.tail1234.ts.net:8765") and acceso.host_permitido("127.0.0.1:8765")
    assert not acceso.host_permitido("atacante.com")


# ------------------------------------------------------------------ web

def test_desde_el_computador_no_pide_clave(app):
    _activar()
    with TestClient(app) as c:
        assert c.get("/").status_code == 200 and "Salir" not in c.get("/").text


def test_iphone_bloqueado_si_no_esta_activado(app):
    acceso.fijar_clave("Clave2026", "Clave2026")
    with _cliente(app) as c:
        assert c.get("/").status_code == 403


def test_otra_red_siempre_bloqueada(app):
    _activar()
    with _cliente(app, ip=IP_WIFI, host="192.168.1.10:8765") as c:
        assert c.get("/").status_code == 403
        assert c.get("/acceso").status_code == 403


def test_host_extrano_bloqueado(app):
    _activar()
    with _cliente(app, host="atacante.com:8765") as c:
        assert c.get("/acceso").status_code == 400


def test_iphone_entra_con_clave(app):
    _activar()
    with _cliente(app) as c:
        r = c.get("/cuentas")
        assert "Escribe tu clave de acceso" in r.text and "siguiente=%2Fcuentas" in str(r.url)
        assert c.get("/reporte.pdf", follow_redirects=False).status_code == 303
        assert c.get("/static/icono-180.png").status_code == 200
        assert c.get("/manifest.webmanifest").json()["display"] == "standalone"
        r = c.post("/acceso", data={"clave": "mala", "siguiente": "/cuentas"})
        assert "Clave incorrecta" in r.text
        r = c.post("/acceso", data={"clave": "Clave2026", "siguiente": "/cuentas"})
        assert r.status_code == 200 and "Mover dinero entre cuentas" in r.text and "Salir" in r.text
        cookie = c.cookies.get(COOKIE_SESION)
        assert cookie and acceso.sesion_valida(cookie)
        c.post("/salir")
        assert "Escribe tu clave de acceso" in c.get("/").text


def test_no_redirige_a_otro_sitio(app):
    _activar()
    with _cliente(app) as c:
        r = c.post("/acceso", data={"clave": "Clave2026", "siguiente": "//atacante.com"}, follow_redirects=False)
        assert r.headers["location"] == "/"


def test_bloqueo_por_intentos(app):
    _activar()
    with _cliente(app) as c:
        for _ in range(5):
            c.post("/acceso", data={"clave": "mala"})
        r = c.post("/acceso", data={"clave": "Clave2026"})
        assert "Demasiados intentos" in r.text


def test_iphone_no_cambia_seguridad(app):
    _activar()
    with _cliente(app) as c:
        c.post("/acceso", data={"clave": "Clave2026"})
        r = c.post("/configuracion/acceso-desactivar")
        assert "solo se configura desde el computador" in r.text
        r = c.post("/configuracion/acceso-clave", data={"actual": "Clave2026", "nueva": "Hackeo999", "confirmacion": "Hackeo999"})
        assert "solo se configura desde el computador" in r.text
    assert acceso.remoto_activo() and acceso.verificar("Clave2026")


def test_configuracion_desde_el_computador(app):
    with TestClient(app) as c:
        r = c.get("/configuracion")
        assert "Acceso desde el iPhone" in r.text and "Crear la clave de acceso" in r.text
        r = c.post("/configuracion/acceso-clave", data={"nueva": "Clave2026", "confirmacion": "Clave2026"})
        assert "Clave de acceso guardada" in r.text
        r = c.post("/configuracion/acceso-activar")
        assert "Acceso desde el iPhone activado" in r.text and "vuelve a abrir la app" in r.text
        assert acceso.remoto_activo()
        r = c.post("/configuracion/acceso-desactivar")
        assert not acceso.remoto_activo()
