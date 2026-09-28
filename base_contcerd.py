import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date, timedelta
import threading
import time

# ==================== CONEXIÓN CON GOOGLE SHEETS ====================
SPREADSHEET_ID = "10aCfWrVTpIbXGMrM-TbQ4R_xFT7xP9ShtCgQN8go0LI"

@st.cache_resource
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

@st.cache_resource
def get_spreadsheet():
    return get_client().open_by_key(SPREADSHEET_ID)

# ==================== HELPERS ====================
def es_enfermeria(corral):
    return str(corral) in ("9", "10")

def timestamp_ahora():
    return datetime.now().isoformat()

def formatear_timestamp(ts):
    try:
        return pd.to_datetime(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts

# ==================== FUNCIONES DE BASE DE DATOS ====================
def init_hojas():
    sh = get_spreadsheet()
    ws = sh.worksheet("estado")
    valores = ws.get_all_values()

    ids_esperados = set()
    for caseta in range(1, 5):
        for i in range(1, 19):
            ids_esperados.add(f"{caseta}-{i}")

    ids_existentes = set()
    for fila in valores[1:]:
        if fila and fila[0]:
            ids_existentes.add(fila[0])

    faltantes = []
    for uid in sorted(ids_esperados - ids_existentes):
        caseta, corral = uid.split("-")
        faltantes.append([uid, caseta, corral, "0"])

    if faltantes:
        ws.append_rows(faltantes, value_input_option="USER_ENTERED")

def get_estado():
    sh = get_spreadsheet()
    ws = sh.worksheet("estado")
    valores = ws.get_all_values()
    estado = {}
    for fila in valores[1:]:
        if len(fila) >= 4 and fila[0]:
            try:
                estado[fila[0]] = int(fila[3]) if fila[3] else 0
            except ValueError:
                estado[fila[0]] = 0
    return estado

def _buscar_fila_estado(ws_estado, unidad_id, valores=None):
    if valores is None:
        valores = ws_estado.get_all_values()
    for i, fila in enumerate(valores[1:], start=2):
        if fila and fila[0] == unidad_id:
            try:
                valor = int(fila[3]) if len(fila) > 3 and fila[3] else 0
            except ValueError:
                valor = 0
            return i, valor
    return None, None

def _registrar_movimiento(sh, unidad_id, categoria, anterior, nuevo, motivo=""):
    ws_mov = sh.worksheet("movimientos")
    ws_mov.append_row([
        timestamp_ahora(), unidad_id, categoria,
        str(anterior), str(nuevo), motivo or ""
    ], value_input_option="USER_ENTERED")

def _registrar_baja(sh, caseta, corral_origen, tipo, cantidad, motivo=""):
    ws_bajas = sh.worksheet("bajas")
    ws_bajas.append_row([
        timestamp_ahora(), str(caseta), str(corral_origen),
        tipo, str(cantidad), motivo or ""
    ], value_input_option="USER_ENTERED")

def _actualizar_poblacion(sh, ws_estado, fila_idx, nuevo_valor):
    ws_estado.update_cell(fila_idx, 4, int(nuevo_valor))

# ==================== ACCIONES ====================
def ajustar_calculo(unidad_id, nuevo_valor, motivo=""):
    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    fila_idx, anterior = _buscar_fila_estado(ws_estado, unidad_id)
    if fila_idx is None:
        st.error(f"No se encontró {unidad_id}")
        return False
    if anterior == nuevo_valor:
        st.info("El valor no cambió")
        return False
    _actualizar_poblacion(sh, ws_estado, fila_idx, nuevo_valor)
    _registrar_movimiento(sh, unidad_id, "ajuste_calculo", anterior, nuevo_valor, motivo)
    st.cache_data.clear()
    return True

def añadir_cerdos(unidad_id, cantidad, motivo=""):
    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    fila_idx, anterior = _buscar_fila_estado(ws_estado, unidad_id)
    if fila_idx is None:
        st.error(f"No se encontró {unidad_id}")
        return False
    if cantidad <= 0:
        st.error("La cantidad debe ser mayor a 0")
        return False
    nuevo = anterior + cantidad
    _actualizar_poblacion(sh, ws_estado, fila_idx, nuevo)
    _registrar_movimiento(sh, unidad_id, "recepcion", anterior, nuevo, motivo)
    st.cache_data.clear()
    return True

def mover_entre_corrales(uid_origen, uid_destino, cantidad, motivo=""):
    if cantidad <= 0:
        st.error("La cantidad debe ser mayor a 0")
        return False
    if uid_origen == uid_destino:
        st.error("Origen y destino no pueden ser el mismo corral")
        return False

    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    valores = ws_estado.get_all_values()

    fila_o, ant_o = _buscar_fila_estado(ws_estado, uid_origen, valores)
    fila_d, ant_d = _buscar_fila_estado(ws_estado, uid_destino, valores)

    if fila_o is None or fila_d is None:
        st.error("No se encontró el corral de origen o destino")
        return False
    if cantidad > ant_o:
        st.error(f"No puedes mover {cantidad} cerdos: solo hay {ant_o} en {uid_origen}")
        return False

    corral_o = uid_origen.split("-")[1]
    corral_d = uid_destino.split("-")[1]

    if es_enfermeria(corral_o) and es_enfermeria(corral_d):
        categoria = "mover_enfermeria_enfermeria"
    elif not es_enfermeria(corral_o) and not es_enfermeria(corral_d):
        categoria = "mover_normal"
    else:
        st.error("Origen y destino deben ser del mismo tipo")
        return False

    _actualizar_poblacion(sh, ws_estado, fila_o, ant_o - cantidad)
    _actualizar_poblacion(sh, ws_estado, fila_d, ant_d + cantidad)
    _registrar_movimiento(sh, uid_origen, categoria, ant_o, ant_o - cantidad, motivo)
    _registrar_movimiento(sh, uid_destino, categoria, ant_d, ant_d + cantidad, motivo)

    st.cache_data.clear()
    return True

def mover_a_enfermeria(uid_origen, uid_destino, cantidad, motivo=""):
    if cantidad <= 0:
        st.error("La cantidad debe ser mayor a 0")
        return False

    corral_o = uid_origen.split("-")[1]
    corral_d = uid_destino.split("-")[1]
    if es_enfermeria(corral_o):
        st.error("El origen ya es enfermería")
        return False
    if not es_enfermeria(corral_d):
        st.error("El destino debe ser enfermería (9 o 10)")
        return False

    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    valores = ws_estado.get_all_values()

    fila_o, ant_o = _buscar_fila_estado(ws_estado, uid_origen, valores)
    fila_d, ant_d = _buscar_fila_estado(ws_estado, uid_destino, valores)

    if fila_o is None or fila_d is None:
        st.error("No se encontró el corral")
        return False
    if cantidad > ant_o:
        st.error(f"No puedes mover {cantidad} cerdos: solo hay {ant_o} en {uid_origen}")
        return False

    _actualizar_poblacion(sh, ws_estado, fila_o, ant_o - cantidad)
    _actualizar_poblacion(sh, ws_estado, fila_d, ant_d + cantidad)
    _registrar_movimiento(sh, uid_origen, "mover_enfermeria_normal", ant_o, ant_o - cantidad, motivo)
    _registrar_movimiento(sh, uid_destino, "mover_enfermeria_normal", ant_d, ant_d + cantidad, motivo)

    st.cache_data.clear()
    return True

def recuperar_de_enfermeria(uid_origen, uid_destino, cantidad, motivo=""):
    if cantidad <= 0:
        st.error("La cantidad debe ser mayor a 0")
        return False

    corral_o = uid_origen.split("-")[1]
    corral_d = uid_destino.split("-")[1]
    if not es_enfermeria(corral_o):
        st.error("El origen debe ser enfermería (9 o 10)")
        return False
    if es_enfermeria(corral_d):
        st.error("El destino debe ser un corral normal")
        return False

    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    valores = ws_estado.get_all_values()

    fila_o, ant_o = _buscar_fila_estado(ws_estado, uid_origen, valores)
    fila_d, ant_d = _buscar_fila_estado(ws_estado, uid_destino, valores)

    if fila_o is None or fila_d is None:
        st.error("No se encontró el corral")
        return False
    if cantidad > ant_o:
        st.error(f"No puedes mover {cantidad} cerdos: solo hay {ant_o} en {uid_origen}")
        return False

    _actualizar_poblacion(sh, ws_estado, fila_o, ant_o - cantidad)
    _actualizar_poblacion(sh, ws_estado, fila_d, ant_d + cantidad)
    _registrar_movimiento(sh, uid_origen, "recuperar", ant_o, ant_o - cantidad, motivo)
    _registrar_movimiento(sh, uid_destino, "recuperar", ant_d, ant_d + cantidad, motivo)

    st.cache_data.clear()
    return True

def registrar_baja(unidad_id, tipo, cantidad, motivo=""):
    if tipo not in ("muerto", "sacrificado"):
        st.error("Tipo debe ser 'muerto' o 'sacrificado'")
        return False
    if cantidad <= 0:
        st.error("La cantidad debe ser mayor a 0")
        return False

    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    fila_idx, anterior = _buscar_fila_estado(ws_estado, unidad_id)

    if fila_idx is None:
        st.error(f"No se encontró {unidad_id}")
        return False
    if cantidad > anterior:
        st.error(f"No puedes registrar {cantidad} {tipo}s: solo hay {anterior} en {unidad_id}")
        return False

    nuevo = anterior - cantidad
    _actualizar_poblacion(sh, ws_estado, fila_idx, nuevo)

    caseta, corral = unidad_id.split("-")
    _registrar_movimiento(sh, unidad_id, tipo, anterior, nuevo, motivo)
    _registrar_baja(sh, caseta, corral, tipo, cantidad, motivo)

    st.cache_data.clear()
    return True

# ==================== SNAPSHOTS ====================
def guardar_snapshot(motivo="manual"):
    sh = get_spreadsheet()
    ws_estado = sh.worksheet("estado")
    valores = ws_estado.get_all_values()

    hoy = date.today().isoformat()
    filas = []
    for fila in valores[1:]:
        if fila and fila[0]:
            poblacion = fila[3] if len(fila) > 3 else "0"
            filas.append([hoy, fila[0], poblacion or "0"])

    ws_snap = sh.worksheet("snapshots")
    ws_snap.append_rows(filas, value_input_option="USER_ENTERED")
    return len(filas)

def scheduler_medianoche():
    while True:
        ahora = datetime.now()
        manana = datetime(ahora.year, ahora.month, ahora.day) + \
                 __import__("datetime").timedelta(days=1)
        espera = (manana - ahora).total_seconds()
        time.sleep(espera)
        try:
            guardar_snapshot("automático medianoche")
        except Exception as e:
            print(f"Error en snapshot automático: {e}")

@st.cache_resource
def iniciar_scheduler():
    t = threading.Thread(target=scheduler_medianoche, daemon=True)
    t.start()
    return True

# ==================== LECTURA DE BAJAS ====================
@st.cache_data(ttl=60)
def leer_bajas():
    """Lee la pestaña 'bajas' y devuelve un DataFrame."""
    sh = get_spreadsheet()
    ws = sh.worksheet("bajas")
    valores = ws.get_all_values()
    if len(valores) <= 1:
        return pd.DataFrame(columns=["timestamp", "caseta", "corral_origen",
                                     "tipo", "cantidad", "motivo"])
    df = pd.DataFrame(valores[1:],
                      columns=["timestamp", "caseta", "corral_origen",
                               "tipo", "cantidad", "motivo"])
    # Parsear
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["caseta"] = df["caseta"].astype(str)
    df["corral_origen"] = df["corral_origen"].astype(str)
    df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0).astype(int)
    df["fecha"] = df["timestamp"].dt.date
    return df

