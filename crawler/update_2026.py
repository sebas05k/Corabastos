"""
update_2026.py  (versión SQLite)
=================================
Descarga boletines nuevos e inserta directo en corabastos.db.
No genera CSV intermedios. El CSV histórico sigue como respaldo.

Uso:
    python update_2026.py                  # descarga + inserta
    python update_2026.py --only-download  # solo descarga PDFs
    python update_2026.py --only-normalize # solo procesa PDFs ya descargados
    python update_2026.py --start 2026-03-01
"""

import os
import re
import sys
import json
import time
import random
import sqlite3
import argparse
import requests
import pdfplumber
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
from tqdm import tqdm

# ── Rutas ─────────────────────────────────────────────────────────────────────
BASE         = Path(__file__).parent
OUT_DIR_PDFS = BASE / "data" / "raw_pdfs"
DB_PATH      = Path(os.environ.get("CORABASTOS_DB", BASE.parent / "data" / "corabastos.db"))
OUT_DIR_LOGS = BASE / "data" / "logs"
START_2026   = date(2026, 1, 1)
UA           = "Mozilla/5.0 (corabastos-crawler/2026-sqlite)"

def siguiente_a_ultima_fecha() -> date:
    """Día siguiente al último boletín guardado (evita revisar todo 2026 en cada corrida)."""
    if DB_PATH.exists():
        con = sqlite3.connect(DB_PATH)
        ultima = con.execute("SELECT MAX(fecha) FROM precios").fetchone()[0]
        con.close()
        if ultima:
            return date.fromisoformat(ultima) + timedelta(days=1)
    return START_2026

# ── URLs ──────────────────────────────────────────────────────────────────────

def url_for_day(d: date) -> str:
    return (f"https://corabastos.com.co/wp-content/uploads/"
            f"{d:%Y}/{d:%m}/Boletin_diario_{d:%Y%m%d}.pdf")

def iter_days(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)

# ── Descarga ──────────────────────────────────────────────────────────────────

def head_exists(url, timeout=20):
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={"User-Agent": UA})
        if r.status_code == 405:
            r = requests.get(url, timeout=timeout, stream=True,
                             allow_redirects=True, headers={"User-Agent": UA})
            return r.status_code == 200
        return r.status_code == 200
    except requests.RequestException:
        return False

def download_pdf(url, out_path, timeout=60):
    r = requests.get(url, timeout=timeout, stream=True, headers={"User-Agent": UA})
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if chunk:
                f.write(chunk)

def run_download(start, end, sleep_min=0.3, sleep_max=0.9):
    OUT_DIR_PDFS.mkdir(parents=True, exist_ok=True)
    downloaded, missing, errors = [], [], []
    days = list(iter_days(start, end))
    print(f"\n📥 Revisando {len(days)} días ({start} → {end})")

    for d in tqdm(days, desc="Descargando PDFs"):
        url      = url_for_day(d)
        out_path = OUT_DIR_PDFS / f"Boletin_diario_{d:%Y%m%d}.pdf"

        if out_path.exists() and out_path.stat().st_size > 10_000:
            continue
        if not head_exists(url):
            missing.append({"date": d.isoformat(), "url": url})
            continue
        try:
            download_pdf(url, out_path)
            downloaded.append({"date": d.isoformat(), "url": url})
        except Exception as e:
            errors.append({"date": d.isoformat(), "url": url, "error": str(e)})
        time.sleep(random.uniform(sleep_min, sleep_max))

    return downloaded, missing, errors

# ── Parser ────────────────────────────────────────────────────────────────────

MONEY_RE = re.compile(r"\$\s*\d[\d\.,]*")

CATEGORY_KEYWORDS = [
    "HORTALIZAS","VERDURAS","FRUTAS","TUBERCULOS","TUBÉRCULOS",
    "PLATANOS","PLÁTANOS","GRANOS","CEREALES","CARNES","CÁRNICOS",
    "AVES","POLLO","PESCADOS","PESCADO","MARISCOS","LACTEOS","LÁCTEOS",
    "HUEVOS","ABARROTES","PROCESADOS","ENLATADOS","BEBIDAS","DULCES"
]
PROTEINA_HINTS = [
    "POLLO","ALAS","PECHUGA","MENUDENCIAS","PERNILES","PESCADO","FILETE",
    "BAGRE","MOJARRA","CAMARON","ROBALO","SIERRA","CORVINA","TILAPIA",
    "CARNE","CERDO","RES","CHORIZO","JAMON","SALCHICHA"
]
AGRICOLA_HINTS = [
    "PAPA","CEBOLLA","TOMATE","AHUYAMA","YUCA","PLATANO","PLÁTANO",
    "LIMON","LIMÓN","BANANO","ARVEJA","ZANAHORIA","LECHUGA","CILANTRO",
    "REPOLLO","BROCOLI","BRÓCOLI","PIMENTON","PIMENTÓN","MORA","LULO",
    "NARANJA","MANDARINA","MANGO","PIÑA","PAPAYA","AGUACATE",
    "MAIZ","MAÍZ","FRIJOL","FRÍJOL"
]
PROCESADO_HINTS = [
    "CHOCOLATE","GALLET","PASTA","ARROZ","AZUCAR","AZÚCAR",
    "ACEITE","HARINA","SAL","CAFÉ","CAFE"
]
UNITS = {
    "KG","KILO","KILOGRAMO","LB","LIBRA","BULTO","CAJA","ATADO",
    "CANASTA","DOCENA","UND","UNIDAD","ARROBA","PAQ","PAQUETE",
    "MANOJO","RAMA","BANDEJA","SACO","TULA","ROLLO"
}

