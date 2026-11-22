/* ---------- Seguimiento: correo cruzado con Odoo ---------- */
let SG = null, SG_T = 'A', SG_TM = null;
const SG_TABS = [['R', '🔔 Recordatorios'], ['A', '💬 Piden precio, sin presupuesto'], ['C', '🛒 Piden material, sin pedido'], ['B', '📝 Presupuesto sin respuesta'], ['D', '📦 Pedido sin servir'], ['E', '🧾 Servido sin facturar']];
const SG_EST = { draft: 'Presupuesto', sent: 'Presupuesto enviado', sale: 'Pedido', cancel: 'Cancelado' };
const sgF = s => s ? s.slice(8, 10) + '/' + s.slice(5, 7) : '';
const sgHoy = (d = 0) => { const x = new Date(Date.now() + d * 864e5); return x.toISOString().slice(0, 10); };

async function sgBadge() {
  try { const r = await api('/api/seg/aviso'); const nb = $('#nb-sg'); if (nb) { nb.textContent = r.n; nb.hidden = !r.n; } } catch (e) { }
}
async function sgOpen(recalcular) {
  clearTimeout(SG_TM);
  try { SG = await api('/api/seg' + (recalcular ? '?recalcular=1' : '')); } catch (e) { $('#sg-l').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  if (!sgOpen.ya && SG.items.length) { sgOpen.ya = true; if (!sgDe(SG_T).length) { const t = SG_TABS.find(([k]) => sgDe(k).length); if (t) SG_T = t[0]; } }
  sgEstado(); sgTabs(); sgLista(); sgBadge();
  if (!SG.cuentas.length && $('#sg-ctas').hidden) { $('#sg-ctas').hidden = false; sgCuentas(); }
  if ((SG.calculando || SG.lectura.leyendo) && !$('#v-sg').hidden) SG_TM = setTimeout(() => sgOpen(), 6000);
}
function sgEstado() {
  const L = SG.lectura, e = $('#sg-est');
  let h = '';
  if (!SG.cuentas.length) h = `<p class="pc-aviso">Para cruzar el correo añade las cuentas aquí abajo, en «Cuentas de correo». Mientras tanto aquí sale lo que dice Odoo: presupuestos sin respuesta, pedidos sin servir y servido sin facturar.</p>`;
  else h = `<b>${SG.correos.toLocaleString('es-ES')}</b> correos leídos de ${SG.cuentas.length} cuentas${SG.desde ? ' desde el ' + fdate(SG.desde) : ''}`;
  if (L.leyendo) {
    const c = Object.entries(L.cuentas).map(([k, v]) => v.error ? `🔴 ${escH(k)}: ${escH(v.error)}` : `${escH(k)} ${v.leidos}/${v.total}`).join(' · ');
    h += ` · <span class="bdg b-warn">⏳ ${escH(L.fase)}${L.por_clasificar && L.fase.startsWith('Clasif') ? ` ${L.clasificados}/${L.por_clasificar}` : ''}</span><br><small>${c}</small>`;
  } else if (L.error) h += ` · <span class="bdg b-bad">${escH(L.error)}</span>`;
  if (SG.calculando) h += ' · <span class="bdg b-warn">⏳ cruzando con Odoo…</span>';
  else if (SG.calculado) h += ` · calculado a las ${new Date(SG.calculado * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}`;
  if (SG.error) h += ` · <span class="bdg b-bad">${escH(SG.error)}</span>`;
  e.innerHTML = h;
  $('#sg-leer').hidden = !SG.cuentas.length;
}
function sgDe(t) {
  const dorm = $('#sg-dorm').checked;
  if (t === 'R') return SG.avisos;
  return SG.items.filter(i => i.tipo === t && (dorm || !i.dormido));
}
function sgTabs() {
  $('#sg-tabs').innerHTML = SG_TABS.map(([k, n]) => { const l = sgDe(k); const imp = ['B', 'D', 'E'].includes(k) ? l.reduce((a, i) => a + (i.so ? i.so.base : 0), 0) : 0;
    return `<button data-t="${k}" class="${k === SG_T ? 'on' : ''}">${n} <b>${l.length}</b>${imp ? ` <small>${eur(imp)}</small>` : ''}</button>`; }).join('');
  $('#sg-tabs').querySelectorAll('button').forEach(b => b.onclick = () => { SG_T = b.dataset.t; sgTabs(); sgLista(); });
}
function sgLista() {
  const q = ($('#sg-q').value || '').toLowerCase(), oc = $('#sg-cont').checked;
  let l = sgDe(SG_T).filter(i => (!q || [i.cliente, i.asunto, i.que, i.de, i.nombre, i.so && i.so.ref, i.so && i.so.su_ref].join(' ').toLowerCase().includes(q)) && !(oc && i.contestado));
  l = l.slice().sort((a, b) => (a.contestado - b.contestado) || (b.fecha > a.fecha ? 1 : -1));
  const box = $('#sg-l');
  if (!l.length) { box.innerHTML = `<p class="empty">${SG.calculando ? 'Calculando…' : 'Nada aquí 👍'}</p>`; sgSel(); return; }
  box.innerHTML = `<p class="muted sg-ayuda">${SG_T === 'R' ? 'Lo que dejaste para hoy o días anteriores.' : escH(SG.tipos[SG_T] || '')}. Marca <b>Hecho</b> lo resuelto, <b>Descartar</b> lo que no va a salir y <b>Recordar</b> para que vuelva a aparecer un día.</p>`
    + l.map(i => { const s = i.so; const m = i.marca;
      return `<div class="sg-i" data-k="${escH(i.clave)}"><label class="sg-ck"><input type="checkbox" data-k="${escH(i.clave)}"></label>
        <div class="sg-c"><div class="sg-1"><b>${escH(i.cliente || i.nombre || i.de)}</b><span class="muted">${sgF(i.fecha)} · hace ${i.dias} d</span>
          ${s ? `<a href="#" class="bdg b-prov sg-so">${escH(s.ref)} · ${escH(SG_EST[s.estado] || s.estado)}${s.estado === 'sale' && s.entrega === 'partial' ? ' (servido en parte)' : ''}</a> <span class="bdg">${escH(s.empresa)}</span> <b>${eur(s.base)}</b>` : ''}
          ${i.contestado ? '<span class="bdg b-ok">contestado por correo</span>' : (i.hilo ? '<span class="bdg b-warn">sin contestar</span>' : '')}
          ${i.sin_correo ? '<span class="bdg">solo en Odoo</span>' : ''}${m && m.estado === 'recordar' ? `<span class="bdg b-warn">⏰ ${fdate(m.recordar)}</span>` : ''}</div>
          ${i.asunto || i.que ? `<div class="sg-2">${i.asunto ? `<b>${escH(i.asunto)}</b>` : ''}${i.que ? ` — ${escH(i.que)}` : ''}${i.cuenta ? ` <small class="muted">(${escH(i.cuenta.split('@')[0])} @${escH(i.cuenta.split('@')[1] || '')})</small>` : ''}</div>` : ''}
          ${m && m.nota ? `<div class="sg-nota">📝 ${escH(m.nota)}</div>` : ''}
          <div class="sg-3">${i.hilo ? `<button class="lnk" data-a="ver">✉️ Ver correo (${i.n})</button>` : ''}<button class="lnk" data-a="hecho">✓ Hecho</button>
            <button class="lnk" data-a="rec">⏰ Recordar</button><button class="lnk del" data-a="descartado">Descartar</button>${m ? '<button class="lnk" data-a="abierto">quitar marca</button>' : ''}</div>
          <div class="sg-x"></div></div></div>`; }).join('');
  box.querySelectorAll('.sg-i').forEach(d => {
    const it = l.find(x => x.clave === d.dataset.k);
    d.querySelectorAll('[data-a]').forEach(b => b.onclick = () => sgAccion(it, b.dataset.a, d));
    const so = d.querySelector('.sg-so'); if (so) so.onclick = e => { e.preventDefault(); const s = it.so; openDetail({ id: s.id, model: 'sale.order', ref: s.ref, cliente: s.cliente, empresa: s.empresa, fecha: s.fecha, total: s.total, cid: s.cid, estado: s.estado === 'sale' ? 'pedido' : 'presupuesto', su_ref: s.su_ref }); };
    d.querySelector('input[type=checkbox]').onchange = sgSel;
  });
  sgSel();
}
function sgSel() { const n = $('#sg-l').querySelectorAll('.sg-ck input:checked').length; $('#sg-seln').textContent = n; $('#sg-sel').hidden = !n; }
async function sgMarca(claves, estado, extra = {}) {
  await jpost('/api/seg/marca', { claves, estado, ...extra });
  SG = await api('/api/seg'); sgTabs(); sgLista(); sgBadge();
}
async function sgAccion(it, a, d) {
  const x = d.querySelector('.sg-x');
  if (a === 'ver') {
    if (x.innerHTML) { x.innerHTML = ''; return; }
    x.innerHTML = '<p class="muted">Cargando…</p>';
    try { const L = await api('/api/seg/hilo/' + encodeURIComponent(it.hilo));
      x.innerHTML = L.map(m => `<div class="sg-m ${m.enviado ? 'env' : ''}"><div class="muted"><b>${m.enviado ? '➡️ Nosotros' : escH(m.nombre || m.de)}</b> · ${escH(m.fecha)}${m.clase ? ` · ${escH(m.clase)}` : ''}${m.adjuntos.length ? ` · 📎 ${m.adjuntos.map(escH).join(', ')}` : ''}</div>
        <div><b>${escH(m.asunto)}</b></div><div class="sg-t">${escH((m.texto || '').split('[mensaje citado]')[0]).replace(/\n/g, '<br>')}</div></div>`).join('') || '<p class="muted">No se encuentra el correo</p>';
    } catch (e) { x.innerHTML = `<p class="err">${escH(e.message)}</p>`; }
    return;
  }
  if (a === 'rec') {
    x.innerHTML = `<div class="sg-rec"><label>Recordar el <input type="date" value="${sgHoy(3)}"></label>${[1, 3, 7, 15].map(n => `<button class="lnk" data-n="${n}">+${n} d</button>`).join('')}
      <input class="sg-nt" placeholder="Nota (qué falta, a quién llamar…)" value="${escH((it.marca && it.marca.nota) || '')}"><button class="ac-go">Guardar</button></div>`;
    const di = x.querySelector('input[type=date]');
    x.querySelectorAll('[data-n]').forEach(b => b.onclick = () => di.value = sgHoy(+b.dataset.n));
    x.querySelector('.ac-go').onclick = () => sgMarca([it.clave], 'recordar', { recordar: di.value, nota: x.querySelector('.sg-nt').value });
    return;
  }
  try { await sgMarca([it.clave], a); } catch (e) { alertBox(e.message); }
}
$('#sg-q').oninput = () => sgLista();
$('#sg-cont').onchange = () => sgLista();
$('#sg-dorm').onchange = () => { sgTabs(); sgLista(); };
$('#sg-rec').onclick = () => sgOpen(true);
document.querySelectorAll('#sg-sel [data-bulk]').forEach(b => b.onclick = async () => {
  const ks = [...$('#sg-l').querySelectorAll('.sg-ck input:checked')].map(c => c.dataset.k);
  if (!ks.length) return;
  if (!confirm(`¿Marcar ${ks.length} como «${b.dataset.bulk}»?`)) return;
  try { await sgMarca(ks, b.dataset.bulk); } catch (e) { alertBox(e.message); }
});
$('#sg-leer').onclick = async () => {
  const d = prompt('¿Desde qué fecha leo el correo? (AAAA-MM-DD). Lee entrada y enviados de todas las cuentas; la primera vez tarda un rato.', SG.desde || '2026-01-01');
  if (!d) return;
  try { await jpost('/api/seg/leer', { desde: d }); sgOpen(); } catch (e) { alertBox(e.message); }
};
/* cuentas de correo */
const SG_SPARK = ['foldercastellon.es', 'regalosfolder.es', 'distripluslevante.es', 'foldercs.es'].map(d => 'info' + String.fromCharCode(64) + d);
async function sgCuentas() {
  const L = await api('/api/seg/cuentas').catch(() => []);
  $('#sg-cl').innerHTML = L.length ? L.map(c => `<div class="sg-cta">🟢 <b>${escH(c.correo)}</b> <span class="muted">${escH(c.servidor)}</span> <button class="lnk del" data-q="${escH(c.correo)}">quitar</button></div>`).join('')
    : '<p class="muted">Todavía no hay ninguna. Añádelas una a una:</p>';
  const faltan = SG_SPARK.filter(x => !L.some(c => c.correo === x));
  $('#sg-dl').innerHTML = faltan.map(x => `<option value="${x}">`).join('');
  if (faltan.length && !$('#sg-cm').value) $('#sg-cm').value = faltan[0];
  $('#sg-cl').querySelectorAll('[data-q]').forEach(b => b.onclick = async () => { if (!confirm('¿Dejar de leer ' + b.dataset.q + '?')) return; await jpost('/api/seg/cuentas/quitar', { correo: b.dataset.q }); sgCuentas(); });
  return L;
}
$('#sg-cbtn').onclick = () => { const p = $('#sg-ctas'); p.hidden = !p.hidden; if (!p.hidden) sgCuentas(); };
$('#sg-cf').onsubmit = async e => {
  e.preventDefault(); const r = $('#sg-cr'); r.textContent = '⏳ Probando…'; r.className = 'muted';
  try { const x = await jpost('/api/seg/cuentas', { correo: $('#sg-cm').value, clave: $('#sg-cp').value, servidor: $('#sg-cs').value });
    r.textContent = `✅ ${x.correo} conectada (${x.proveedor || x.servidor}, ${x.bandeja} correos en la bandeja)`; r.className = 'ok-t';
    $('#sg-cp').value = ''; $('#sg-cm').value = ''; $('#sg-cs').value = ''; await sgCuentas(); sgOpen(); }
  catch (er) { r.textContent = '❌ ' + er.message; r.className = 'err-t'; $('#sg-cp').value = ''; }
};