# ==================== EDITOR DE CADA CORRAL ====================
def editor_corral(uid, poblacion):
    corral = uid.split("-")[1]
    caseta = uid.split("-")[0]
    en_enf = es_enfermeria(corral)

    st.markdown(f"#### Editando **{uid}** (actual: {poblacion})")
    st.caption(f"Tipo: {'🏥 Enfermería' if en_enf else '🟢 Corral normal'}")

    if en_enf:
        opciones = [
            "🔢 Ajuste de cálculo",
            "🔄 Mover a otra enfermería",
            "➕ Añadir enfermos",
            "✅ Recuperar a corral normal",
            "⚫ Registrar muerte",
            "🔴 Registrar sacrificio",
        ]
    else:
        opciones = [
            "🔢 Ajuste de cálculo",
            "🔄 Mover a otro corral",
            "➕ Añadir cerdos",
            "🏥 Mover a enfermería",
            "⚫ Registrar muerte",
            "🔴 Registrar sacrificio",
        ]

    accion = st.radio("Acción:", opciones, key=f"accion_{uid}")
    motivo = st.text_input("Motivo (opcional)", key=f"motivo_{uid}",
                           placeholder="ej: enfermedad, orden sanitaria...")

    if accion == "🔢 Ajuste de cálculo":
        nuevo = st.number_input("Nuevo total", min_value=0, value=poblacion,
                                key=f"num_{uid}")
        if st.button("💾 Guardar ajuste", key=f"btn_ajuste_{uid}", width="stretch"):
            if ajustar_calculo(uid, int(nuevo), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    elif accion == "➕ Añadir cerdos" or accion == "➕ Añadir enfermos":
        cantidad = st.number_input("Cantidad a añadir", min_value=1, value=1,
                                   step=1, key=f"add_{uid}")
        if st.button("💾 Añadir", key=f"btn_add_{uid}", width="stretch"):
            if añadir_cerdos(uid, int(cantidad), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    elif accion == "🔄 Mover a otro corral" or accion == "🔄 Mover a otra enfermería":
        if en_enf:
            otro = "10" if corral == "9" else "9"
            uid_destino = f"{caseta}-{otro}"
            st.info(f"Destino: **{uid_destino}**")
            destinos = [uid_destino]
        else:
            destinos_posibles = [f"{caseta}-{i}" for i in range(1, 19)
                                 if not es_enfermeria(i) and f"{caseta}-{i}" != uid]
            uid_destino = st.selectbox("Corral destino", destinos_posibles,
                                       key=f"dest_{uid}")
            destinos = [uid_destino]

        cantidad = st.number_input("Cantidad a mover", min_value=1, value=1,
                                   step=1, key=f"mov_{uid}")
        if st.button("💾 Mover", key=f"btn_mov_{uid}", width="stretch"):
            if mover_entre_corrales(uid, destinos[0], int(cantidad), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    elif accion == "🏥 Mover a enfermería":
        uid_destino = st.selectbox(
            "Enfermería destino",
            [f"{caseta}-9", f"{caseta}-10"],
            key=f"dest_enf_{uid}"
        )
        cantidad = st.number_input("Cantidad a mover", min_value=1, value=1,
                                   step=1, key=f"movenf_{uid}")
        if st.button("💾 Mover a enfermería", key=f"btn_movenf_{uid}",
                     width="stretch"):
            if mover_a_enfermeria(uid, uid_destino, int(cantidad), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    elif accion == "✅ Recuperar a corral normal":
        destinos_posibles = [f"{caseta}-{i}" for i in range(1, 19)
                             if not es_enfermeria(i)]
        uid_destino = st.selectbox("Corral destino", destinos_posibles,
                                   key=f"dest_rec_{uid}")
        cantidad = st.number_input("Cantidad a recuperar", min_value=1, value=1,
                                   step=1, key=f"rec_{uid}")
        if st.button("💾 Recuperar", key=f"btn_rec_{uid}", width="stretch"):
            if recuperar_de_enfermeria(uid, uid_destino, int(cantidad), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    elif accion == "⚫ Registrar muerte":
        cantidad = st.number_input("Cantidad de muertos", min_value=1, value=1,
                                   step=1, key=f"muerte_{uid}")
        if st.button("💾 Registrar muerte", key=f"btn_muerte_{uid}",
                     width="stretch"):
            if registrar_baja(uid, "muerto", int(cantidad), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    elif accion == "🔴 Registrar sacrificio":
        cantidad = st.number_input("Cantidad de sacrificados", min_value=1,
                                   value=1, step=1, key=f"sac_{uid}")
        if st.button("💾 Registrar sacrificio", key=f"btn_sac_{uid}",
                     width="stretch"):
            if registrar_baja(uid, "sacrificado", int(cantidad), motivo):
                st.session_state[f"editar_{uid}"] = False
                st.rerun()

    if st.button("❌ Cerrar editor", key=f"cancel_{uid}", width="stretch"):
        st.session_state[f"editar_{uid}"] = False
        st.rerun()

# ==================== RENDER DE CADA CASETA ====================
def render_caseta(caseta):
    st.subheader(f"🏠 Caseta {caseta}")

    filas = [
        ("10", "9"), ("11", "8"), ("12", "7"), ("13", "6"), ("14", "5"),
        ("15", "4"), ("16", "3"), ("17", "2"), ("18", "1"),
    ]

    html = '<div style="display:flex; justify-content:center; padding:10px; background:#eaf4fb; border-radius:12px; border:1px solid #b0c4de;"><table style="border-collapse:separate; border-spacing:0 6px;">'

    for izq_id, der_id in filas:
        uid_izq = f"{caseta}-{izq_id}"
        uid_der = f"{caseta}-{der_id}"
        pob_izq = estado.get(uid_izq, 0)
        pob_der = estado.get(uid_der, 0)

        es_enf = izq_id in ("10", "9")
        ancho = "80px" if es_enf else "130px"
        alto = "50px" if es_enf else "auto"
        bg = "#f5e6c8" if es_enf else "#c9dff0"

        html += (
            f'<tr>'
            f'<td style="width:{ancho}; min-width:{ancho}; height:{alto}; background:{bg}; border:1px solid #7a9cbf; border-radius:4px; text-align:center; vertical-align:middle; padding:6px; font-size:12px; color:#1a3d5c;">'
            f'<b>{uid_izq}</b><br><span style="font-size:16px;">{pob_izq}</span>'
            f'</td>'
            f'<td style="width:60px;"></td>'
            f'<td style="width:{ancho}; min-width:{ancho}; height:{alto}; background:{bg}; border:1px solid #7a9cbf; border-radius:4px; text-align:center; vertical-align:middle; padding:6px; font-size:12px; color:#1a3d5c;">'
            f'<b>{uid_der}</b><br><span style="font-size:16px;">{pob_der}</span>'
            f'</td>'
            f'</tr>'
        )

    html += '</table></div>'
    st.markdown(html, unsafe_allow_html=True)

    with st.expander(f"✏️ Editar corrales de Caseta {caseta}"):
        cols = st.columns(6)
        todos_ids = []
        for izq, der in filas:
            todos_ids.append(f"{caseta}-{izq}")
            todos_ids.append(f"{caseta}-{der}")

        for idx, uid in enumerate(todos_ids):
            with cols[idx % 6]:
                pob = estado.get(uid, 0)
                if st.button(f"{uid} ({pob})", key=f"editbtn_{uid}",
                             width="stretch"):
                    st.session_state[f"editar_{uid}"] = True

        for uid in todos_ids:
            if st.session_state.get(f"editar_{uid}", False):
                pob = estado.get(uid, 0)
                editor_corral(uid, pob)

# ==================== PÁGINA: CONTROL ====================
def pagina_control():
    st.title("🐖 Control de población - Granja")

    with st.spinner("Cargando datos desde Google Sheets..."):
        estado = get_estado()

    total = sum(estado.values())
    col1, col2, col3 = st.columns([2, 1, 1])
    col1.metric("Población total", total)
    if col2.button("💾 Guardar snapshot manual", width="stretch"):
        with st.spinner("Guardando snapshot..."):
            n = guardar_snapshot("manual")
        st.success(f"Snapshot guardado ({n} unidades)")
    if col3.button("🔄 Refrescar", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    for c in range(1, 5):
        render_caseta(c)
        st.markdown("---")

    with st.expander("📜 Historial de movimientos"):
        try:
            sh = get_spreadsheet()
            ws = sh.worksheet("movimientos")
            valores = ws.get_all_values()
            if len(valores) > 1:
                df = pd.DataFrame(valores[1:],
                                  columns=["Fecha", "Unidad", "Categoría",
                                           "Antes", "Después", "Motivo"])
                df["Fecha"] = df["Fecha"].apply(formatear_timestamp)
                df = df.iloc[::-1].head(200)
                st.dataframe(df, width="stretch")
            else:
                st.info("Sin movimientos registrados aún.")
        except Exception as e:
            st.error(f"Error leyendo movimientos: {e}")

    with st.expander("⚫🔴 Historial de bajas"):
        try:
            df = leer_bajas()
            if len(df) > 0:
                df_mostrar = df.copy()
                df_mostrar["timestamp"] = df_mostrar["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
                df_mostrar = df_mostrar.rename(columns={
                    "timestamp": "Fecha", "caseta": "Caseta",
                    "corral_origen": "Corral", "tipo": "Tipo",
                    "cantidad": "Cantidad", "motivo": "Motivo"
                })
                df_mostrar = df_mostrar.iloc[::-1].head(200)
                st.dataframe(df_mostrar[["Fecha", "Caseta", "Corral",
                                         "Tipo", "Cantidad", "Motivo"]],
                             width="stretch")
            else:
                st.info("Sin bajas registradas aún.")
        except Exception as e:
            st.error(f"Error leyendo bajas: {e}")

    with st.expander("📸 Snapshots guardados"):
        try:
            sh = get_spreadsheet()
            ws = sh.worksheet("snapshots")
            valores = ws.get_all_values()
            if len(valores) > 1:
                df = pd.DataFrame(valores[1:],
                                  columns=["Fecha", "Unidad", "Población"])
                resumen = df.groupby("Fecha").agg(
                    Unidades=("Unidad", "count"),
                    Total=("Población",
                           lambda x: pd.to_numeric(x, errors="coerce").sum())
                ).reset_index().sort_values("Fecha", ascending=False)
                st.dataframe(resumen, width="stretch")
            else:
                st.info("Sin snapshots guardados aún.")
        except Exception as e:
            st.error(f"Error leyendo snapshots: {e}")

# ==================== PÁGINA: ESTADÍSTICAS ====================
def preparar_datos_muertes(df_bajas, dias=None):
    """
    Prepara datos de muertes agrupados por día y caseta.
    Devuelve un DataFrame con columnas: fecha, caseta, cantidad.
    """
    df = df_bajas[df_bajas["tipo"] == "muerto"].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=["fecha", "caseta", "cantidad"])

    if dias is not None:
        corte = pd.Timestamp(date.today() - timedelta(days=dias-1))
        df = df[df["timestamp"] >= corte]

    # Agrupar por fecha + caseta
    agrupado = df.groupby(["fecha", "caseta"])["cantidad"].sum().reset_index()

    # Rellenar días faltantes con 0
    if len(agrupado) > 0:
        min_fecha = agrupado["fecha"].min()
        max_fecha = agrupado["fecha"].max()
        rango_fechas = pd.date_range(min_fecha, max_fecha, freq="D").date
        casetas = ["1", "2", "3", "4"]

        completo = []
        for f in rango_fechas:
            for c in casetas:
                completo.append({"fecha": f, "caseta": c, "cantidad": 0})
        completo = pd.DataFrame(completo)

        agrupado = completo.merge(agrupado, on=["fecha", "caseta"],
                                  how="left", suffixes=("", "_y"))
        agrupado["cantidad"] = agrupado["cantidad_y"].fillna(agrupado["cantidad"]).fillna(0).astype(int)
        agrupado = agrupado[["fecha", "caseta", "cantidad"]]

    return agrupado

def grafica_caseta(df, caseta, modo):
    """Gráfica de línea para una caseta individual."""
    df_c = df[df["caseta"] == caseta].sort_values("fecha").copy()
    if modo == "Acumulado":
        df_c["valor"] = df_c["cantidad"].cumsum()
    else:
        df_c["valor"] = df_c["cantidad"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_c["fecha"], y=df_c["valor"],
        mode="lines+markers",
        line=dict(color="#c0392b", width=2),
        marker=dict(size=6),
        fill="tozeroy",
        fillcolor="rgba(192, 57, 43, 0.15)",
        name=f"Caseta {caseta}"
    ))
    fig.update_layout(
        title=f"Caseta {caseta}",
        xaxis_title="Día",
        yaxis_title="Muertos" + (" acumulados" if modo == "Acumulado" else " por día"),
        height=300,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=False
    )
    return fig

def grafica_total_stacked(df, modo):
    """Gráfica de área apilada con las 4 casetas."""
    if len(df) == 0:
        return None

    pivote = df.pivot_table(index="fecha", columns="caseta",
                            values="cantidad", aggfunc="sum").fillna(0)
    pivote = pivote.sort_index()

    if modo == "Acumulado":
        pivote = pivote.cumsum()

    colores = {"1": "#3498db", "2": "#2ecc71", "3": "#f39c12", "4": "#9b59b6"}

    fig = go.Figure()
    for caseta in ["1", "2", "3", "4"]:
        if caseta in pivote.columns:
            fig.add_trace(go.Scatter(
                x=pivote.index, y=pivote[caseta],
                mode="lines",
                stackgroup="one",
                name=f"Caseta {caseta}",
                line=dict(width=0.5, color=colores[caseta]),
                fillcolor=colores[caseta]
            ))
    fig.update_layout(
        title="🏠 Total granja (aportación por caseta)",
        xaxis_title="Día",
        yaxis_title="Muertos" + (" acumulados" if modo == "Acumulado" else " por día"),
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

def pagina_estadisticas():
    st.title("📊 Estadísticas")

    # Filtros
    col_f1, col_f2 = st.columns([1, 1])
    with col_f1:
        dias = st.selectbox("Rango:", [7, 14, 30, 90, "Todo"],
                            index=2, key="filtro_dias")
    with col_f2:
        modo = st.radio("Modo:", ["Diarios", "Acumulado"],
                        horizontal=True, key="filtro_modo")

    st.markdown("---")

    # Solo muertos por ahora
    st.subheader("⚫ Muertes")

    with st.spinner("Cargando datos..."):
        df_bajas = leer_bajas()

    if len(df_bajas) == 0:
        st.info("Aún no hay datos de bajas registrados. Registra muertes desde la página de Control.")
        return

    dias_num = None if dias == "Todo" else int(dias)
    df_muertes = preparar_datos_muertes(df_bajas, dias_num)

    if len(df_muertes) == 0:
        st.info("No hay muertes registradas en el rango seleccionado.")
        return

    # Métricas rápidas
    total_rango = df_muertes["cantidad"].sum()
    col1, col2, col3 = st.columns(3)
    col1.metric("Muertes en el rango", total_rango)
    col2.metric("Días con datos", df_muertes["fecha"].nunique())
    col3.metric("Promedio diario", round(total_rango / max(df_muertes["fecha"].nunique(), 1), 2))

    st.markdown("---")

    # 4 gráficas individuales (2x2)
    st.subheader("Por caseta")
    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(grafica_caseta(df_muertes, "1", modo),
                        use_container_width=True)
        st.plotly_chart(grafica_caseta(df_muertes, "3", modo),
                        use_container_width=True)
    with col_b:
        st.plotly_chart(grafica_caseta(df_muertes, "2", modo),
                        use_container_width=True)
        st.plotly_chart(grafica_caseta(df_muertes, "4", modo),
                        use_container_width=True)

    st.markdown("---")

    # Gráfica total stacked
    st.subheader("Total granja")
    fig_total = grafica_total_stacked(df_muertes, modo)
    if fig_total:
        st.plotly_chart(fig_total, use_container_width=True)

# ==================== NAVEGACIÓN ====================
st.set_page_config(page_title="Granja de cerdos", layout="wide")

# Inicialización (solo una vez)
if "init_done" not in st.session_state:
    init_hojas()
    iniciar_scheduler()
    st.session_state["init_done"] = True

# Sidebar
with st.sidebar:
    st.title("🐖 Granja")
    st.markdown("---")
    pagina = st.radio("Navegación:", ["🏠 Control", "📊 Estadísticas"],
                      key="nav_principal")
    st.markdown("---")
    st.caption("Sistema de control de población porcina")

# Render de la página seleccionada
if pagina == "🏠 Control":
    pagina_control()
else:
    pagina_estadisticas()