def _c(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def _num(s):
    s = _c(s).replace("$","").replace("COP","").replace(".","").replace(",","")
    try: return float(s)
    except: return None

def _fecha(fn):
    m = re.search(r"Boletin_diario_(\d{8})\.pdf$", fn, re.IGNORECASE)
    if not m: return None
    g = m.group(1)
    return f"{g[:4]}-{g[4:6]}-{g[6:8]}"

def _header(up):
    return any(b in up for b in [
        "CORABASTOS","BOLETIN","PESOS COLOMBIANOS","$ CAL.",
        "NOMBRE","PRESENTACIÓN","PRESENTACION","CANTIDAD","UNIDAD DE MEDIDA",
        "PRECIO CON","CALIDAD","PRECIO POR","VARIACIÓN","VARIACION",
        "PRECIO DÍA","PRECIO DIA"
    ])

def _cat(line):
    up = _c(line).upper()
    if not up or "$" in up: return None
    if any(k in up for k in CATEGORY_KEYWORDS):
        up = re.sub(r"[-–—•|]+", " ", up)
        return re.sub(r"\s+", " ", up).strip()[:80]
    return None

def _grupo(nombre):
    up = _c(nombre).upper()
    if any(h in up for h in PROTEINA_HINTS): return "PROTEINA"
    if any(h in up for h in AGRICOLA_HINTS): return "AGRICOLA"
    if any(h in up for h in PROCESADO_HINTS): return "PROCESADO"
    return "OTRO"

def _left(left):
    tokens = _c(left).split()
    if len(tokens) < 2: return None
    qi = None
    for i in range(len(tokens)-1, -1, -1):
        if re.fullmatch(r"\d+([.,]\d+)?", tokens[i]):
            qi = i; break
    if qi is None:
        return {"nombre": _c(left), "presentacion":"", "cantidad":None, "unidad_medida":""}
    unidad = tokens[qi+1] if qi+1 < len(tokens) else ""
    pres   = ""
    if qi-1 >= 0:
        cand = re.sub(r"[^A-Za-zÁÉÍÓÚÑáéíóúñ]","",tokens[qi-1]).upper()
        if cand in UNITS: pres = tokens[qi-1]; nombre_t = tokens[:qi-1]
        else: nombre_t = tokens[:qi]
    else: nombre_t = tokens[:qi]
    try: qty = float(tokens[qi].replace(",","."))
    except: qty = None
    return {"nombre": _c(" ".join(nombre_t)), "presentacion": _c(pres),
            "cantidad": qty, "unidad_medida": _c(unidad)}

def _parse_line(line, categoria):
    raw = _c(line)
    if not raw or _header(raw.upper()): return None
    monies = list(MONEY_RE.finditer(raw))
    if not monies: return None
    lp = _left(_c(raw[:monies[0].start()]))
    if lp is None or not lp.get("nombre"): return None
    grupo = _grupo(lp["nombre"])
    if len(monies) >= 3:
        m1,m2,m3 = monies[-3],monies[-2],monies[-1]
        return {"categoria":categoria,"grupo":grupo,**lp,
                "precio_extra":_num(raw[m1.start():m1.end()]),
                "precio_primera":_num(raw[m2.start():m2.end()]),
                "precio_unidad":_num(raw[m3.start():m3.end()]),
                "variacion_raw":_c(raw[m3.end():])}
    last = monies[-1]
    return {"categoria":categoria,"grupo":grupo,**lp,
            "precio_extra":None,"precio_primera":None,
            "precio_unidad":_num(raw[last.start():last.end()]),
            "variacion_raw":_c(raw[last.end():])}

def extract_records(pdf_path, fecha):
    records, cat = [], ""
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages, 1):
            lines = [_c(x) for x in (page.extract_text() or "").split("\n") if _c(x)]
            for ln in lines:
                c = _cat(ln)
                if c: cat = c; continue
                row = _parse_line(ln, cat)
                if row:
                    row.update({"fecha":fecha,"fuente":"CORABASTOS_PDF","page":pi})
                    records.append(row)
    return records

