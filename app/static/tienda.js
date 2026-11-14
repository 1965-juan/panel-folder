/* ---------- Mi tienda: reposiciones pedidas a almacén y encargos de clientes ---------- */
let TI = null;
async function tiOpen() {
  $('#ti-rep').innerHTML = $('#ti-enc').innerHTML = '<p class="muted">Cargando…</p>';
  try { TI = await api(`/api/tienda/datos?t=${encodeURIComponent($('#ti-sel').value || '')}`); }
  catch (e) { $('#ti-rep').innerHTML = `<p class="err">${escH(e.message)}</p>`; $('#ti-enc').innerHTML = ''; return; }
  const sel = $('#ti-sel');
  sel.hidden = TI.fija;
  if (!TI.fija && !sel.options.length) { sel.innerHTML = TI.tiendas.map(t => `<option value="${t.codigo}">${escH(t.nombre)}</option>`).join(''); sel.value = TI.tienda; }
  $('#ti-tpv').href = TI.tpv; $('#ti-tpv').textContent = `🧾 Abrir el TPV de ${TI.nombre}`;
  $('#h1').textContent = TI.nombre;
  const tabla = ls => `<table class="co-t ti-t"><thead><tr><th>Artículo</th><th class="num">Pedido</th><th class="num">Servido</th><th class="num">Falta</th></tr></thead><tbody>
    ${ls.map(l => `<tr><td>${escH(l.articulo)}${l.estado ? ` <span class="bdg">${escH(l.estado)}</span>` : ''}</td><td class="num">${+l.pedido}</td><td class="num">${+l.servido}</td><td class="num"><b>${+l.pendiente}</b></td></tr>`).join('')}</tbody></table>`;
  const rep = TI.reposiciones.filter(r => !r.error);
  $('#ti-rep').innerHTML = (TI.reposiciones.find(r => r.error) ? `<p class="err">${escH(TI.reposiciones.find(r => r.error).error)}</p>` : '') +
    (rep.length ? rep.map(r => `<div class="ti-card"><div class="ti-h"><b>${escH(r.ref)}</b><span class="muted">${escH(r.fecha)}</span></div>${tabla(r.lineas)}</div>`).join('')
      : '<p class="empty">No tenéis reposiciones pendientes</p>');
  $('#ti-enc').innerHTML = TI.encargos.length ? TI.encargos.map(o => `<div class="ti-card"><div class="ti-h"><b>${escH(o.ref)}</b> ${escH(o.cliente)}${o.ref_cliente ? ` · <i>${escH(o.ref_cliente)}</i>` : ''}
      ${o.contado ? '<span class="bdg b-warn">💶 Contado: cobrar al entregar</span>' : `<span class="bdg">${escH(o.plazo)}: solo servir</span>`}
      <span class="muted">${coDate(o.fecha)} · ${eur(o.total)}</span></div>${tabla(o.lineas)}</div>`).join('') : '<p class="empty">No hay encargos pendientes de servir</p>';
  const cob = TI.por_cobrar.filter(o => o.contado);
  $('#ti-cob').innerHTML = cob.length ? `<table class="co-t ti-t"><tbody>${cob.map(o => `<tr><td><b>${escH(o.ref)}</b> ${escH(o.cliente)}${o.ref_cliente ? ` · <i>${escH(o.ref_cliente)}</i>` : ''}
      <br><small class="muted">${coDate(o.fecha)}${o.factura ? ' · ' + escH(o.factura) : ''}</small></td><td class="num"><b>${eur(o.importe)}</b></td>
      <td class="ti-cb"><button class="ac-go" data-c="${o.id}">💶 Cobrar en el TPV</button></td></tr>`).join('')}</tbody></table>`
    : '<p class="empty">No hay nada servido pendiente de cobrar</p>';
  $('#ti-cob').querySelectorAll('[data-c]').forEach(b => b.onclick = async () => {
    const o = cob.find(x => x.id === +b.dataset.c);
    b.disabled = true; b.textContent = 'Preparando…';
    try { const r = await jpost('/api/tienda/preparar_cobro', { so_id: o.id, t: TI.tienda });
      $('#ti-paso').hidden = false;
      $('#ti-paso').innerHTML = `<b>Cobra ${eur(r.importe)} a ${escH(r.cliente)} en el TPV:</b>
        <ol><li>Abre el TPV (botón de arriba).</li><li>Pulsa <b>Cliente</b> y elige <b>${escH(r.cliente)}</b>.</li>
        <li>En la ficha del cliente pulsa <b>Liquidar deuda</b> (o «Settle due») y marca ${r.facturas.length > 1 ? 'las facturas' : 'la factura'} <b>${escH(r.facturas.join(', '))}</b>.</li>
        <li>Cobra en <b>Efectivo</b> o <b>Tarjeta</b>. El dinero entra en la caja del TPV y el material no se vuelve a descontar.</li></ol>
        <button class="btn" onclick="this.parentElement.hidden=true">Entendido</button>`;
      $('#ti-paso').scrollIntoView({ behavior: 'smooth' });
    } catch (e) { alertBox(e.message); }
    b.disabled = false; b.textContent = '💶 Cobrar en el TPV';
  });
  tiRepo();
  $('#ti-info').textContent = `${rep.length} reposiciones · ${TI.encargos.length} encargos pendientes`;
}
async function tiRepo() {
  let R; try { R = await api(`/api/tienda/repo?t=${TI ? TI.tienda : ''}`); } catch (e) { $('#ti-borr').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  if (!R.puede) { $('#ti-borr').innerHTML = '<p class="muted">La reposición de esta tienda de momento se sigue pidiendo desde su TPV.</p>'; $('#ti-q').disabled = true; return; }
  $('#ti-q').disabled = false;
  $('#ti-borr').innerHTML = (R.lineas.length ? `<table class="co-t ti-t"><thead><tr><th>Artículo</th><th class="num">Hay en almacén</th><th class="num">Hay en tienda</th><th class="num">Pedir</th><th></th></tr></thead><tbody>
    ${R.lineas.map(l => `<tr><td>${escH(l.nombre)}</td><td class="num ${l.stock_ac < l.cant ? 'ti-poco' : ''}">${+(l.stock_ac ?? 0)}</td><td class="num">${+(l.stock_tienda ?? 0)}</td>
      <td class="num"><input type="number" min="0" step="1" value="${+l.cant}" data-q="${l.id}"></td><td><button class="tb-x" data-x="${l.id}" title="Quitar">✕</button></td></tr>`).join('')}</tbody></table>
    <div class="ti-lz"><button class="ac-go" id="ti-lanzar">🚚 Lanzar reposición a almacén (${R.lineas.length} artículos)</button><button class="lnk del" id="ti-vac">vaciar</button></div>`
    : '<p class="empty">No hay nada en preparación. Busca arriba y añade lo que necesitéis.</p>')
    + (R.lanzadas.length ? `<p class="muted ti-ult">Últimas lanzadas desde el panel: ${R.lanzadas.map(x => `${escH(x.picking)} (${x.lineas} art., ${new Date(x.cuando * 1000).toLocaleDateString('es-ES')})`).join(' · ')}</p>` : '');
  const post = b => jpost('/api/tienda/repo', { t: TI.tienda, ...b }).then(tiRepo).catch(e => alertBox(e.message));
  $('#ti-borr').querySelectorAll('[data-q]').forEach(i => i.onchange = () => post({ accion: 'cantidad', id: +i.dataset.q, cant: i.value }));
  $('#ti-borr').querySelectorAll('[data-x]').forEach(b => b.onclick = () => post({ accion: 'quitar', id: +b.dataset.x }));
  const vac = $('#ti-vac'); if (vac) vac.onclick = () => confirm('¿Vaciar la reposición en preparación?') && post({ accion: 'vaciar' });
  const lz = $('#ti-lanzar'); if (lz) lz.onclick = async () => {
    if (!confirm(`¿Lanzar ya la reposición al almacén central con ${R.lineas.length} artículos?`)) return;
    lz.disabled = true; lz.textContent = 'Lanzando…';
    try { const r = await jpost('/api/tienda/repo/lanzar', { t: TI.tienda }); alertBox(`Reposición ${r.picking} enviada al almacén (${r.lineas} artículos)`); tiOpen(); }
    catch (e) { alertBox(e.message); lz.disabled = false; }
  };
}
(() => {
  const inp = $('#ti-q'), box = $('#ti-sug'); let tm;
  const pinta = r => { box.hidden = false; box.innerHTML = r.length ? r.map(p => `<a href="#" data-id="${p.id}"><b>${escH(p.nombre)}</b> <small class="muted">${escH(p.ean)} · almacén ${+p.stock_ac} · tienda ${+p.stock_tienda}</small></a>`).join('') : '<small class="muted">Nada</small>';
    box.querySelectorAll('a').forEach(a => a.onclick = async e => { e.preventDefault(); const p = r.find(x => x.id === +a.dataset.id);
      const q = prompt(`¿Cuántas unidades de ${p.nombre}?`, '1'); if (q === null) return;
      box.hidden = true; inp.value = '';
      await jpost('/api/tienda/repo', { t: TI.tienda, accion: 'anadir', product_id: p.id, nombre: p.nombre, ref: p.ref, cant: q }).catch(er => alertBox(er.message)); tiRepo(); inp.focus(); }); };
  inp.oninput = () => { clearTimeout(tm); tm = setTimeout(async () => { const q = inp.value.trim(); if (q.length < 2) { box.hidden = true; return; }
    try { pinta(await api(`/api/tienda/buscar?t=${TI.tienda}&q=${encodeURIComponent(q)}`)); } catch (e) { } }, 300); };
  inp.onkeydown = e => { if (e.key === 'Enter') { const a = box.querySelector('a'); if (a) a.click(); } if (e.key === 'Escape') box.hidden = true; };
})();
$('#ti-sel').onchange = () => tiOpen();
$('#ti-rec').onclick = () => tiOpen();
