// Gráficos del dashboard (ECharts local). Colores tomados de los tokens CSS para
// respetar el modo claro/oscuro; texto siempre en tokens de tinta, nunca en el color de la serie.
(function () {
  const datos = JSON.parse(document.getElementById("datos").textContent);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fmt = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
  const cop = (v) => "$ " + fmt.format(Math.round(v));
  const corto = (v) => {
    const a = Math.abs(v);
    if (a >= 1e6) return "$ " + (v / 1e6).toLocaleString("es-CO", { maximumFractionDigits: 1 }) + " M";
    if (a >= 1e3) return "$ " + Math.round(v / 1e3) + " mil";
    return "$ " + v;
  };
  const pct = (v) => (v * 100).toLocaleString("es-CO", { maximumFractionDigits: 0 }) + " %";
  const graficos = [];

  function tema() {
    return {
      tinta: css("--tinta"), tinta2: css("--tinta-2"), tinta3: css("--tinta-3"),
      rejilla: css("--rejilla"), eje: css("--eje"), superficie: css("--superficie"),
      s1: css("--serie-1"), s2: css("--serie-2"), s3: css("--serie-3"), neutra: css("--serie-neutra"),
    };
  }

  function tooltipBase(t) {
    return {
      backgroundColor: t.superficie, borderColor: t.rejilla, borderWidth: 1,
      textStyle: { color: t.tinta, fontSize: 12 }, extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.12);border-radius:8px;",
    };
  }

  function ejeValor(t) {
    return {
      type: "value", axisLabel: { color: t.tinta3, formatter: corto, fontSize: 11 },
      splitLine: { lineStyle: { color: t.rejilla } }, axisLine: { show: false }, axisTick: { show: false },
    };
  }

  function evolucion(t) {
    const el = document.getElementById("g-evolucion");
    if (!el) return;
    const filas = datos.evolucion;
    const g = echarts.init(el, null, { renderer: "svg" });
    const serie = (nombre, clave, color) => ({
      name: nombre, type: "bar", data: filas.map((f) => f[clave]), barMaxWidth: 24, barGap: "12%",
      itemStyle: { color, borderRadius: [4, 4, 0, 0] }, emphasis: { focus: "none" },
    });
    g.setOption({
      animation: false,
      grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
      tooltip: Object.assign(tooltipBase(t), {
        trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: "rgba(127,127,127,0.08)" } },
        formatter: (ps) => {
          const f = filas[ps[0].dataIndex];
          if (f.futuro) return `<b>${f.etiqueta}</b><br><span style="color:${t.tinta2}">Aún no llega</span>`;
          let h = `<b>${f.etiqueta}</b>${f.parcial ? " <span style='color:" + t.tinta3 + "'>(en curso)</span>" : ""}<br>`;
          ps.forEach((p) => { h += `${p.marker}${p.seriesName}: <b>${cop(p.value)}</b><br>`; });
          h += `<span style="color:${t.tinta2}">Balance: ${cop(f.balance)}</span>`;
          return h;
        },
      }),
      xAxis: { type: "category", data: filas.map((f) => f.etiqueta), axisLine: { lineStyle: { color: t.eje } },
               axisTick: { show: false }, axisLabel: { color: t.tinta3, fontSize: 11 } },
      yAxis: ejeValor(t),
      series: [serie("Ingresos", "ingresos", t.s1), serie("Gastos", "gastos", t.s2), serie("Ahorro", "ahorro", t.s3)],
    });
    graficos.push(g);
  }

  function categorias(t) {
    const el = document.getElementById("g-categorias");
    if (!el) return;
    const filas = datos.categorias.slice().reverse();  // la mayor arriba
    const g = echarts.init(el, null, { renderer: "svg" });
    g.setOption({
      animation: false,
      grid: { left: 16, right: 76, top: 4, bottom: 4, containLabel: true },
      tooltip: Object.assign(tooltipBase(t), {
        trigger: "item",
        formatter: (p) => {
          const f = filas[p.dataIndex];
          let h = `<b>${f.nombre}</b><br>${cop(f.total)} · ${pct(f.participacion)} del gasto`;
          if (f.variacion !== null) {
            const signo = f.variacion > 0 ? "▲" : "▼";
            h += `<br><span style="color:${t.tinta2}">${signo} ${pct(Math.abs(f.variacion))} vs ${datos.parcial ? "mismo periodo del mes anterior" : "mes anterior"} (${cop(f.anterior)})</span>`;
          } else if (f.anterior === 0) {
            h += `<br><span style="color:${t.tinta2}">Sin gasto en el periodo anterior</span>`;
          }
          return h;
        },
      }),
      xAxis: { type: "value", show: false },
      yAxis: { type: "category", data: filas.map((f) => f.nombre), axisLine: { show: false }, axisTick: { show: false },
               axisLabel: { color: t.tinta2, fontSize: 12 } },
      series: [{
        type: "bar", data: filas.map((f) => f.total), barMaxWidth: 18, cursor: "pointer",
        itemStyle: { color: t.s1, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", color: t.tinta2, fontSize: 11, formatter: (p) => corto(p.value) },
      }],
    });
    g.on("click", (p) => {
      const f = filas[p.dataIndex];
      const destino = `/movimientos?mes=${datos.mes}&tipo=gasto&categoria=${f.id}`;
      if (window.finanzasIr) window.finanzasIr(destino); else window.location.href = destino;
    });
    graficos.push(g);
  }

  function ritmo(t) {
    const el = document.getElementById("g-ritmo");
    if (!el) return;
    const r = datos.ritmo;
    const g = echarts.init(el, null, { renderer: "svg" });
    const linea = (nombre, data, color, ancho) => ({
      name: nombre, type: "line", data, showSymbol: false, symbolSize: 8, smooth: false,
      lineStyle: { width: ancho, color, cap: "round", join: "round" }, itemStyle: { color, borderColor: t.superficie, borderWidth: 2 },
      emphasis: { focus: "none" },
    });
    const actual = linea(r.etiqueta_actual, r.actual, t.s1, 2);
    if (r.actual.length) {
      actual.markPoint = { symbol: "circle", symbolSize: 9, data: [{ coord: [r.actual.length - 1, r.actual[r.actual.length - 1]] }],
                           itemStyle: { color: t.s1, borderColor: t.superficie, borderWidth: 2 }, label: { show: false } };
    }
    g.setOption({
      animation: false,
      grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
      tooltip: Object.assign(tooltipBase(t), {
        trigger: "axis", axisPointer: { type: "line", lineStyle: { color: t.eje } },
        formatter: (ps) => {
          let h = `<b>Día ${ps[0].axisValue}</b><br>`;
          ps.forEach((p) => { if (p.value !== undefined) h += `${p.marker}${p.seriesName}: <b>${cop(p.value)}</b><br>`; });
          return h;
        },
      }),
      xAxis: { type: "category", data: r.dias, boundaryGap: false, axisLine: { lineStyle: { color: t.eje } },
               axisTick: { show: false }, axisLabel: { color: t.tinta3, fontSize: 11, interval: 4 } },
      yAxis: ejeValor(t),
      series: [linea(r.etiqueta_anterior, r.anterior, t.neutra, 2), actual],
    });
    graficos.push(g);
  }

  function dibujar() {
    graficos.splice(0).forEach((g) => g.dispose());
    const t = tema();
    evolucion(t); categorias(t); ritmo(t);
  }

  dibujar();
  window.addEventListener("resize", () => graficos.forEach((g) => g.resize()));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", dibujar);
})();