# ── SQLite ─────────────────────────────────────────────────────────────────────

def get_fechas_en_db() -> set:
    """Fechas que ya existen en la DB para no re-insertar."""
    if not DB_PATH.exists():
        print(f"❌ No encontré la DB en {DB_PATH}")
        print("   Corre primero: python migrate_to_sqlite.py")
        sys.exit(1)
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT DISTINCT fecha FROM precios").fetchall()
    con.close()
    return {r[0] for r in rows}

def insert_records(records: list) -> int:
    if not records:
        return 0

    df = pd.DataFrame(records)
    df = df[df["precio_unidad"].notna()].copy()

    cols = [
        "fecha","nombre","grupo","categoria","presentacion",
        "cantidad","unidad_medida","precio_extra","precio_primera",
        "precio_unidad","variacion_raw","fuente","page"
    ]
    cols = [c for c in cols if c in df.columns]

    con = sqlite3.connect(DB_PATH)

    # Insertar precios
    df[cols].to_sql("precios", con, if_exists="append", index=False,
                    chunksize=2000, method="multi")

    # Actualizar tabla productos
    con.executemany(
        "INSERT OR IGNORE INTO productos (nombre, grupo, categoria) VALUES (?,?,?)",
        df[["nombre","grupo","categoria"]].drop_duplicates().values.tolist()
    )

    # Registrar actualización
    fechas_nuevas = df["fecha"].nunique()
    con.execute(
        "INSERT INTO actualizaciones (fecha_actualizacion, boletines_procesados, filas_insertadas, nota) VALUES (?,?,?,?)",
        (date.today().isoformat(), fechas_nuevas, len(df), "update_2026.py")
    )
    con.commit()
    con.close()
    return len(df)

def run_normalize(start: date, end: date):
    fechas_existentes = get_fechas_en_db()

    # Buscar PDFs nuevos (cuya fecha NO está en la DB)
    pattern = re.compile(r"Boletin_diario_(\d{8})\.pdf$", re.IGNORECASE)
    pdfs_nuevos = []

    for fn in sorted(os.listdir(OUT_DIR_PDFS)):
        m = pattern.match(fn)
        if not m: continue
        g = m.group(1)
        fecha = f"{g[:4]}-{g[4:6]}-{g[6:8]}"
        if fecha < start.isoformat() or fecha > end.isoformat(): continue
        if fecha in fechas_existentes: continue
        pdfs_nuevos.append((fn, fecha))

    if not pdfs_nuevos:
        print("  ✅ La DB ya está al día, sin PDFs nuevos que procesar.")
        return 0

    print(f"\n📊 Procesando {len(pdfs_nuevos)} PDFs nuevos → SQLite")
    all_records = []

    for fn, fecha in tqdm(pdfs_nuevos, desc="Normalizando"):
        try:
            recs = extract_records(OUT_DIR_PDFS / fn, fecha)
            print(f"{fecha}: {len(recs)} registros")
            all_records.extend(recs)
        except Exception as e:
            print(f"\n  ⚠️  Error en {fn}: {e}")

    inserted = insert_records(all_records)
    print(f"✅ {inserted:,} filas insertadas en {DB_PATH.name}")
    return inserted

# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Actualiza Corabastos 2026 → SQLite")
    p.add_argument("--start", default=None, help="Fecha inicio ISO (ej: 2026-01-01)")
    p.add_argument("--only-download",  action="store_true")
    p.add_argument("--only-normalize", action="store_true")
    return p.parse_args()

def main():
    args  = parse_args()
    OUT_DIR_LOGS.mkdir(parents=True, exist_ok=True)

    start = date.fromisoformat(args.start) if args.start else siguiente_a_ultima_fecha()
    end   = date.today()

    log = {
        "run_date": date.today().isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "downloaded": [], "missing": [], "errors": [],
        "inserted_rows": 0
    }

    if not args.only_normalize:
        dl, miss, err = run_download(start, end)
        log["downloaded"] = dl
        log["missing"]    = miss
        log["errors"]     = err
        print(f"\n  ↳ Nuevos: {len(dl)}  Sin boletín: {len(miss)}  Errores: {len(err)}")

    if not args.only_download:
        inserted = run_normalize(start, end)
        log["inserted_rows"] = inserted

    log_path = OUT_DIR_LOGS / f"update_{date.today().isoformat()}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"\n📋 Log: {log_path}")

if __name__ == "__main__":
    main()
