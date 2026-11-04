/* Sustituye las capacidades de Claude (db, assets, downloads) por el almacén del Panel Folder en el NAS.
   Así el presupuestador funciona igual que antes, pero guarda en el NAS y lee Odoo en directo. */
(function () {
  "use strict";
  const subs = []; // {kind:'doc'|'coll', path, cb, err}

  async function req(method, url, body) {
    const r = await fetch(url, {
      method, credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401) { location.href = "/entrar"; throw new Error("Sesión caducada"); }
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) throw Object.assign(new Error((j && j.detail) || r.statusText), { code: r.status });
    return j;
  }

  function docSnap(id, data) { return { id, exists: data !== null && data !== undefined, data: () => data }; }

  async function runSub(s) {
    try {
      if (s.kind === "doc") {
        const [coll, id] = s.path.split("/");
        const r = await req("GET", `/api/store/${coll}/${encodeURIComponent(id)}`);
        s.cb(docSnap(id, r.exists ? r.data : null));
      } else {
        const rows = await req("GET", `/api/store/${s.path}`);
        s.cb({ docs: rows.map(x => docSnap(x.id, x.data)), size: rows.length, empty: !rows.length });
      }
    } catch (e) { if (s.err) s.err(e); }
  }
  function refresh(coll) { subs.filter(s => s.path.split("/")[0] === coll && !s.static).forEach(runSub); }

  const db = {
    doc(path) {
      const [coll, id] = path.split("/");
      return {
        onSnapshot(cb, err) { const s = { kind: "doc", path, cb, err }; subs.push(s); runSub(s); return () => subs.splice(subs.indexOf(s), 1); },
        async set(data) { await req("PUT", `/api/store/${coll}/${encodeURIComponent(id)}`, data); refresh(coll); },
        async delete() { await req("DELETE", `/api/store/${coll}/${encodeURIComponent(id)}`); refresh(coll); },
      };
    },
    collection(name) {
      return {
        onSnapshot(cb, err) {
          const s = { kind: "coll", path: name, cb, err, static: /_odoo$/.test(name) };
          subs.push(s); runSub(s); return () => subs.splice(subs.indexOf(s), 1);
        },
        async add(data) { const r = await req("POST", `/api/store/${name}`, data); refresh(name); return { id: r.id }; },
      };
    },
  };

  const assets = {
    async upload(file) {
      const fd = new FormData(); fd.append("file", file, file.name || "foto.png");
      const r = await fetch("/api/blob", { method: "POST", body: fd, credentials: "same-origin" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);
      return { id: j.id };
    },
  };

  const downloads = {
    async save({ filename, data }) {
      const url = URL.createObjectURL(data);
      const a = document.createElement("a"); a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    },
  };

  // cambios de otros usuarios: refresco suave cada 30 s
  setInterval(() => { if (document.visibilityState === "visible") subs.filter(s => !s.static && s.kind === "coll").forEach(runSub); }, 30000);

  window.claude = { use: async name => ({ db, assets, downloads }[name] || null) };
})();
