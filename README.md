# Panel Folder

Panel propio de Folder Castellón que trabaja encima de Odoo (API JSON-2).
Funciona en el NAS (Container Manager, carpeta docker/panel-folder) en el puerto 8090.

- `app/` código del panel (FastAPI + página web). Se descarga solo al arrancar el contenedor.
- `arranque.py` descarga la última versión de este repositorio y arranca el panel.
- `datos/` (solo en el NAS, nunca en el repositorio): clave de Odoo, token de GitHub, usuarios.

Para publicar un cambio: subirlo a `main` y pulsar "Actualizar panel" (usuario Juan).
Si cambia `requirements.txt` o `Dockerfile` hay que reconstruir el proyecto en Container Manager.
