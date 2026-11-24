// Formularios de movimiento: muestra solo los campos del tipo elegido y
// cambia la lista de categorías entre gasto e ingreso.
(function () {
  document.querySelectorAll("form").forEach((form) => {
    const radios = form.querySelectorAll('input[name="tipo"]');
    const select = form.querySelector("select[data-categorias]");
    if (!select && !form.querySelector("[data-solo]")) return;

    function tipoActual() {
      const r = form.querySelector('input[name="tipo"]:checked');
      if (r) return r.value;
      const s = form.querySelector('select[name="tipo"]');
      return s ? s.value : "gasto";
    }

    let ultimo = tipoActual();
    function aplicar() {
      const tipo = tipoActual();
      form.querySelectorAll("[data-solo]").forEach((el) => {
        el.hidden = !el.dataset.solo.split(" ").includes(tipo);
      });
      if (select && tipo !== ultimo && tipo !== "ahorro") {
        const plantilla = form.querySelector(`template[data-plantilla="${tipo}"]`);
        if (plantilla) {
          select.innerHTML = '<option value="">— Elige una categoría —</option>';
          select.insertAdjacentHTML("beforeend", plantilla.innerHTML);
          select.value = "";
        }
      }
      if (tipo !== "ahorro") ultimo = tipo;
    }
    radios.forEach((r) => r.addEventListener("change", aplicar));
    form.querySelectorAll('select[name="tipo"]').forEach((s) => s.addEventListener("change", aplicar));
    aplicar();
  });
})();
