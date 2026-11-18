"""
data.py — capa de datos del dashboard de Corabastos.

Lee data/corabastos.db en modo solo lectura y devuelve DataFrames de pandas.
No importa Streamlit, así que se puede probar desde la terminal:

    python -c "import data; print(data.resumen_general())"
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(
    os.environ.get("CORABASTOS_DB", Path(__file__).parent / "data" / "corabastos.db")
)

CAMPOS = {
    "precio_unidad": "Precio unidad",
    "precio_extra": "Calidad extra",
    "precio_primera": "Calidad primera",
    "variacion_pct": "Variación % vs boletín anterior",
}

FRECUENCIAS = {
    "day": "Diario",
    "week": "Semanal",
    "month": "Mensual",
    "year": "Anual",
}


# ── Conexión ─────────────────────────────────────────────────────────────────

def conectar() -> sqlite3.Connection:
    """Solo lectura e inmutable: nunca crea archivos -wal/-shm junto a la BD."""
    uri = DB_PATH.resolve().as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def consulta(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = conectar()
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def db_version() -> float:
    """Marca de tiempo de la BD; sirve para invalidar la caché al actualizar datos."""
    return DB_PATH.stat().st_mtime


# ── Catálogo ─────────────────────────────────────────────────────────────────

def lista_productos() -> pd.DataFrame:
    return consulta("SELECT nombre, grupo, categoria FROM productos ORDER BY nombre")


def anios() -> list[str]:
    df = consulta("SELECT DISTINCT SUBSTR(fecha, 1, 4) AS anio FROM precios ORDER BY anio DESC")
    return df["anio"].tolist()


def presentaciones(nombre: str) -> pd.DataFrame:
    return consulta(
        """
        SELECT DISTINCT presentacion, cantidad, unidad_medida
        FROM precios WHERE nombre = ?
        ORDER BY presentacion, cantidad
        """,
        (nombre,),
    )


def etiqueta_presentacion(fila: pd.Series) -> str:
    partes = []
    if fila["presentacion"]:
        partes.append(str(fila["presentacion"]))
    if pd.notna(fila["cantidad"]):
        cant = fila["cantidad"]
        partes.append(str(int(cant)) if float(cant).is_integer() else str(cant))
    if fila["unidad_medida"]:
        partes.append(str(fila["unidad_medida"]))
    return " ".join(partes) or "(sin presentación)"


# ── Series de precios ────────────────────────────────────────────────────────

def serie_completa(
    nombre: str,
    presentacion: str | None = None,
    cantidad: float | None = None,
    unidad: str | None = None,
) -> pd.DataFrame:
    """
    Historial completo de un producto (todas las fechas), con la variación
    porcentual calculada contra el boletín anterior.

    Nota: variacion_raw solo trae texto ("Subio", "Bajo", "Estable"), no un
    porcentaje; por eso el % se calcula a partir de los precios reales.
    """
    filtros, params = ["nombre = ?", "precio_unidad IS NOT NULL"], [nombre]
    if presentacion is not None:
        filtros.append("presentacion = ?")
        params.append(presentacion)
    if cantidad is not None:
        filtros.append("cantidad = ?")
        params.append(float(cantidad))
    if unidad is not None:
        filtros.append("unidad_medida = ?")
        params.append(unidad)

    df = consulta(
        f"""
        SELECT fecha, precio_extra, precio_primera, precio_unidad,
               variacion_raw, presentacion, cantidad, unidad_medida
        FROM precios
        WHERE {' AND '.join(filtros)}
        ORDER BY fecha
        """,
        tuple(params),
    )
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["variacion_pct"] = df["precio_unidad"].pct_change() * 100
    return df


def filtrar_anio(df: pd.DataFrame, anio: str | None) -> pd.DataFrame:
    if not anio:
        return df
    return df[df["fecha"].dt.year == int(anio)]


def agregar(df: pd.DataFrame, campo: str, freq: str) -> pd.DataFrame:
    """Promedio, mínimo, máximo y n por periodo (día, semana, mes o año)."""
    d = df[["fecha", campo]].dropna().copy()
    formato = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m", "year": "%Y"}[freq]
    d["periodo"] = d["fecha"].dt.strftime(formato)
    out = (
        d.groupby("periodo")[campo]
        .agg(promedio="mean", min="min", max="max", n="count")
        .reset_index()
        .sort_values("periodo")
    )
    return out


def tarjetas(df: pd.DataFrame) -> dict | None:
    """Indicadores de la cabecera: último precio, promedios y rango del año."""
    if df.empty:
        return None
    ultimo = df.iloc[-1]
    mes = df[df["fecha"].dt.to_period("M") == ultimo["fecha"].to_period("M")]
    anio = df[df["fecha"].dt.year == ultimo["fecha"].year]
    pct = ultimo["variacion_pct"]
    return {
        "ultima_fecha": ultimo["fecha"].strftime("%Y-%m-%d"),
        "ultimo_precio": float(ultimo["precio_unidad"]),
        "variacion_pct": None if pd.isna(pct) else float(pct),
        "tendencia_boletin": ultimo["variacion_raw"] or None,
        "promedio_mes": float(mes["precio_unidad"].mean()),
        "promedio_anio": float(anio["precio_unidad"].mean()),
        "min_anio": float(anio["precio_unidad"].min()),
        "max_anio": float(anio["precio_unidad"].max()),
    }


# ── Catálogo con estadísticas y resumen general ──────────────────────────────

def catalogo_estadisticas() -> pd.DataFrame:
    return consulta(
        """
        WITH ultimo AS (
            SELECT nombre, MAX(fecha) AS hasta FROM precios
            WHERE precio_unidad IS NOT NULL GROUP BY nombre
        )
        SELECT
            p.nombre, p.grupo, p.categoria,
            COUNT(*)                        AS registros,
            MIN(pr.fecha)                   AS desde,
            u.hasta                         AS hasta,
            MAX(CASE WHEN pr.fecha = u.hasta THEN pr.precio_unidad END) AS ultimo_precio,
            ROUND(AVG(pr.precio_unidad), 0) AS precio_promedio,
            MIN(pr.precio_unidad)           AS precio_min,
            MAX(pr.precio_unidad)           AS precio_max
        FROM productos p
        JOIN precios pr ON pr.nombre = p.nombre AND pr.precio_unidad IS NOT NULL
        JOIN ultimo  u  ON u.nombre  = p.nombre
        GROUP BY p.nombre
        ORDER BY p.nombre
        """
    )


def resumen_general() -> dict:
    g = consulta(
        """
        SELECT COUNT(*)                          AS total_registros,
               COUNT(DISTINCT nombre)            AS total_productos,
               COUNT(DISTINCT fecha)             AS dias_con_boletin,
               MIN(fecha)                        AS fecha_desde,
               MAX(fecha)                        AS fecha_hasta
        FROM precios
        """
    ).iloc[0].to_dict()
    act = consulta(
        """
        SELECT fecha_actualizacion, boletines_procesados, filas_insertadas
        FROM actualizaciones ORDER BY id DESC LIMIT 1
        """
    )
    g["ultima_actualizacion"] = act.iloc[0].to_dict() if not act.empty else None
    return g


def estadisticas() -> tuple[pd.DataFrame, pd.DataFrame]:
    por_grupo = consulta(
        """
        SELECT grupo, COUNT(*) AS registros, COUNT(DISTINCT nombre) AS productos
        FROM precios GROUP BY grupo ORDER BY registros DESC
        """
    )
    por_anio = consulta(
        """
        SELECT SUBSTR(fecha, 1, 4) AS anio, COUNT(*) AS registros,
               COUNT(DISTINCT fecha) AS boletines, COUNT(DISTINCT nombre) AS productos,
               ROUND(AVG(precio_unidad), 0) AS precio_promedio
        FROM precios WHERE precio_unidad IS NOT NULL
        GROUP BY anio ORDER BY anio DESC
        """
    )
    return por_grupo, por_anio
