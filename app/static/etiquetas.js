/* ---------- Etiquetas de tienda: 70×30 mm, 27 por A4 (mismo formato que etiquetas_tiendas.html) ---------- */
let ET = [];          // [{id, nombre, ref, cod, precio, escalados:[{desde, precio}], cant, recibido?}]
const ET_LOGO = '/static/etq_logo.png';
const etLS = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || 'null'); localStorage.setItem(k, JSON.stringify(v)); } catch (_) { return null; } };
const etPrecio = n => (n === '' || n == null || isNaN(+n)) ? '' : (+n).toFixed(2).replace('.', ',') + ' €';

function etGuardar() { etLS('et_lista', ET); etLS('et_aj', ['et-msup', 'et-mizq', 'et-hh', 'et-hv', 'et-filas'].map(i => $('#' + i).value).concat([$('#et-borde').checked, $('#et-esc').checked])); }
function etCargarAjustes() {
  const a = etLS('et_aj'); if (!a) return;
  ['et-msup', 'et-mizq', 'et-hh', 'et-hv', 'et-filas'].forEach((i, k) => { if (a[k] != null) $('#' + i).value = a[k]; });
  if (a[5] != null) $('#et-borde').checked = a[5]; if (a[6] != null) $('#et-esc').checked = a[6];
}

async function etOpen() {
  if (!etOpen.hecho) { etOpen.hecho = true; etCargarAjustes(); ET = etLS('et_lista') || []; }
  // artículos marcados en Odoo: #etiquetas?tmpl=1,2,3  o  ?prod=4,5
  const m = location.hash.match(/[?&](tmpl|prod)=([\d,]+)/);
  if (m) {
    history.replaceState(null, '', '#etiquetas');
    $('#et-info').textContent = 'Cargando lo marcado en Odoo…';
    try {
      const ids = m[2].split(',').filter(Boolean);
      const r = await jpost('/api/etiquetas/datos', m[1] === 'tmpl' ? { plantillas: ids } : { productos: ids });
      etAnadir(r, true);
      etAviso(`${r.length} artículo(s) marcados en Odoo.`);
    } catch (e) { alertBox(e.message); }
  }
  etPinta();
}
function etAnadir(lista, reemplazar = false) {
  if (reemplazar) ET = [];
  lista.forEach(e => { const y = ET.find(x => x.id === e.id); if (y) Object.assign(y, e, { cant: y.cant }); else ET.push(e); });
  etGuardar();
}
function etAviso(t) { const r = $('#et-res'); r.hidden = !t; r.innerHTML = t ? escH(t).replace(/\n/g, '<br>') : ''; }

function etVars() {
  const z = $('#et-zona').style, n = (id, d) => { const v = parseFloat($('#' + id).value); return isNaN(v) ? d : v; };
  z.setProperty('--margen-sup', n('et-msup', 13.5) + 'mm'); z.setProperty('--margen-izq', n('et-mizq', 0) + 'mm');
  z.setProperty('--hueco-h', n('et-hh', 0) + 'mm'); z.setProperty('--hueco-v', n('et-hv', 0) + 'mm');
  z.setProperty('--borde-print', $('#et-borde').checked ? '#c8ccd2' : 'transparent');
  return Math.max(1, Math.round(n('et-filas', 9)));
}
function etCodigo(v) {
  const tr = String(v || '').split(/[\/\s,;]+/).filter(Boolean);
  const ean = tr.find(t => /^\d{13}$/.test(t)); if (ean) return { valor: ean, tipo: 'EAN13' };
  const d12 = tr.find(t => /^\d{12}$/.test(t)); if (d12) return { valor: '0' + d12, tipo: 'EAN13' };
  return { valor: tr.sort((a, b) => b.length - a.length)[0] || '', tipo: 'CODE128' };
}
function etEanOk(c) { if (!/^\d{13}$/.test(c)) return false; let s = 0; for (let i = 0; i < 12; i++) s += +c[i] * (i % 2 ? 3 : 1); return (10 - s % 10) % 10 === +c[12]; }

function etEtiqueta(d) {
  const c = etCodigo(d.cod); let tipo = c.tipo; if (tipo === 'EAN13' && !etEanOk(c.valor)) tipo = 'CODE128';
  const esc = $('#et-esc').checked && d.escalados && d.escalados.length
    ? `<div class="esc">${d.escalados.map(x => `Desde ${x.desde % 1 ? x.desde : Math.round(x.desde)} ud: ${etPrecio(x.precio)}`).join(' · ')}</div>` : '';
  return `<div class="etq"><div class="fila-top"><div style="min-width:0"><div class="nombre">${escH(d.nombre)}</div>${d.ref ? `<div class="ref">Ref ${escH(d.ref)}</div>` : ''}</div>
    <div class="precio">${etPrecio(d.precio)}</div></div>${esc}
    <div class="fila-bot"><img class="logo" src="${ET_LOGO}" alt="logo"><div class="cod">${c.valor ? `<svg class="bc" data-cod="${escH(c.valor)}" data-tipo="${tipo}"></svg>` : ''}</div></div></div>`;
}

