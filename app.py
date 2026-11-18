"""
app.py — Dashboard de precios mayoristas de Corabastos (Streamlit).

Ejecutar en local:   streamlit run app.py
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data

st.set_page_config(
    page_title="Corabastos · Precios mayoristas",
    page_icon="🌽",
    layout="wide",
)

VERDE, ROJO, GRIS = "#2E7D32", "#C62828", "#9E9E9E"

# ── Verificación de la base de datos ─────────────────────────────────────────
_ok, _motivo = data.db_valida()
if not _ok:
    st.error(
        f"No puedo usar la base de datos `{data.DB_PATH.relative_to(data.RAIZ).as_posix()}`: {_motivo}."
    )
    st.markdown(
        "Revisa en GitHub que el archivo **`data/corabastos.db`** (~10 MB) esté en el repositorio. "
        "Si no aparece, súbelo con `git add -f data/corabastos.db` (algunos `.gitignore` ignoran los `.db`), "
        "haz commit y push; la app se redespliega sola."
    )
    st.markdown("**Archivos que ve la app en el servidor:**")
    st.code(
        "\n".join(
            sorted(
                p.relative_to(data.RAIZ).as_posix()
                for p in data.RAIZ.rglob("*")
                if p.is_file() and not {".git", "__pycache__"} & set(p.parts)
            )[:60]
        )
    )
    st.stop()


# ── Caché (se invalida sola cuando cambia el archivo de la BD) ───────────────

V = data.db_version()


@st.cache_data(show_spinner=False)
def _productos(v):
    return data.lista_productos()


@st.cache_data(show_spinner=False)
def _anios(v):
    return data.anios()


@st.cache_data(show_spinner=False)
def _presentaciones(v, nombre):
    return data.presentaciones(nombre)


@st.cache_data(show_spinner=False)
def _serie(v, nombre, presentacion, cantidad, unidad):
    return data.serie_completa(nombre, presentacion, cantidad, unidad)


@st.cache_data(show_spinner=False)
def _catalogo(v):
    return data.catalogo_estadisticas()


@st.cache_data(show_spinner=False)
def _resumen(v):
    return data.resumen_general()


@st.cache_data(show_spinner=False)
def _estadisticas(v):
    return data.estadisticas()


# ── Utilidades de formato ────────────────────────────────────────────────────

def pesos(n) -> str:
    """1234567 -> $1.234.567 (separador de miles colombiano)."""
    if n is None or pd.isna(n):
        return "—"
    return "$" + f"{n:,.0f}".replace(",", ".")


def pct(n) -> str | None:
    if n is None or pd.isna(n):
        return None
    return f"{n:+.1f}%".replace(".", ",")


# ── Barra lateral ────────────────────────────────────────────────────────────

st.sidebar.title("🌽 Corabastos")
st.sidebar.caption("Precios mayoristas · Bogotá")

productos = _productos(V)

grupo = st.sidebar.selectbox(
    "Grupo", ["Todos"] + sorted(productos["grupo"].dropna().unique().tolist())
)
opciones = productos if grupo == "Todos" else productos[productos["grupo"] == grupo]
nombres = opciones["nombre"].tolist()

por_defecto = nombres.index("PAPA PASTUSA") if "PAPA PASTUSA" in nombres else 0
producto = st.sidebar.selectbox(
    "Producto (escribe para buscar)", nombres, index=por_defecto
)

anios = _anios(V)
anio_sel = st.sidebar.selectbox("Año", ["Todos"] + anios, index=1 if anios else 0)
anio = None if anio_sel == "Todos" else anio_sel

campo = st.sidebar.selectbox(
    "Dato a graficar", list(data.CAMPOS), format_func=data.CAMPOS.get
)
freq = st.sidebar.radio(
    "Frecuencia", list(data.FRECUENCIAS), format_func=data.FRECUENCIAS.get, horizontal=True
)

# Presentación: solo aparece si el producto tiene más de una
pres = _presentaciones(V, producto)
f_pres = f_cant = f_uni = None
if len(pres) > 1:
    etiquetas = [data.etiqueta_presentacion(r) for _, r in pres.iterrows()]
    elegida = st.sidebar.selectbox("Presentación", etiquetas)
    fila = pres.iloc[etiquetas.index(elegida)]
    f_pres = fila["presentacion"]
    f_cant = None if pd.isna(fila["cantidad"]) else fila["cantidad"]
    f_uni = fila["unidad_medida"]
elif len(pres) == 1:
    st.sidebar.caption(f"Presentación: {data.etiqueta_presentacion(pres.iloc[0])}")

resumen = _resumen(V)
st.sidebar.divider()
st.sidebar.caption(
    f"Datos del {resumen['fecha_desde']} al {resumen['fecha_hasta']} · "
    f"{resumen['dias_con_boletin']} boletines · fuente: Corabastos (PDF diario)"
)

# ── Pestañas ─────────────────────────────────────────────────────────────────

tab_dash, tab_cat, tab_res = st.tabs(["📈 Dashboard", "🥕 Catálogo", "📊 Resumen general"])

# ═════════════════════════════ Dashboard ═════════════════════════════════════
def pintar_dashboard():
    historial = _serie(V, producto, f_pres, f_cant, f_uni)
    vista = data.filtrar_anio(historial, anio)

    st.subheader(producto)
    info = opciones[opciones["nombre"] == producto].iloc[0]
    st.caption(f"Grupo: {info['grupo'] or '—'} · Categoría: {info['categoria'] or '—'}")

    t = data.tarjetas(historial)
    if t is None:
        st.warning("Este producto no tiene precios registrados.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Último precio",
        pesos(t["ultimo_precio"]),
        delta=(pct(t["variacion_pct"]) + " vs boletín anterior") if pct(t["variacion_pct"]) else None,
    )
    c2.metric("Promedio del mes", pesos(t["promedio_mes"]), help="Mes del último boletín")
    c3.metric(
        "Rango del año",
        f"{pesos(t['min_anio'])} – {pesos(t['max_anio'])}",
        help=f"Mínimo y máximo de {t['ultima_fecha'][:4]}",
    )
    c4.metric("Último boletín", t["ultima_fecha"])

    if vista.empty:
        st.info("No hay datos para el año seleccionado.")
        return

    # Serie a graficar según frecuencia
    if freq == "day":
        serie = vista[["fecha", campo]].dropna().rename(columns={campo: "promedio"})
        serie["periodo"] = serie["fecha"].dt.strftime("%Y-%m-%d")
        serie["min"] = serie["max"] = serie["promedio"]
    else:
        serie = data.agregar(vista, campo, freq)

    fig = go.Figure()
    if campo == "variacion_pct":
        colores = [VERDE if v >= 0 else ROJO for v in serie["promedio"]]
        fig.add_bar(x=serie["periodo"], y=serie["promedio"], marker_color=colores, name="Variación %")
        fig.update_yaxes(ticksuffix="%", zeroline=True)
    else:
        fig.add_trace(
            go.Scatter(
                x=serie["periodo"], y=serie["promedio"], mode="lines",
                line=dict(color=VERDE, width=2, shape="spline", smoothing=0.6),
                fill="tozeroy", fillcolor="rgba(46,125,50,0.08)",
                name="Promedio" if freq != "day" else "Precio",
                hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>",
            )
        )
        if freq != "day":
            for col, nombre in (("max", "Máximo"), ("min", "Mínimo")):
                fig.add_trace(
                    go.Scatter(
                        x=serie["periodo"], y=serie[col], mode="lines",
                        line=dict(color=GRIS, width=1, dash="dot"), name=nombre,
                        hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>",
                    )
                )
    fig.update_layout(
        height=420, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
        legend=dict(orientation="h", y=1.08, x=0),
        yaxis_title=data.CAMPOS[campo],
    )
    st.plotly_chart(fig)

    if campo == "variacion_pct":
        st.caption(
            "El boletín del PDF solo indica si el precio subió, bajó o siguió estable; "
            "aquí el porcentaje se calcula con los precios de cada boletín."
        )

    # Tabla + descarga
    st.markdown("**Detalle**")
    if freq == "day":
        tabla = vista[["fecha", "precio_extra", "precio_primera", "precio_unidad", "variacion_raw", "variacion_pct"]].copy()
        tabla["fecha"] = tabla["fecha"].dt.strftime("%Y-%m-%d")
        tabla = tabla.rename(columns={"variacion_raw": "tendencia"}).iloc[::-1]
        config = {
            "precio_extra": st.column_config.NumberColumn("Calidad extra", format="$%.0f"),
            "precio_primera": st.column_config.NumberColumn("Calidad primera", format="$%.0f"),
            "precio_unidad": st.column_config.NumberColumn("Precio unidad", format="$%.0f"),
            "variacion_pct": st.column_config.NumberColumn("Variación %", format="%.1f%%"),
        }
    else:
        tabla = serie[["periodo", "promedio", "min", "max", "n"]].iloc[::-1]
        config = {
            "promedio": st.column_config.NumberColumn("Promedio", format="%.1f" if campo == "variacion_pct" else "$%.0f"),
            "min": st.column_config.NumberColumn("Mínimo", format="%.1f" if campo == "variacion_pct" else "$%.0f"),
            "max": st.column_config.NumberColumn("Máximo", format="%.1f" if campo == "variacion_pct" else "$%.0f"),
            "n": st.column_config.NumberColumn("Boletines"),
        }
    st.dataframe(tabla, hide_index=True, column_config=config)
    st.download_button(
        "⬇️ Descargar CSV",
        tabla.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"corabastos_{producto.lower().replace(' ', '_')}_{freq}.csv",
        mime="text/csv",
    )


with tab_dash:
    pintar_dashboard()

# ═════════════════════════════ Catálogo ══════════════════════════════════════
with tab_cat:
    st.subheader("Catálogo de productos")
    cat = _catalogo(V)

    f1, f2 = st.columns([2, 1])
    texto = f1.text_input("Buscar por nombre", placeholder="papa, tomate, leche…")
    grupo_cat = f2.selectbox("Grupo ", ["Todos"] + sorted(cat["grupo"].dropna().unique().tolist()), key="grupo_cat")

    vista_cat = cat
    if texto:
        vista_cat = vista_cat[vista_cat["nombre"].str.contains(texto, case=False, na=False)]
    if grupo_cat != "Todos":
        vista_cat = vista_cat[vista_cat["grupo"] == grupo_cat]

    st.caption(f"{len(vista_cat)} de {len(cat)} productos")
    st.dataframe(
        vista_cat,
        hide_index=True,
        column_config={
            "nombre": "Producto", "grupo": "Grupo", "categoria": "Categoría",
            "registros": st.column_config.NumberColumn("Registros"),
            "desde": "Desde", "hasta": "Hasta",
            "ultimo_precio": st.column_config.NumberColumn("Último precio", format="$%.0f"),
            "precio_promedio": st.column_config.NumberColumn("Promedio", format="$%.0f"),
            "precio_min": st.column_config.NumberColumn("Mínimo", format="$%.0f"),
            "precio_max": st.column_config.NumberColumn("Máximo", format="$%.0f"),
        },
    )
    st.download_button(
        "⬇️ Descargar catálogo (CSV)",
        vista_cat.to_csv(index=False).encode("utf-8-sig"),
        file_name="corabastos_catalogo.csv",
        mime="text/csv",
    )

# ═════════════════════════════ Resumen general ═══════════════════════════════
with tab_res:
    st.subheader("Resumen de la base de datos")
    r = resumen
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Registros", f"{r['total_registros']:,}".replace(",", "."))
    m2.metric("Productos", r["total_productos"])
    m3.metric("Boletines", r["dias_con_boletin"])
    m4.metric("Último boletín", r["fecha_hasta"])

    por_grupo, por_anio = _estadisticas(V)
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Registros por grupo**")
        fig_g = go.Figure(go.Bar(x=por_grupo["grupo"], y=por_grupo["registros"], marker_color=VERDE))
        fig_g.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_g)
        st.dataframe(por_grupo, hide_index=True)
    with g2:
        st.markdown("**Boletines y precio promedio por año**")
        st.dataframe(
            por_anio, hide_index=True,
            column_config={"precio_promedio": st.column_config.NumberColumn("Precio promedio", format="$%.0f")},
        )

    ult = r.get("ultima_actualizacion")
    if ult:
        st.caption(
            f"Última actualización del crawler: {ult['fecha_actualizacion']} "
            f"({ult['boletines_procesados']} boletines, {ult['filas_insertadas']} filas)."
        )
