# 🌽 Corabastos · Precios mayoristas (Streamlit)

Dashboard de los boletines diarios de precios de Corabastos (Bogotá).
Reemplaza al backend Node + frontend Vue por una sola app de Streamlit que lee la base SQLite.

```
app.py                      interfaz (Dashboard · Catálogo · Resumen general)
data.py                     consultas a SQLite con pandas (sin Streamlit)
data/corabastos.db          base de datos (solo lectura para la app)
crawler/update_2026.py      descarga boletines PDF nuevos y los inserta en la BD
.github/workflows/          actualización automática semanal con GitHub Actions
requirements.txt            dependencias de la app (Streamlit Cloud)
requirements-crawler.txt    dependencias del crawler (solo GitHub Actions / local)
```

## Probar en local

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Publicar en Streamlit Community Cloud

1. Crea un repositorio en GitHub y sube todo el contenido de esta carpeta (incluida `data/corabastos.db`, ~10 MB).
2. Entra a <https://share.streamlit.io> → **Create app** → elige el repositorio y la rama.
3. En **Main file path** escribe `app.py` y pulsa **Deploy**.

Cada vez que el repositorio reciba un commit nuevo (por ejemplo, la BD actualizada), la app se redespliega sola.

## Mantener los datos al día

Streamlit Cloud no sirve para correr el crawler: su disco es temporal y la app se duerme sin visitas.
Por eso el crawler corre en **GitHub Actions** (`.github/workflows/actualizar.yml`):

- Se ejecuta cada lunes (8:00 a. m. Bogotá) y también a mano: pestaña **Actions → Actualizar precios Corabastos → Run workflow**.
- Descarga los boletines posteriores al último guardado, los inserta en `data/corabastos.db` y hace commit.
- La BD actual llega hasta el 2026-07-01, así que la primera corrida rellenará los meses faltantes.

Si prefieres actualizar desde tu computador:

```bash
pip install -r requirements-crawler.txt
python crawler/update_2026.py
git add data/corabastos.db && git commit -m "Datos: actualización" && git push
```

> Cada actualización guarda de nuevo la BD en el historial de Git. Por eso la frecuencia por defecto es semanal.

## Notas

- `variacion_raw` en los PDFs solo dice «Subió / Bajó / Estable», no un porcentaje. La gráfica **Variación %** se calcula con los precios de cada boletín.
- No se portó el módulo SIPSA: su tabla está vacía en la BD y la sincronización estaba fallando.
