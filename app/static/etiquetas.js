/* ---------- Etiquetas: el programa de siempre (etiquetas_tiendas.html) + cuadro para pedirlas por tienda ---------- */
let ET_RES = null;
const etPr = n => (+n).toFixed(2).replace('.', ',');
const etHoy = (d = 0) => { const x = new Date(); x.setDate(x.getDate() + d); return x.toISOString().slice(0, 10); };

function etTabla(lista) {   // mismas columnas que el CSV que exporta Odoo (+ precio con IVA y cantidad)
  return [['Nombre', 'Referencia interna', 'Precio de venta', 'IVA', 'Precio con IVA', 'Codigo de barras', 'Cantidad']]
    .concat(lista.map(d => [d.nombre, d.ref, etPr(d.base), d.base ? Math.round((d.precio / d.base - 1) * 100) : 21, etPr(d.precio), d.cod, d.cant || 1]));
}
function etAlPrograma(lista, titulo) {
  const w = $('#et-app').contentWindow;
  if (!w || !w.procesarTabla) return alertBox('El programa de etiquetas aún se está cargando, prueba en un segundo');
  w.procesarTabla(etTabla(lista));
  $('#et-app').scrollIntoView({ behavior: 'smooth' });
  try { w.document.getElementById('info').textContent += ' · ' + titulo; } catch (_) { }
}
function etCSV(lista, nombre) {
  const csv = '﻿' + etTabla(lista).map(f => f.map(c => String(c ?? '').replace(/;/g, ',')).join(';')).join('\r\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = nombre + '.csv'; a.click();
}
function etPintaRes(r, titulo) {
  ET_RES = r;
  const nom = { T1: 'Tienda 1', T2: 'Tienda 2', T3: 'Tienda Logipaper' };
  const fila = (k, lista, etq) => `<div class="et-t"><b>${etq}</b><span>${lista.length} artículo(s)</span>
    ${lista.length ? `<button class="ac-go" data-k="${k}">Ver en el programa</button><button class="btn" data-csv="${k}">⬇ CSV</button>` : '<span class="muted">nada en stock</span>'}</div>`;
  $('#et-res').innerHTML = `<div class="et-tit">${escH(titulo)} · 1 etiqueta por artículo, solo lo que cada tienda tiene en stock</div>
    ${Object.keys(nom).filter(k => r.tiendas[k]).map(k => fila(k, r.tiendas[k], nom[k])).join('')}
    ${r.todos.length ? fila('todos', r.todos, 'Todos (sin mirar stock)') : ''}
    ${r.sin_stock.length ? `<p class="muted">Sin stock en ninguna tienda: ${r.sin_stock.map(escH).join(', ')}</p>` : ''}
    ${r.sin_precio.length ? `<p class="muted">Fuera por no tener precio de venta: ${r.sin_precio.map(escH).join(', ')}</p>` : ''}`;
  const lista = k => k === 'todos' ? r.todos : r.tiendas[k];
  const fecha = new Date().toISOString().slice(0, 10);
  $('#et-res').querySelectorAll('[data-k]').forEach(b => b.onclick = () => etAlPrograma(lista(b.dataset.k), `${nom[b.dataset.k] || 'Todos'} · ${titulo}`));
  $('#et-res').querySelectorAll('[data-csv]').forEach(b => b.onclick = () => etCSV(lista(b.dataset.csv), `Etiquetas_${b.dataset.csv}_${fecha}`));
}
async function etTiendas(cuerpo, titulo) {
  $('#et-res').innerHTML = '<p class="muted">Mirando el stock de cada tienda…</p>';
  try { etPintaRes(await jpost('/api/etiquetas/tiendas', cuerpo), titulo); }
  catch (e) { $('#et-res').innerHTML = ''; alertBox(e.message); }
}

async function etOpen() {
  if (!$('#et-desde').value) $('#et-desde').value = etHoy(-7);
  const m = location.hash.match(/[?&](tmpl|prod)=([\d,]+)/);   // lo marcado en Odoo (Acción → 🏷 Etiquetas en el Panel)
  if (m) {
    history.replaceState(null, '', '#etiquetas');
    const ids = m[2].split(',').filter(Boolean);
    etTiendas(m[1] === 'tmpl' ? { plantillas: ids } : { productos: ids }, `${ids.length} marcado(s) en Odoo`);
  }
}

$('#et-go').onclick = async () => {
  const t = $('#et-q').value.trim(); if (!t) return $('#et-q').focus();
  const b = $('#et-go'); b.disabled = true; b.textContent = 'Buscando…';
  $('#et-res').innerHTML = '<p class="muted">Buscando en Odoo…</p>';
  try {
    const r = await jpost('/api/etiquetas/pedir', { texto: t });
    if (!r.etiquetas.length) $('#et-res').innerHTML = `<p class="muted">${escH(r.explicacion || 'No he encontrado nada')}</p>`;
    else await etTiendas({ productos: r.etiquetas.map(e => e.id) }, r.explicacion || t);
  } catch (e) { $('#et-res').innerHTML = ''; alertBox(e.message); }
  b.disabled = false; b.textContent = 'Sacar por tienda';
};
$('#et-q').onkeydown = e => { if (e.key === 'Enter') $('#et-go').click(); };

$('#et-ult').onclick = async () => {
  $('#et-ent').innerHTML = '<p class="muted">Cargando entradas…</p>';
  try {
    const L = (await api('/api/etiquetas/entradas?dias=20')).filter(x => !x.entre_empresas);
    $('#et-ent').innerHTML = L.length ? `<div class="et-ent">${L.map((x, i) => `<label><input type="checkbox" value="${x.id}" ${i === 0 ? 'checked' : ''}>
        <b>${escH(x.proveedor)}</b> <span class="muted">${escH(x.nombre)}${x.origen ? ' · ' + escH(x.origen) : ''} · ${coDate(x.fecha.slice(0, 10))} · ${x.lineas} líneas</span></label>`).join('')}</div>
      <button class="ac-go" id="et-ent-ok">Sacar etiquetas de las marcadas</button>` : '<p class="muted">No hay entradas de proveedor en los últimos 20 días</p>';
    const ok = $('#et-ent-ok'); if (ok) ok.onclick = () => {
      const sel = [...$('#et-ent').querySelectorAll('input:checked')];
      if (!sel.length) return alertBox('Marca al menos una entrada');
      etTiendas({ entradas: sel.map(i => +i.value) }, sel.map(i => i.closest('label').querySelector('b').textContent + ' ' + i.closest('label').querySelector('span').textContent.split(' · ')[0]).join(' + '));
    };
  } catch (e) { $('#et-ent').innerHTML = ''; alertBox(e.message); }
};
$('#et-cat').onclick = () => {
  const d = $('#et-desde').value; if (!d) return alertBox('Pon la fecha desde la que han cambiado los precios');
  etTiendas({ desde: d }, `Artículos que han cambiado de precio desde ${coDate(d)}`);
};
