/* ---------- Correo integrado ---------- */
let MA_E = 'pendientes', MA_L = [], MA_SEL = null, MA_T = null;
const MA_CLASE = { pedido: ['🛒 Pedido', 'b-ok'], presupuesto: ['📝 Presupuesto', 'b-warn'], factura_proveedor: ['🧾 Factura proveedor', ''], albaran_proveedor: ['📦 Albarán proveedor', ''],
  consulta: ['❓ Consulta', ''], reclamacion: ['⚠️ Reclamación', 'b-bad'], publicidad: ['Publicidad', ''], otro: ['Otro', ''] };
const MA_URG = { alta: ['Muy urgente', 'b-bad'], media: ['Normal', ''], baja: ['Sin prisa', ''] };
document.querySelectorAll('.ma-tabs button').forEach(b => b.onclick = () => { MA_E = b.dataset.e; document.querySelectorAll('.ma-tabs button').forEach(x => x.classList.toggle('on', x === b)); maOpen(); });
$('#ma-q').oninput = () => maLista();
async function maOpen() {
  clearTimeout(MA_T);
  try { MA_L = await api('/api/correo?estado=' + MA_E); } catch (e) { $('#ma-list').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  maLista();
  const nb = $('#nb-ma'); if (nb && MA_E === 'pendientes') { const n = MA_L.filter(m => m.clase === 'pedido' || m.urgencia === 'alta').length; nb.textContent = n; nb.hidden = !n; }
  if (MA_L.some(m => m.estado === 'clasificando') && !$('#v-ma').hidden) MA_T = setTimeout(maOpen, 8000);
}
function maLista() {
  const q = ($('#ma-q').value || '').toLowerCase();
  const l = MA_L.filter(m => !q || (m.nombre + m.de + m.asunto + (m.resumen || '')).toLowerCase().includes(q));
  $('#ma-list').innerHTML = l.length ? l.map(m => { const [ct, cc] = MA_CLASE[m.clase] || ['⏳ Clasificando', 'b-warn']; const [ut, uc] = MA_URG[m.urgencia] || ['', ''];
    return `<div class="ma-i ${m.id === MA_SEL ? 'on' : ''} u-${m.urgencia || ''}" data-id="${m.id}"><div class="ma-i1"><b>${escH(m.nombre || m.de)}</b><small>${hora(m.recibido)}</small></div>
      <div class="ma-i2">${escH(m.asunto || '(sin asunto)')}</div><div class="ma-i3">${m.estado === 'clasificando' ? '<span class="bdg b-warn">⏳ Leyendo</span>' : `<span class="bdg ${cc}">${ct}</span>`}
      ${ut && m.urgencia !== 'media' ? `<span class="bdg ${uc}">${ut}</span>` : ''}${m.adjuntos.length ? `<span class="bdg">📎 ${m.adjuntos.length}</span>` : ''}${m.pedido ? `<span class="bdg b-ok">${escH(m.pedido)}</span>` : ''}</div></div>`; }).join('')
    : `<p class="empty">${MA_E === 'pendientes' ? 'No hay correos por hacer. Llegan solos desde n8n.' : 'Nada todavía'}</p>`;
  $('#ma-list').querySelectorAll('.ma-i').forEach(d => d.onclick = () => maVer(d.dataset.id));
}
async function maVer(id) {
  MA_SEL = id; maLista();
  const c = $('#ma-c'); c.innerHTML = '<p class="muted">Cargando…</p>';
  let m; try { m = await api('/api/correo/' + id); } catch (e) { c.innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  const [ct, cc] = MA_CLASE[m.clase] || ['⏳ Clasificando', 'b-warn'];
  c.innerHTML = `<div class="ma-h"><h3>${escH(m.asunto || '(sin asunto)')}</h3><div class="muted">De <b>${escH(m.nombre)}</b> &lt;${escH(m.de)}&gt; · ${escH(m.fecha || '')} ${m.cuenta ? '· a ' + escH(m.cuenta) : ''}</div></div>
    <div class="ma-r"><span class="bdg ${cc}">${ct}</span> ${m.urgencia ? `<span class="bdg ${(MA_URG[m.urgencia] || [])[1] || ''}">${(MA_URG[m.urgencia] || [m.urgencia])[0]}</span>` : ''}
      ${m.resumen ? `<p><b>Qué pasa:</b> ${escH(m.resumen)}</p>` : ''}${m.que_hacer ? `<p><b>Qué hacer:</b> ${escH(m.que_hacer)}</p>` : ''}
      <div class="fa-bar">${['pedido', 'presupuesto', 'consulta', 'reclamacion', 'otro'].includes(m.clase) && !m.pedido ? `<button class="ac-go" id="ma-prep">🛒 Preparar pedido</button>` : ''}
        ${m.clase === 'presupuesto' ? `<a class="btn" href="/presupuestos" target="_blank">📝 Abrir Presupuestador</a>` : ''}
        ${m.pedido ? `<span class="bdg b-ok">Pedido ${escH(m.pedido)} creado</span>` : ''}
        ${m.estado === 'hecho' || m.estado === 'descartado' ? `<button class="lnk" data-st="nuevo">volver a "por hacer"</button>` : `<button class="btn" data-st="hecho">✓ Hecho</button><button class="lnk del" data-st="descartado">descartar</button>`}</div>
      ${m.adjuntos.length ? `<div class="ma-adj">${m.adjuntos.map(a => `<a target="_blank" rel="noopener" href="/api/correo/${m.id}/adjunto/${a.i}">📎 ${escH(a.nombre)}</a>${a.doc ? ' <span class="bdg b-ok">en Compras</span>' : ''}`).join(' ')}</div>` : ''}</div>
    <div class="ma-txt">${escH(m.texto || '').replace(/\n/g, '<br>')}</div><div id="ma-pp"></div>`;
  c.querySelectorAll('[data-st]').forEach(b => b.onclick = async () => { await jpost(`/api/correo/${m.id}/estado`, { estado: b.dataset.st }); MA_SEL = null; c.innerHTML = '<p class="empty">Hecho. Elige otro correo.</p>'; maOpen(); });
  const pb = $('#ma-prep'); if (pb) pb.onclick = () => maPreparar(m);
  if (m.datos && m.datos.lineas && !m.pedido) maPintaPedido(m, m.datos);
}
async function maPreparar(m, partner_id) {
  const box = $('#ma-pp'); box.innerHTML = '<p class="pc-aviso">Mirando el histórico del cliente y buscando los artículos… (unos segundos)</p>'; box.scrollIntoView({ behavior: 'smooth' });
  try { const d = await jpost(`/api/correo/${m.id}/preparar`, partner_id ? { partner_id } : {}); maPintaPedido(m, d); }
  catch (e) { box.innerHTML = `<p class="err">${escH(e.message)}</p>`; }
}
function maPintaPedido(m, d) {
  const box = $('#ma-pp'), L = d.lineas || [];
  const eu = v => v || v === 0 ? eur(+v) : '';
  const pinta = () => {
    box.innerHTML = `<div class="ma-pp"><h3>🛒 Preparar pedido</h3>
      <div class="pc-prov"><b>Cliente:</b> ${d.partner_id ? escH(d.cliente) : '<span class="err-t" style="display:inline">sin identificar</span>'}
        ${(d.clientes || []).length > 1 ? `<select id="mp-cli">${d.clientes.map(c => `<option value="${c.id}" ${c.id === d.partner_id ? 'selected' : ''}>${escH(c.nombre)}</option>`).join('')}</select>` : ''}
        <a href="#" id="mp-ccli">buscar otro</a><div id="mp-bcli" hidden><input placeholder="Nombre o CIF del cliente"><div></div></div></div>
      ${d.aviso ? `<p class="pc-aviso">${escH(d.aviso)}</p>` : ''}
      ${L.map((l, i) => `<div class="mp-l"><div class="mp-p"><b>Pide:</b> «${escH(l.pide)}» <label>Cant. <input type="number" min="0" step="any" data-c="${i}" value="${l.cantidad || 1}"></label>
          ${l.duda ? `<span class="pc-que" style="display:inline">· ${escH(l.duda)}</span>` : ''}</div>
        <div class="mp-ops">${(l.opciones || []).map(o => `<label class="mp-o ${l.elegido === o.product_id ? 'on' : ''}"><input type="radio" name="mp${i}" data-i="${i}" value="${o.product_id}" ${l.elegido === o.product_id ? 'checked' : ''}>
            <span><b>${escH(o.articulo)}</b><small>${escH(o.motivo)}${o.veces ? ` · comprado ${o.veces} veces, último ${coDate(o.ultima_fecha)} a ${eu(o.ultimo_precio)}` : ` · PVP ${eu(o.pvp)}`} · stock AC: ${o.stock_ac}</small></span></label>`).join('')}
          <label class="mp-o ${!l.elegido ? 'on' : ''}"><input type="radio" name="mp${i}" data-i="${i}" value="" ${!l.elegido ? 'checked' : ''}><span>No meter esta línea</span></label>
          <a href="#" data-b="${i}">🔎 buscar otro artículo</a><div class="pc-bus" data-bb="${i}" hidden><input placeholder="Nombre, referencia o EAN"><div></div></div></div></div>`).join('')}
      <p><a href="#" id="mp-add">＋ añadir otra línea</a></p>
      ${d.nota_cliente ? `<p><b>Nota del cliente:</b> ${escH(d.nota_cliente)}</p>` : ''}
      ${d.pregunta_al_cliente ? `<p class="pc-aviso"><b>Habría que preguntarle:</b> ${escH(d.pregunta_al_cliente)}</p>` : ''}
      ${(d.historial || []).length ? `<details><summary>Lo que suele comprar este cliente (${d.historial.length})</summary><table class="co-t pc-t"><tbody>${d.historial.map(h => `<tr><td>${escH(h.articulo)}</td><td>${h.veces} veces</td><td>${coDate(h.ultima_fecha)}</td><td>${eu(h.ultimo_precio)}</td></tr>`).join('')}</tbody></table></details>` : ''}
      <div class="fa-bar"><button class="ac-go" id="mp-go" ${d.partner_id ? '' : 'disabled'}>Crear pedido en borrador (Distriplus)</button><a href="#" id="mp-re">volver a interpretar</a></div><div id="mp-res"></div></div>`;
    const sc = $('#mp-cli'); if (sc) sc.onchange = () => maPreparar(m, +sc.value);
    $('#mp-re').onclick = e => { e.preventDefault(); maPreparar(m, d.partner_id); };
    $('#mp-ccli').onclick = e => { e.preventDefault(); const bx = $('#mp-bcli'); bx.hidden = false; const inp = bx.querySelector('input'); inp.focus(); let t;
      inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => { const r = await api('/api/correo/clientes/buscar?q=' + encodeURIComponent(inp.value));
        bx.querySelector('div').innerHTML = r.map(c => `<a href="#" class="pc-opt" data-id="${c.id}">${escH(c.nombre)}</a>`).join('');
        bx.querySelectorAll('[data-id]').forEach(a => a.onclick = ev => { ev.preventDefault(); maPreparar(m, +a.dataset.id); }); }, 300); }; };
    box.querySelectorAll('input[data-c]').forEach(x => x.onchange = () => L[x.dataset.c].cantidad = parseFloat(x.value) || 0);
    box.querySelectorAll('input[type=radio]').forEach(x => x.onchange = () => { L[x.dataset.i].elegido = x.value ? +x.value : null; pinta(); });
    box.querySelectorAll('[data-b]').forEach(a => a.onclick = e => { e.preventDefault(); const i = a.dataset.b, bx = box.querySelector(`[data-bb="${i}"]`); bx.hidden = false;
      const inp = bx.querySelector('input'); inp.focus(); let t;
      inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => { const r = await api('/api/correo/articulos/buscar?q=' + encodeURIComponent(inp.value));
        bx.querySelector('div').innerHTML = r.map(p => `<a href="#" class="pc-opt" data-id="${p.product_id}">${escH(p.articulo)} · ${eu(p.pvp)}</a>`).join('') || '<small class="muted">Nada</small>';
        bx.querySelectorAll('[data-id]').forEach(o => o.onclick = ev => { ev.preventDefault(); const p = r.find(x => x.product_id == o.dataset.id);
          L[i].opciones = [{ product_id: p.product_id, articulo: p.articulo, motivo: 'elegido a mano', pvp: p.pvp, stock_ac: '?' }, ...(L[i].opciones || []).filter(x => x.product_id !== p.product_id)];
          L[i].elegido = p.product_id; pinta(); }); }, 300); }; });
    $('#mp-add').onclick = e => { e.preventDefault(); L.push({ pide: '(añadida a mano)', cantidad: 1, opciones: [], elegido: null }); pinta(); box.querySelector(`[data-b="${L.length - 1}"]`).click(); };
    $('#mp-go').onclick = async () => {
      const lineas = L.filter(l => l.elegido && l.cantidad > 0).map(l => ({ product_id: l.elegido, cantidad: l.cantidad }));
      if (!lineas.length) return alertBox('Elige al menos un artículo');
      $('#mp-go').disabled = true;
      try { const r = await jpost(`/api/correo/${m.id}/pedido`, { partner_id: d.partner_id, lineas, nota: d.nota_cliente || '' });
        $('#mp-res').innerHTML = `<div class="ac-docs">✓ Pedido <b>${escH(r.pedido)}</b> creado en borrador (${eur(r.base)} sin IVA). <a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/sale.order/${r.id}">Abrir en Odoo</a> para revisarlo y confirmarlo.</div>`;
        maOpen();
      } catch (e) { alertBox(e.message); $('#mp-go').disabled = false; }
    };
  };
  pinta();
}
