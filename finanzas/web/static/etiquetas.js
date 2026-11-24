// Autocompletado de #categoría en los campos de texto libre (registro rápido e importar texto).
// Al escribir «#» muestra las categorías; al seguir escribiendo filtra las que contienen el texto
// (sin importar tildes ni mayúsculas). Flechas para moverse, Enter o Tab para elegir, Esc para cerrar.
(function () {
  const fuente = document.getElementById("etiquetas-categorias");
  const campos = document.querySelectorAll("[data-etiquetas]");
  if (!fuente || !campos.length) return;
  let categorias;
  try { categorias = JSON.parse(fuente.textContent); } catch (e) { return; }

  const norm = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const clave = (t) => norm(t).replace(/[^a-z0-9ñ]/g, "");
  categorias.forEach((c) => {
    c.buscar = [clave(c.nombre), clave(c.padre), c.etiqueta.replace("/", "")];
  });

  campos.forEach((campo) => {
    const caja = document.createElement("div");
    caja.className = "sugerencias";
    caja.setAttribute("role", "listbox");
    caja.hidden = true;
    // El campo va dentro de un contenedor para que la lista quede justo debajo de él.
    const envoltura = document.createElement("div");
    envoltura.className = "campo-etiquetas" + (campo.tagName === "TEXTAREA" ? " bloque" : "");
    campo.parentNode.insertBefore(envoltura, campo);
    envoltura.append(campo, caja);
    campo.setAttribute("aria-autocomplete", "list");
    let opciones = [], activa = 0, inicio = -1;

    function lineaActual(antes) {
      return antes.slice(antes.lastIndexOf("\n") + 1);
    }

    function cerrar() { caja.hidden = true; opciones = []; inicio = -1; }

    function actualizar() {
      const pos = campo.selectionStart;
      const antes = campo.value.slice(0, pos);
      const m = /#([^\s#()]*)$/.exec(antes);
      if (!m || campo.selectionStart !== campo.selectionEnd) return cerrar();
      inicio = pos - m[0].length;
      const linea = lineaActual(antes).trim();
      if (/(^|\s)ahorr(o|é|e|ar)(\s|$)/i.test(linea)) return cerrar();  // el ahorro no lleva categoría
      const tipo = linea.startsWith("+") ? "ingreso" : "gasto";
      const q = clave(m[1]);
      opciones = categorias.filter((c) => c.tipo === tipo && (!q || c.buscar.some((b) => b.includes(q))));
      // Primero las que empiezan por lo escrito.
      opciones.sort((a, b) => (q && !clave(a.nombre).startsWith(q)) - (q && !clave(b.nombre).startsWith(q)));
      activa = 0;
      pintar(q, tipo);
    }

    function pintar(q, tipo) {
      caja.innerHTML = "";
      if (!opciones.length) {
        const vacio = document.createElement("div");
        vacio.className = "sugerencia vacia";
        vacio.textContent = `No hay categorías de ${tipo} que contengan «${q}».`;
        caja.appendChild(vacio);
      }
      opciones.forEach((c, i) => {
        const fila = document.createElement("div");
        fila.className = "sugerencia" + (i === activa ? " activa" : "");
        fila.setAttribute("role", "option");
        fila.id = `sug-${campo.name}-${i}`;
        const nombre = document.createElement("span");
        nombre.textContent = c.padre ? `${c.padre} › ` : "";
        nombre.className = "padre";
        const hija = document.createElement("strong");
        hija.textContent = c.nombre + (c.padre ? "" : " (general)");
        const etq = document.createElement("code");
        etq.textContent = "#" + c.etiqueta;
        fila.append(nombre, hija, etq);
        fila.addEventListener("mousedown", (ev) => { ev.preventDefault(); elegir(i); });
        caja.appendChild(fila);
      });
      caja.hidden = false;
      marcar();
    }

    function marcar() {
      caja.querySelectorAll(".sugerencia").forEach((f, i) => f.classList.toggle("activa", i === activa));
      const f = caja.children[activa];
      if (f && opciones.length) {
        f.scrollIntoView({ block: "nearest" });
        campo.setAttribute("aria-activedescendant", f.id);
      }
    }

    function elegir(i) {
      const c = opciones[i];
      if (!c || inicio < 0) return;
      const pos = campo.selectionStart;
      const despues = campo.value.slice(pos).replace(/^[^\s#()]*/, "");  // completa la palabra a medias
      const texto = "#" + c.etiqueta + (despues.startsWith(" ") ? "" : " ");
      campo.value = campo.value.slice(0, inicio) + texto + despues;
      const nuevo = inicio + texto.length;
      campo.setSelectionRange(nuevo, nuevo);
      cerrar();
      campo.focus();
    }

    campo.addEventListener("input", actualizar);
    campo.addEventListener("click", actualizar);
    campo.addEventListener("blur", () => setTimeout(cerrar, 100));
    campo.addEventListener("keydown", (ev) => {
      if (caja.hidden || !opciones.length) {
        if (ev.key === "Escape") cerrar();
        return;
      }
      if (ev.key === "ArrowDown") { activa = (activa + 1) % opciones.length; marcar(); ev.preventDefault(); }
      else if (ev.key === "ArrowUp") { activa = (activa - 1 + opciones.length) % opciones.length; marcar(); ev.preventDefault(); }
      else if (ev.key === "Enter" || ev.key === "Tab") { elegir(activa); ev.preventDefault(); }
      else if (ev.key === "Escape") { cerrar(); ev.preventDefault(); }
    });
  });
})();
