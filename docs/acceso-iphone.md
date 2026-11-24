# Usar Finanzas desde el iPhone (con Tailscale)

Tus datos **siguen en tu computador**. Tailscale crea una red privada solo entre tus
dispositivos: el iPhone llega a la app como si estuviera en tu casa, desde cualquier lugar,
y la conexión va cifrada de punta a punta. La app no queda publicada en internet.

> El computador tiene que estar **encendido, sin suspender, con Tailscale conectado y la app abierta**
> (`iniciar.bat`).

## Una sola vez

### 1. Crea tu cuenta de Tailscale
Entra a <https://tailscale.com>, elige **Get started** y entra con tu cuenta de Google, Apple
o Microsoft. El plan **Personal** es gratuito.

### 2. Instala Tailscale en el computador
Descárgalo desde <https://tailscale.com/download/windows>, instálalo y entra con la misma cuenta.
Debe aparecer el ícono de Tailscale junto al reloj, con el estado **Connected**.

### 3. Instala Tailscale en el iPhone
En la App Store busca **Tailscale**, instálalo y entra con la **misma cuenta**. Acepta la
configuración de VPN que te pide iOS y deja el interruptor encendido.

### 4. Activa el acceso en la app (desde el computador)
1. Abre la app (`iniciar.bat`) → **Configuración** → **Acceso desde el iPhone**.
2. Crea tu **clave de acceso** (mejor 8 o más caracteres, con letras y números).
3. Pulsa **Activar acceso desde el iPhone**.
4. **Cierra y vuelve a abrir** la app. En la ventana negra debe aparecer
   `Desde el iPhone (Tailscale): http://...:8765`.
5. Si Windows pregunta si permites que Python use la red, pulsa **Permitir**.

### 5. Ábrela en el iPhone
1. En Safari abre la dirección que muestra **Configuración → Acceso desde el iPhone**
   (por ejemplo `http://mi-pc.tail1234.ts.net:8765` o `http://100.x.y.z:8765`).
2. Escribe tu clave. La sesión dura 30 días en ese iPhone.
3. Botón **Compartir** → **Agregar a pantalla de inicio**: queda como una app con el ícono «$».

## Si no conecta

| Qué pasa | Qué revisar |
|---|---|
| Safari no carga la página | Tailscale encendido en el iPhone y conectado en el PC; la app abierta en el PC. |
| La ventana de la app no muestra la línea «Desde el iPhone» | Tailscale se conectó después de abrir la app: ciérrala y ábrela de nuevo. |
| Funciona y luego deja de funcionar | El PC se suspendió. En Windows: Configuración → Sistema → Energía → «Suspender» en **Nunca** cuando está conectado a la corriente. |
| «Acceso no permitido» | El acceso está desactivado o el iPhone no está entrando por Tailscale (revisa que la VPN esté encendida). |
| «Demasiados intentos fallidos» | Espera 15 minutos. |
| Windows bloqueó la conexión | Panel de control → Firewall de Windows Defender → «Permitir una aplicación» → habilita Python. |

## Seguridad

- Solo los dispositivos que entraron con **tu** cuenta de Tailscale pueden llegar a la app, y además
  necesitan la clave. Desde cualquier otra red la app responde «Acceso no permitido».
- La clave se guarda cifrada (scrypt) en `data/acceso.json`, nunca en texto plano ni en la base.
- La clave y la activación solo se cambian **desde el computador**, nunca desde el iPhone.
- Cambiar la clave o desactivar el acceso **cierra todas las sesiones** abiertas en el iPhone.
- **Si pierdes el iPhone:** en el PC, desactiva el acceso (o cambia la clave) y en
  <https://login.tailscale.com/admin/machines> elimina ese dispositivo.
- No invites a otras personas a tu red de Tailscale.