function etPinta() {
  const filas = etVars(), porHoja = 3 * filas;
  const todas = []; ET.forEach(d => { for (let i = 0; i < (+d.cant || 0); i++) todas.push(d); });
  const nH = Math.ceil(todas.length / porHoja);
  $('#et-info').textContent = ET.length ? `${ET.length} artículos · ${todas.length} etiquetas · ${nH} hoja(s)` : '';
  // lista con cantidades
  $('#et-lista').innerHTML = ET.length ? `<div class="et-lh"><span>Artículo</span><span>Ref.</span><span>PVP IVA incl.</span><span>Escalados</span><span>Etiquetas</span><span></span></div>` + ET.map((d, i) => `<div class="et-row">
      <span class="et-nom" title="${escH(d.nombre)}">${escH(d.nombre)}${d.recibido ? ` <small class="muted">(recibidas ${+d.recibido})</small>` : ''}${d.cod ? '' : ' <small class="et-warn">sin código de barras</small>'}</span>
      <span class="muted">${escH(d.ref)}</span><b>${etPrecio(d.precio)}</b>
      <small class="muted">${(d.escalados || []).map(x => `${Math.round(x.desde)}+: ${etPrecio(x.precio)}`).join(' · ')}</small>
      <input type="number" min="0" step="1" value="${d.cant}" data-i="${i}"><button class="lnk del" data-x="${i}">quitar</button></div>`).join('')
    : `<p class="empty">Pide arriba las etiquetas, busca artículos para añadirlos, o márcalos en Odoo (Inventario → Productos → ⚙ Acción → «🏷 Etiquetas en el Panel»).</p>`;
  $('#et-lista').querySelectorAll('input[data-i]').forEach(inp => inp.onchange = () => { ET[inp.dataset.i].cant = Math.max(0, parseInt(inp.value) || 0); etGuardar(); etPinta(); });
  $('#et-lista').querySelectorAll('[data-x]').forEach(b => b.onclick = () => { ET.splice(+b.dataset.x, 1); etGuardar(); etPinta(); });
  $('#et-recib').hidden = !ET.some(d => d.recibido);
  // hojas
  let h = '';
  for (let k = 0; k < nH; k++) {
    let celdas = '';
    for (let j = 0; j < porHoja; j++) { const d = todas[k * porHoja + j]; celdas += d ? etEtiqueta(d) : '<div class="etq vacia"></div>'; }
    h += `<div class="hoja"><div class="rejilla" style="grid-template-rows:repeat(${filas},var(--etq-alto))">${celdas}</div></div>`;
  }
  $('#et-zona').innerHTML = h;
  $('#et-zona').querySelectorAll('svg.bc').forEach(svg => {
    const cod = svg.dataset.cod, tipo = svg.dataset.tipo;
    try { JsBarcode(svg, cod, { format: tipo, displayValue: true, fontSize: 20, textMargin: 0, margin: 0, height: 55, width: tipo === 'EAN13' ? 1.6 : 1.4 }); }
    catch (e) { try { JsBarcode(svg, cod, { format: 'CODE128', displayValue: true, fontSize: 20, margin: 0, height: 55, width: 1.4 }); } catch (e2) { svg.outerHTML = '<span style="font-size:6pt;color:#c00">cód. no válido</span>'; } }
  });
}

$('#et-go').onclick = async () => {
  const t = $('#et-q').value.trim(); if (!t) return $('#et-q').focus();
  const b = $('#et-go'); b.disabled = true; b.textContent = 'Buscando…'; etAviso('Buscando en Odoo… (unos segundos)');
  try {
    const r = await jpost('/api/etiquetas/pedir', { texto: t });
    if (r.etiquetas.length) {
      const sust = !ET.length || confirm(`He encontrado ${r.etiquetas.length} artículos.\n\nAceptar = sustituir la lista actual\nCancelar = añadirlos a la lista`);
      etAnadir(r.etiquetas, sust);
    }
    etAviso((r.explicacion || '') + (r.etiquetas.length ? '' : '\nNo he sacado ningún artículo: prueba a decirlo de otra forma.'));
    etPinta();
  } catch (e) { etAviso(''); alertBox(e.message); }
  b.disabled = false; b.textContent = '🏷 Sacar etiquetas';
};
$('#et-q').onkeydown = e => { if (e.key === 'Enter') $('#et-go').click(); };

(() => {
  const inp = $('#et-bus'); let t, caja;
  inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
    caja?.remove(); const q = inp.value.trim(); if (q.length < 2) return;
    const r = await api(`/api/etiquetas/buscar?q=${encodeURIComponent(q)}`);
    caja = document.createElement('div'); caja.className = 'et-sug';
    caja.innerHTML = r.length ? r.map(p => `<a href="#" data-id="${p.id}">${escH(p.nombre)} <small class="muted">${escH(p.ref)} ${escH(p.cod)}</small></a>`).join('') : '<small class="muted">Nada</small>';
    inp.after(caja);
    caja.querySelectorAll('a').forEach(a => a.onclick = async e => { e.preventDefault(); caja.remove(); inp.value = '';
      const d = await jpost('/api/etiquetas/datos', { items: [{ id: +a.dataset.id, cant: 1 }] }); etAnadir(d); etPinta(); });
  }, 300); };
  inp.onkeydown = e => { if (e.key === 'Enter') { const a = caja?.querySelector('a'); if (a) a.click(); } if (e.key === 'Escape') caja?.remove(); };
})();
$('#et-todos-ok').onclick = () => { const n = Math.max(0, parseInt($('#et-todos').value) || 0); ET.forEach(d => d.cant = n); etGuardar(); etPinta(); };
$('#et-recib').onclick = () => { ET.forEach(d => { if (d.recibido) d.cant = Math.ceil(d.recibido); }); etGuardar(); etPinta(); };
$('#et-vaciar').onclick = () => { if (ET.length && !confirm('¿Vaciar la lista de etiquetas?')) return; ET = []; etGuardar(); etAviso(''); etPinta(); };
$('#et-aj-b').onclick = () => { $('#et-aj').hidden = !$('#et-aj').hidden; };
document.querySelectorAll('#et-aj input').forEach(i => i.oninput = i.onchange = () => { etGuardar(); etPinta(); });
$('#et-imp').onclick = () => { if (!ET.length) return alertBox('No hay etiquetas que imprimir'); window.print(); };
