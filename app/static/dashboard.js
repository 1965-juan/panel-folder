/* ---------- Dashboard: la situación de las empresas de un vistazo (solo administrador) ---------- */
let DB = null, DB_E = 'all';
try { DB_E = localStorage.getItem('pf_db_emp') || 'all'; } catch (e) {}
const DB_COL = { 1: '#3A1F73', 4: '#F39A2B', 5: '#1F7A4D', 2: '#6A3FB5', 3: '#9C8FB8' };

async function dbOpen(forzar) {
  if (!DB) $('#db').innerHTML = '<p class="muted">Leyendo los datos de Odoo (la primera vez tarda un poco)…</p>';
  try { DB = await api('/api/dashboard' + (forzar ? '?refrescar_ya=1' : '')); }
  catch (e) { $('#db').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  dbPinta();
  if (DB.calculando) setTimeout(() => { if (location.hash.startsWith('#dashboard')) dbOpen(); }, 20000);
}

function dbPinta() {
  const D = DB, sel = DB_E === 'all' ? [1, 2, 3, 4, 5] : [+DB_E], en = x => sel.includes(x.empresa);
  const sum = (l, f = x => x.saldo) => (l || []).filter(en).reduce((a, x) => a + (f(x) || 0), 0);
  const T = D.tesoreria || {}, V = D.ventas || [], S = D.stock || [], EE = D.entre || { cuentas: [], socios: [] };
  const bancos = sum(T.bancos), cajT = sum(T.cajas_tiendas), cajG = sum(T.cajas_generales), stock = sum(S, x => x.valor);
  const cobrar = sum(V, x => x.facturas), albar = sum(V, x => x.albaranes), pedidos = sum(V, x => x.pedidos), vencido = sum(V, x => x.vencido);
  const entre = DB_E === 'all' ? 0 : EE.cuentas.filter(c => c.libro === +DB_E).reduce((a, c) => a + c.saldo, 0);
  const aPagar = 0;   // hueco reservado: facturas de proveedor pendientes (todavía sin datos)
  const partes = [['Bancos', bancos, '#3A1F73'], ['Cajas', cajT + cajG, '#6A3FB5'], ['Stock (a coste)', stock, '#F39A2B'],
    ['Facturas por cobrar', cobrar, '#1F7A4D'], ['Albaranes por facturar', albar, '#5FA77F']];
  if (DB_E !== 'all') partes.push(['Saldo con otras empresas del grupo', entre, '#9C8FB8']);
  const liquidez = partes.reduce((a, p) => a + p[1], 0) - aPagar;
  const pos = partes.filter(p => p[1] > 0), totPos = pos.reduce((a, p) => a + p[1], 0) || 1;
  const hora = D.hecho ? new Date(D.hecho * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' }) : '';
  const nomE = e => (D.empresas || {})[e] || e;
  const chip = (v, t) => `<button class="${DB_E === String(v) ? 'on' : ''}" data-e="${v}">${t}</button>`;
  const barras = (l, max) => l.map(([t, v, c, sub]) => `<div class="db-bar"><div class="db-bl"><span>${escH(t)}</span><b class="${v < 0 ? 'neg' : ''}">${eur(v)}</b></div>
      <div class="db-bt"><i style="width:${max ? Math.max(1, Math.abs(v) / max * 100) : 0}%;background:${v < 0 ? '#C8102E' : c}"></i></div>${sub ? `<small class="muted">${sub}</small>` : ''}</div>`).join('');
  const dias = f => f ? Math.round((Date.now() - new Date(f + 'T12:00:00')) / 864e5) : null;

  // stock
  const st = S.filter(en).filter(x => x.valor || x.uds), stMax = Math.max(...st.map(x => x.valor), 1);
  // bancos por empresa
  const bk = (T.bancos || []).filter(en).sort((a, b) => a.empresa - b.empresa || b.saldo - a.saldo), bkMax = Math.max(...bk.map(x => Math.abs(x.saldo)), 1);
  // entre empresas
  const ec = EE.cuentas.filter(c => DB_E === 'all' || c.libro === +DB_E || c.otra === +DB_E);
  const so = EE.socios.filter(en);
  const vSel = V.filter(en);

  $('#db').innerHTML = `
  <div class="db-top"><div class="wa-tabs db-emp">${chip('all', 'Todas')}${[1, 4, 5, 2, 3].map(e => chip(e, nomE(e))).join('')}</div>
    <span class="muted">Datos de las ${hora}${D.calculando ? ' · actualizando…' : ''}</span><button class="btn" id="db-rf">↻ Actualizar ahora</button></div>
  ${(D.errores || []).length ? `<p class="err">No se ha podido leer: ${D.errores.map(escH).join(' · ')}</p>` : ''}

  <section class="db-liq ${liquidez < 0 ? 'mal' : ''}">
    <div><span>Liquidez total ${DB_E === 'all' ? 'del grupo' : '· ' + escH(nomE(+DB_E))}</span><b>${eur(liquidez)}</b>
      <small>Todo lo realizable (bancos, cajas, stock, facturas por cobrar y albaranes por facturar) menos lo que hay que pagar</small></div>
    <div class="db-stack">${pos.map(p => `<i title="${p[0]}: ${eur(p[1])}" style="width:${p[1] / totPos * 100}%;background:${p[2]}"></i>`).join('')}</div>
    <div class="db-ley">${partes.map(p => `<span><i style="background:${p[2]}"></i>${p[0]} <b class="${p[1] < 0 ? 'neg' : ''}">${eur(p[1])}</b></span>`).join('')}
      <span><i style="background:#C8102E"></i>A pagar <b>— pendiente</b></span></div>
  </section>

  <section class="kpis db-k">
    <div class="kpi k-ped"><span>Bancos</span><b>${eur(bancos)}</b><small>${bk.length} cuentas</small></div>
    <div class="kpi k-alb"><span>Cajas</span><b>${eur(cajT + cajG)}</b><small>tiendas ${eur(cajT)} · generales ${eur(cajG)}</small></div>
    <div class="kpi k-pre"><span>Stock a coste</span><b>${eur(stock)}</b><small>${st.length} ubicaciones</small></div>
    <div class="kpi" style="background:#1F7A4D"><span>Facturas por cobrar</span><b>${eur(cobrar)}</b><small>${vencido ? 'vencido ' + eur(vencido) : 'nada vencido'}</small></div>
  </section>

  <div class="db-grid">
    <section class="db-c"><h3>📦 Stock (valor a precio de coste)</h3>
      ${st.length ? barras(st.map(x => [x.nombre, x.valor, DB_COL[x.empresa], `${(x.uds || 0).toLocaleString('es-ES')} uds · ${x.refs} artículos${x.sin_coste ? ` · ${x.sin_coste} sin coste en Odoo` : ''}`]), stMax) : '<p class="empty">Sin stock en esta empresa</p>'}
      <p class="db-tot">Total <b>${eur(stock)}</b></p></section>

    <section class="db-c"><h3>🧾 Ventas (como en el panel de Ventas)</h3>
      <div class="db-3">
        <div><span>Pedidos</span><b>${eur(pedidos)}</b><small>${vSel.reduce((a, x) => a + x.pedidos_n, 0)} pedidos</small></div>
        <div><span>Albaranes por facturar</span><b>${eur(albar)}</b><small>${vSel.reduce((a, x) => a + x.albaranes_n, 0)} albaranes</small></div>
        <div><span>Facturas por cobrar</span><b>${eur(cobrar)}</b><small>${vSel.reduce((a, x) => a + x.facturas_n, 0)} facturas${vencido ? ` · <em class="neg">${eur(vencido)} vencido</em>` : ''}</small></div>
      </div>
      ${DB_E === 'all' ? `<table class="co-t db-t"><thead><tr><th>Empresa</th><th class="num">Pedidos</th><th class="num">Albaranes</th><th class="num">Por cobrar</th></tr></thead><tbody>
        ${V.filter(x => x.pedidos || x.albaranes || x.facturas).map(x => `<tr><td><span class="db-dot" style="background:${DB_COL[x.empresa]}"></span>${escH(nomE(x.empresa))}</td><td class="num">${eur(x.pedidos)}</td><td class="num">${eur(x.albaranes)}</td><td class="num">${eur(x.facturas)}</td></tr>`).join('')}</tbody></table>` : ''}
      <p class="muted db-nota">Importes con IVA. No se cuentan las ventas entre empresas del grupo.</p></section>

    <section class="db-c"><h3>💶 Cajas</h3>
      <h4>Tiendas</h4>
      <table class="co-t db-t"><thead><tr><th>Caja</th><th class="num">Saldo en Odoo</th><th>Último cierre</th></tr></thead><tbody>
      ${(T.cajas_tiendas || []).filter(en).map(x => `<tr><td><b>${escH(x.tpv || x.nombre)}</b> <span class="bdg ${x.abierta ? 'b-ok' : ''}">${x.abierta ? 'abierta' : 'cerrada'}</span></td>
        <td class="num ${x.saldo < 0 ? 'neg' : ''}"><b>${eur(x.saldo)}</b></td><td>${x.ultimo_cierre ? coDate(x.ultimo_cierre.slice(0, 10)) + ' ' + x.ultimo_cierre.slice(11) : '—'}${x.contado != null ? `<br><small class="muted">contado ${eur(x.contado)}</small>` : ''}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Sin cajas de tienda</td></tr>'}</tbody></table>
      <h4>Caja general de cada empresa</h4>
      ${barras((T.cajas_generales || []).filter(en).map(x => [nomE(x.empresa) + ' · ' + x.nombre, x.saldo, DB_COL[x.empresa]]), Math.max(...(T.cajas_generales || []).filter(en).map(x => Math.abs(x.saldo)), 1)) || '<p class="empty">Sin caja general</p>'}</section>

    <section class="db-c"><h3>🏦 Bancos</h3>
      ${bk.length ? bk.map(x => { const d = dias(x.ultimo); return `<div class="db-bar"><div class="db-bl"><span><span class="db-dot" style="background:${DB_COL[x.empresa]}"></span>${escH(nomE(x.empresa))} · <b>${escH(x.nombre)}</b>
          ${x.iban ? `<small class="muted"> ···${escH(x.iban.replace(/\s/g, '').slice(-4))}</small>` : ''}</span><b class="${x.saldo < 0 ? 'neg' : ''}">${eur(x.saldo)}</b></div>
        <div class="db-bt"><i style="width:${Math.max(1, Math.abs(x.saldo) / bkMax * 100)}%;background:${x.saldo < 0 ? '#C8102E' : DB_COL[x.empresa]}"></i></div>
        <small class="${d != null && d > 7 ? 'neg' : 'muted'}">${x.ultimo ? `último movimiento ${coDate(x.ultimo)}${d > 7 ? ` (hace ${d} días)` : ''}` : 'sin movimientos en Odoo'}</small></div>`; }).join('') : '<p class="empty">Sin cuentas de banco</p>'}
      <p class="db-tot">Total bancos <b>${eur(bancos)}</b></p>
      ${(T.bancos_otros || []).filter(en).length ? `<details class="db-det"><summary>Cuentas técnicas (tarjeteros, gastos…): no cuentan en la liquidez</summary>
        ${(T.bancos_otros || []).filter(en).map(x => `<div class="db-row"><span>${escH(nomE(x.empresa))} · ${escH(x.nombre)}</span><b>${eur(x.saldo)}</b></div>`).join('')}</details>` : ''}</section>

    <section class="db-c"><h3>🔁 Cuentas entre empresas</h3>
      ${(() => { // una fila por pareja de empresas, con lo que dice la contabilidad de cada una (deberían coincidir)
        const P = new Map();
        ec.forEach(c => { const a = Math.min(c.libro, c.otra), b = Math.max(c.libro, c.otra), k = a + '-' + b;
          if (!P.has(k)) P.set(k, { a, b, lib: {} });
          P.get(k).lib[c.libro] = c.libro === a ? c.saldo : -c.saldo; });   // en positivo: lo que b debe a a
        const txt = (p, v) => Math.abs(v) < 0.5 ? 'en paz' : `<b>${escH(nomE(v > 0 ? p.b : p.a))}</b> debe <b>${eur(Math.abs(v))}</b> a ${escH(nomE(v > 0 ? p.a : p.b))}`;
        return [...P.values()].map(p => { const vs = [p.a, p.b].filter(l => l in p.lib), dif = vs.length === 2 && Math.abs(p.lib[p.a] - p.lib[p.b]) > 1;
          return `<div class="db-pair ${dif ? 'dif' : ''}"><div class="db-ph">${escH(nomE(p.a))} ↔ ${escH(nomE(p.b))}${dif ? ' <span class="bdg b-warn">no cuadran</span>' : ''}</div>
            ${[p.a, p.b].map(l => `<div class="db-row"><span class="muted">Según ${escH(nomE(l))}</span><span>${l in p.lib ? txt(p, p.lib[l]) : '<span class="muted">sin apuntes</span>'}</span></div>`).join('')}</div>`; }).join('')
          || '<p class="empty">Sin saldos entre empresas</p>'; })()}
      <h4>Cuentas con socios (551 / 552)</h4>
      ${so.length ? so.map(x => `<div class="db-row"><span>${escH(nomE(x.empresa))} · ${escH(x.cuenta)}</span><b class="${x.saldo < 0 ? 'neg' : ''}">${eur(x.saldo)}</b></div>`).join('') : '<p class="empty">Sin movimientos con socios</p>'}
      <p class="muted db-nota">En las de socios, en positivo lo que el socio debe a la empresa y en negativo lo que la empresa le debe al socio.</p></section>

    <section class="db-c db-hueco"><h3>💸 Facturas a pagar</h3>
      <div class="db-3"><div><span>Vencido</span><b>—</b></div><div><span>Próximos 30 días</span><b>—</b></div><div><span>Total a pagar</span><b>—</b></div></div>
      <p class="muted">Hueco reservado para la previsión de tesorería. Cuando estén al día las facturas de proveedor pagadas, aquí saldrá lo que hay que pagar y se restará de la liquidez.</p></section>
  </div>`;
  $('#db').querySelectorAll('.db-emp button').forEach(b => b.onclick = () => { DB_E = b.dataset.e; try { localStorage.setItem('pf_db_emp', DB_E); } catch (e) {} dbPinta(); });
  $('#db-rf').onclick = () => { $('#db-rf').textContent = 'Actualizando…'; dbOpen(true); };
}
