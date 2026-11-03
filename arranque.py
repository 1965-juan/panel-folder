"""Arranque del panel: si hay repositorio configurado, descarga la última versión del código
desde GitHub antes de arrancar. Si falla la descarga, arranca con la versión que ya tenía."""
import io
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = os.environ.get("PANEL_REPO", "")          # p. ej. 1965-juan/panel-folder
BRANCH = os.environ.get("PANEL_BRANCH", "main")
TOKEN_FILE = Path(os.environ.get("PANEL_GITHUB_TOKEN_FILE", "/data/token_github.txt"))
LIVE = Path("/data/codigo")                       # código descargado (persiste entre reinicios)
BUILTIN = Path("/srv/app")                        # código que venía en la imagen


def log(*a):
    print("[arranque]", *a, flush=True)


def descargar():
    if not REPO:
        return False
    token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/tarball/{BRANCH}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "PanelFolder"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = urllib.request.urlopen(req, timeout=60).read()
    tmp = Path("/data/codigo_nuevo")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        for m in t.getmembers():
            if m.issym() or m.islnk() or ".." in Path(m.name).parts:
                continue
            parts = Path(m.name).parts[1:]           # quita la carpeta raíz del tarball
            if not parts or parts[0] != "app":
                continue
            m.name = str(Path(*parts))
            t.extract(m, tmp)
    if not (tmp / "app" / "main.py").exists():
        raise RuntimeError("el repositorio no tiene app/main.py")
    shutil.rmtree(LIVE, ignore_errors=True)
    tmp.rename(LIVE)
    return True


def main():
    try:
        if descargar():
            log("código actualizado desde", REPO, BRANCH)
    except Exception as e:
        log("no se pudo actualizar, sigo con la versión anterior:", e)
    root = LIVE if (LIVE / "app" / "main.py").exists() else BUILTIN.parent
    log("arrancando desde", root)
    os.chdir(root)
    sys.path.insert(0, str(root))
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8090, proxy_headers=True)


if __name__ == "__main__":
    main()
