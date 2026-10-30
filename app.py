"""
Robot → STRAP
Converte tabelas de reações/ações exportadas do Autodesk Robot (CSV, TXT ou Excel)
em comandos de carregamento nodal do STRAP.

Executar com:  streamlit run app.py
"""

import csv
import hashlib
import io
import math
import re

import numpy as np
import pandas as pd
import streamlit as st

# =============================================================================
# Unidades
# =============================================================================
# Quantas unidades cabem em 1 kN / 1 m (valores exatos a partir das definições)
UNIDADES_FORCA = {
    "kN": 1.0,
    "kgf": 1000 / 9.80665,
    "tf": 1 / 9.80665,
    "lbf": 1000 / 4.4482216152605,
    "kip": 1 / 4.4482216152605,
}
UNIDADES_COMP = {
    "m": 1.0,
    "cm": 100.0,
    "mm": 1000.0,
    "ft": 1 / 0.3048,
    "in": 1 / 0.0254,
}

COMPONENTES = ["FX", "FY", "FZ", "MX", "MY", "MZ"]
EIXOS = {"X": 0, "Y": 1, "Z": 2}
OPCOES_EIXO = ["X", "-X", "Y", "-Y", "Z", "-Z"]

RE_COMPONENTE = re.compile(r"^\s*([FM])\s*([XYZ])(?![A-Za-z])", re.IGNORECASE)
RE_NO = re.compile(r"^\s*(node|n[óo])\b", re.IGNORECASE)
RE_CASO = re.compile(r"^\s*(case|caso|load)", re.IGNORECASE)


class ErroLeitura(Exception):
    """Erro de leitura com mensagem pronta para mostrar ao usuário."""


def conversor_forca(unidade_entrada, unidade_saida):
    """Multiplicador de conversão de força."""
    return UNIDADES_FORCA[unidade_saida] / UNIDADES_FORCA[unidade_entrada]


def conversor_distancia(unidade_entrada, unidade_saida):
    """Multiplicador de conversão de comprimento."""
    return UNIDADES_COMP[unidade_saida] / UNIDADES_COMP[unidade_entrada]


def conversor_momento(forca_entrada, forca_saida, dist_entrada, dist_saida):
    """Multiplicador de conversão de momento ([força]*[comprimento])."""
    return conversor_forca(forca_entrada, forca_saida) * conversor_distancia(dist_entrada, dist_saida)


# =============================================================================
# Leitura do arquivo -> lista de linhas (cada linha é uma lista de células texto)
# =============================================================================
def decodificar(dados: bytes) -> str:
    """Detecta a codificação do arquivo de texto (BOM primeiro, depois tentativas)."""
    if dados.startswith(b"\xef\xbb\xbf"):
        return dados.decode("utf-8-sig")
    if dados.startswith((b"\xff\xfe", b"\xfe\xff")):
        return dados.decode("utf-16")
    amostra = dados[:4000]
    if amostra and amostra.count(0) > len(amostra) // 4:  # UTF-16 sem BOM
        zeros_pares, zeros_impares = amostra[0::2].count(0), amostra[1::2].count(0)
        encoding = "utf-16-le" if zeros_impares >= zeros_pares else "utf-16-be"
        return dados.decode(encoding, errors="replace")
    for encoding in ("utf-8", "cp1252"):
        try:
            return dados.decode(encoding)
        except UnicodeDecodeError:
            continue
    return dados.decode("latin1")  # latin1 nunca falha


def detectar_separador(texto: str) -> str:
    linhas = [l for l in texto.splitlines()[:100] if l.strip()]
    ponto_virgula = sum(l.count(";") for l in linhas)
    tabulacao = sum(l.count("\t") for l in linhas)
    if ponto_virgula or tabulacao:
        return ";" if ponto_virgula >= tabulacao else "\t"
    if any("," in l for l in linhas):
        return ","
    return ";"


def linhas_de_texto(dados: bytes):
    texto = decodificar(dados)
    separador = detectar_separador(texto)
    return list(csv.reader(io.StringIO(texto, newline=""), delimiter=separador))


def celula_para_texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and math.isnan(valor):
        return ""
    return str(valor).strip()


def linhas_de_excel(dados: bytes, aba):
    df = pd.read_excel(io.BytesIO(dados), sheet_name=aba, header=None, dtype=object)
    return [[celula_para_texto(v) for v in linha] for linha in df.itertuples(index=False)]


@st.cache_data(show_spinner=False)
def listar_abas(dados: bytes):
    try:
        return pd.ExcelFile(io.BytesIO(dados)).sheet_names
    except ImportError:
        raise
    except Exception:
        return []


# =============================================================================
# Interpretação da tabela
# =============================================================================
def para_float(valor):
    """Converte texto em número aceitando vírgula ou ponto decimal. Retorna None se falhar."""
    s = str(valor).strip().replace("\u00a0", "").replace(" ", "").strip('"')
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:  # 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        numero = float(s)
    except ValueError:
        return None
    return numero if math.isfinite(numero) else None


def para_int(valor):
    numero = para_float(valor)
    if numero is None or not numero.is_integer():
        return None
    return int(numero)


def componente(celula: str):
    m = RE_COMPONENTE.match(celula or "")
    return (m.group(1) + m.group(2)).upper() if m else None


def expandir(linha, divisoes):
    """Divide em várias colunas as células do tipo 'Node/Case' (divisoes = {coluna: nº de partes})."""
    saida = []
    for j, celula in enumerate(linha):
        celula = (celula or "").strip().strip('"').strip()
        n = divisoes.get(j)
        if n:
            partes = [p.strip() for p in celula.split("/", n - 1)]
            partes += [""] * (n - len(partes))
            saida.extend(partes)
        else:
            saida.append(celula)
    return saida


def pegar(celulas, j):
    return celulas[j] if j is not None and j < len(celulas) else ""


def localizar_cabecalho(linhas):
    """Primeira linha que contém FX, FY, FZ, MX, MY e MZ."""
    for i, linha in enumerate(linhas):
        celulas = [(c or "").strip().strip('"').strip() for c in linha]
        if set(COMPONENTES) <= {componente(c) for c in celulas}:
            divisoes = {
                j: len(c.split("/"))
                for j, c in enumerate(celulas)
                if "/" in c and not componente(c)
            }
            return i, divisoes
    raise ErroLeitura(
        "Não encontrei o cabeçalho da tabela. O arquivo precisa ter uma linha com as "
        "colunas FX, FY, FZ, MX, MY e MZ (ex.: 'FX [kN]')."
    )


def unidade_do_cabecalho(texto):
    m = re.search(r"[\[\(]\s*([^\]\)]+?)\s*[\]\)]", texto or "")
    return m.group(1) if m else None


def identificar_unidade_forca(unidade):
    if not unidade:
        return None
    for chave in UNIDADES_FORCA:
        if unidade.strip().lower() == chave.lower():
            return chave
    return None


def identificar_unidade_momento(unidade):
    """'kNm', 'kN*m', 'tf.m', 'kgf·cm' ... -> (força, comprimento)"""
    if not unidade:
        return None
    s = re.sub(r"[\s\*·\.\-×]", "", unidade).lower()
    for forca in sorted(UNIDADES_FORCA, key=len, reverse=True):
        if s.startswith(forca.lower()):
            resto = s[len(forca):]
            for comp in UNIDADES_COMP:
                if resto == comp.lower():
                    return forca, comp
    return None


def extrair_dados(linhas):
    """Extrai a tabela de reações/ações de uma lista de linhas.

    A leitura começa logo após o cabeçalho e termina na PRIMEIRA linha cujo número
    de nó não seja um inteiro (linha vazia, 'Sum of val.', 'Max', outra tabela...).
    Assim não depende de existir uma linha em branco no fim da tabela.
    """
    i_cab, divisoes = localizar_cabecalho(linhas)
    cabecalho = expandir(linhas[i_cab], divisoes)

    idx = {}
    for j, c in enumerate(cabecalho):
        comp = componente(c)
        if comp and comp not in idx:
            idx[comp] = j

    idx_no = next((j for j, c in enumerate(cabecalho) if RE_NO.match(c)), 0)
    idx_caso = next((j for j, c in enumerate(cabecalho) if RE_CASO.match(c)), None)
    if idx_caso is None and idx_no + 1 < len(cabecalho) and (idx_no + 1) not in idx.values():
        idx_caso = idx_no + 1

    avisos = []
    registros = []
    linhas_com_vazio = []
    parada = None

    for i in range(i_cab + 1, len(linhas)):
        celulas = expandir(linhas[i], divisoes)
        no = para_int(pegar(celulas, idx_no))
        brutos = {comp: pegar(celulas, idx[comp]) for comp in COMPONENTES}

        if no is None or all(v == "" for v in brutos.values()):
            if registros:
                parada = i
                break
            continue  # linhas entre o cabeçalho e os dados (unidades, separadores...)

        valores = {}
        for comp, bruto in brutos.items():
            if bruto == "":
                valores[comp] = 0.0
                linhas_com_vazio.append(i + 1)
                continue
            v = para_float(bruto)
            if v is None:
                raise ErroLeitura(
                    f"Linha {i + 1} do arquivo: o valor de {comp} do nó {no} não é numérico ('{bruto}')."
                )
            valores[comp] = v

        caso = pegar(celulas, idx_caso) if idx_caso is not None else ""
        caso_num = para_float(caso)
        if caso_num is not None and caso_num.is_integer():
            caso = str(int(caso_num))
        registros.append({"Node": no, "Case": caso or "1", **valores})

    if not registros:
        raise ErroLeitura(
            f"O cabeçalho foi encontrado na linha {i_cab + 1}, mas nenhuma linha abaixo dele "
            "tem um número de nó válido."
        )

    df = pd.DataFrame(registros, columns=["Node", "Case"] + COMPONENTES)

    if linhas_com_vazio:
        linhas_txt = ", ".join(str(n) for n in sorted(set(linhas_com_vazio))[:10])
        avisos.append(f"Células vazias foram consideradas zero (linhas {linhas_txt}...).")
    duplicados = df[df.duplicated(["Node", "Case"], keep=False)]
    if not duplicados.empty:
        pares = ", ".join(f"nó {n}/caso {c}" for n, c in duplicados[["Node", "Case"]].drop_duplicates().values[:10])
        avisos.append(f"Há pares nó/caso repetidos ({pares}). Verifique se o arquivo não tem tabelas duplicadas.")

    info = {
        "linha_cabecalho": i_cab + 1,
        "linha_parada": None if parada is None else parada + 1,
        "conteudo_parada": "" if parada is None else " | ".join(c for c in linhas[parada] if c and c.strip())[:120],
        "unidade_forca": identificar_unidade_forca(unidade_do_cabecalho(pegar(cabecalho, idx["FX"]))),
        "unidade_momento": identificar_unidade_momento(unidade_do_cabecalho(pegar(cabecalho, idx["MX"]))),
        "avisos": avisos,
    }
    return df, info


@st.cache_data(show_spinner=False)
def ler_tabela_robot(dados: bytes, eh_excel: bool, aba):
    if not eh_excel:
        return extrair_dados(linhas_de_texto(dados))
    try:
        return extrair_dados(linhas_de_excel(dados, aba))
    except (ImportError, ErroLeitura):
        raise
    except Exception as erro_excel:
        # Alguns programas salvam CSV/texto com extensão .xls
        try:
            df, info = extrair_dados(linhas_de_texto(dados))
        except ErroLeitura:
            raise ErroLeitura(f"Não foi possível abrir o arquivo Excel ({erro_excel}).") from None
        info["avisos"].insert(0, "O arquivo tem extensão de Excel, mas foi lido como texto.")
        return df, info


# =============================================================================
# Conversão
# =============================================================================
def matriz_eixos(x1, x2, x3):
    """Linha i = eixo do Robot correspondente ao eixo Xi do STRAP."""
    R = np.zeros((3, 3))
    for i, eixo in enumerate((x1, x2, x3)):
        R[i, EIXOS[eixo[-1]]] = -1.0 if eixo.startswith("-") else 1.0
    return R


def formatar(valor, casas):
    texto = f"{valor:.{casas}f}"
    return "0" if float(texto) == 0 else texto  # evita '-0.000'


def converter(df_robot, R, eh_reacao, mult_forca, mult_momento, mapa_nos, casas):
    # Reação no apoio do Robot = carga aplicada (com sinal trocado) no modelo do STRAP
    sinal = -1.0 if eh_reacao else 1.0
    F = df_robot[["FX", "FY", "FZ"]].to_numpy(float) @ R.T * sinal * mult_forca
    M = df_robot[["MX", "MY", "MZ"]].to_numpy(float) @ R.T * sinal * mult_momento

    df = pd.DataFrame({
        "Node Robot": df_robot["Node"].to_numpy(),
        "Node STRAP": df_robot["Node"].map(mapa_nos).to_numpy(),
        "Case": df_robot["Case"].to_numpy(),
    })
    for k in range(3):
        df[f"FX{k + 1}"] = F[:, k]
    for k in range(3):
        df[f"MX{k + 1}"] = M[:, k]

    colunas = ["FX1", "FX2", "FX3", "MX1", "MX2", "MX3"]
    df["Comando"] = [
        " ".join(f"{c} {formatar(linha[c], casas)}" for c in colunas) + f" N {int(linha['Node STRAP'])}"
        for _, linha in df.iterrows()
    ]
    df[colunas] = df[colunas].round(casas)
    return df


def nome_aba(nome, usados):
    base = re.sub(r"[\[\]\:\*\?\/\\]", "_", str(nome))[:31] or "Aba"
    nome_final, n = base, 2
    while nome_final.lower() in {u.lower() for u in usados}:
        sufixo = f" ({n})"
        nome_final = base[: 31 - len(sufixo)] + sufixo
        n += 1
    usados.add(nome_final)
    return nome_final


def gerar_excel(df_strap, df_robot, max_abas_por_caso=50):
    buffer = io.BytesIO()
    usados = {"STRAP", "Robot (lido)"}
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_strap.to_excel(writer, sheet_name="STRAP", index=False)
        casos = df_strap["Case"].unique()
        if len(casos) <= max_abas_por_caso:
            for caso in casos:
                df_strap[df_strap["Case"] == caso].to_excel(
                    writer, sheet_name=nome_aba(f"Caso {caso}", usados), index=False
                )
        df_robot.to_excel(writer, sheet_name="Robot (lido)", index=False)
    return buffer.getvalue()


def indice(lista, valor, padrao=0):
    return lista.index(valor) if valor in lista else padrao


# =============================================================================
# Interface
# =============================================================================
def main():
    st.set_page_config(page_title="Robot → STRAP", layout="wide")
    st.title("Robot → STRAP")
    st.subheader("Conversão de reações e ações do Robot para comandos do STRAP")

    arquivo = st.file_uploader(
        "Selecione o arquivo exportado do Robot",
        type=["csv", "txt", "xlsx", "xlsm", "xls"],
    )
    if arquivo is None:
        st.stop()

    dados = arquivo.getvalue()
    if not dados:
        st.error("O arquivo está vazio.")
        st.stop()
    hash_arquivo = hashlib.md5(dados).hexdigest()[:10]
    eh_excel = arquivo.name.lower().endswith((".xlsx", ".xlsm", ".xls"))

    # ---- Leitura -------------------------------------------------------------
    aba = None
    try:
        if eh_excel:
            abas = listar_abas(dados)
            if len(abas) > 1:
                aba = st.selectbox("Aba da planilha", abas)
            else:
                aba = abas[0] if abas else 0
        df_robot, info = ler_tabela_robot(dados, eh_excel, aba)
    except ImportError:
        st.error(
            "Falta uma biblioteca para ler este tipo de Excel. Para .xlsx instale `openpyxl`; "
            "para .xls antigo instale `xlrd` (ou salve o arquivo como .xlsx)."
        )
        st.stop()
    except ErroLeitura as erro:
        st.error(str(erro))
        st.stop()
    except Exception as erro:
        st.error(f"Erro inesperado ao ler o arquivo: {erro}")
        st.stop()

    st.success(
        f"{len(df_robot)} linhas lidas: {df_robot['Node'].nunique()} nós, "
        f"{df_robot['Case'].nunique()} caso(s)."
    )
    if info["linha_parada"] is None:
        fim = "a leitura foi até o fim do arquivo"
    elif info["conteudo_parada"]:
        fim = f"a leitura parou na linha {info['linha_parada']} (“{info['conteudo_parada']}”)"
    else:
        fim = f"a leitura parou na linha {info['linha_parada']} (linha em branco)"
    st.caption(f"Cabeçalho encontrado na linha {info['linha_cabecalho']}; {fim}.")
    for aviso in info["avisos"]:
        st.warning(aviso)

    with st.expander("Ver dados lidos do Robot"):
        st.dataframe(df_robot, hide_index=True)

    # ---- Eixos ---------------------------------------------------------------
    col1, col2, col3 = st.columns(3)
    with col1:
        x1 = st.selectbox("X1 do STRAP corresponde a", OPCOES_EIXO, index=0)
    with col2:
        x2 = st.selectbox("X2 do STRAP corresponde a", OPCOES_EIXO, index=2)
    with col3:
        x3 = st.selectbox("X3 do STRAP corresponde a", OPCOES_EIXO, index=4)

    R = matriz_eixos(x1, x2, x3)
    det = round(np.linalg.det(R))
    if det == 0:
        st.error("Cada eixo do Robot (X, Y e Z) deve ser usado exatamente uma vez.")
        st.stop()
    if det < 0:
        st.error(
            "Essa combinação inverte o sistema de eixos (fica levógiro): as forças até "
            "seriam transformadas, mas os momentos sairiam com o sinal errado. "
            "Troque o sinal de um dos eixos."
        )
        st.stop()

    # ---- Tipo de planilha ----------------------------------------------------
    tipo_planilha = st.radio(
        "A planilha do Robot é de:",
        ["REACTIONS", "LOADS"],
        format_func=lambda x: {"REACTIONS": "Reações", "LOADS": "Ações"}[x],
        horizontal=True,
    )
    eh_reacao = tipo_planilha == "REACTIONS"

    # ---- Unidades ------------------------------------------------------------
    lista_forca = list(UNIDADES_FORCA)
    lista_comp = list(UNIDADES_COMP)
    uf_detectada = info["unidade_forca"]
    um_detectada = info["unidade_momento"] or (None, None)
    if uf_detectada or info["unidade_momento"]:
        st.caption(
            "Unidades lidas do cabeçalho: "
            f"força {uf_detectada or '?'}, momento "
            f"{um_detectada[0] + '·' + um_detectada[1] if info['unidade_momento'] else '?'}."
        )

    col1, col2 = st.columns(2)
    with col1:
        uf_robot = st.selectbox("Força no Robot", lista_forca, index=indice(lista_forca, uf_detectada))
    with col2:
        uf_strap = st.selectbox("Força no STRAP", lista_forca, index=0)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        umf_robot = st.selectbox("Momento no Robot (força)", lista_forca,
                                 index=indice(lista_forca, um_detectada[0]), key="mom_f_robot")
    with col2:
        umc_robot = st.selectbox("Momento no Robot (comprimento)", lista_comp,
                                 index=indice(lista_comp, um_detectada[1]), key="mom_c_robot")
    with col3:
        umf_strap = st.selectbox("Momento no STRAP (força)", lista_forca, index=0, key="mom_f_strap")
    with col4:
        umc_strap = st.selectbox("Momento no STRAP (comprimento)", lista_comp, index=0, key="mom_c_strap")

    casas = st.number_input("Casas decimais nos comandos", min_value=0, max_value=8, value=3, step=1)

    # ---- Correspondência de nós ----------------------------------------------
    nos = sorted(int(n) for n in df_robot["Node"].unique())
    nodes_iguais = st.checkbox(
        "A numeração dos nós do Robot corresponde à numeração dos nós do STRAP", value=True
    )
    if nodes_iguais:
        mapa_nos = {n: n for n in nos}
    else:
        df_mapa = st.data_editor(
            pd.DataFrame({"Node Robot": nos, "Node STRAP": nos}),
            num_rows="fixed",
            disabled=["Node Robot"],
            hide_index=True,
            key=f"mapa_nos_{hash_arquivo}",
        )
        nos_strap = pd.to_numeric(df_mapa["Node STRAP"], errors="coerce")
        invalidos = df_mapa.loc[nos_strap.isna() | (nos_strap % 1 != 0) | (nos_strap <= 0), "Node Robot"]
        if not invalidos.empty:
            st.error(
                "Informe um número de nó do STRAP (inteiro positivo) para os nós do Robot: "
                + ", ".join(str(n) for n in invalidos.tolist()[:20])
            )
            st.stop()
        repetidos = nos_strap[nos_strap.duplicated(keep=False)]
        if not repetidos.empty:
            st.warning(
                "Mais de um nó do Robot aponta para o mesmo nó do STRAP "
                f"({', '.join(str(int(n)) for n in sorted(set(repetidos))[:20])}): as cargas serão somadas lá."
            )
        mapa_nos = dict(zip(df_mapa["Node Robot"].astype(int), nos_strap.astype(int)))

    # ---- Conversão -----------------------------------------------------------
    assinatura = (
        hash_arquivo, aba, x1, x2, x3, eh_reacao, uf_robot, uf_strap,
        umf_robot, umc_robot, umf_strap, umc_strap, int(casas),
        tuple(sorted((int(k), int(v)) for k, v in mapa_nos.items())),
    )

    if st.button("Realizar a conversão", type="primary"):
        df_strap = converter(
            df_robot, R, eh_reacao,
            conversor_forca(uf_robot, uf_strap),
            conversor_momento(umf_robot, umf_strap, umc_robot, umc_strap),
            mapa_nos, int(casas),
        )
        st.session_state["resultado"] = {
            "assinatura": assinatura,
            "df": df_strap,
            "excel": gerar_excel(df_strap, df_robot),
        }

    resultado = st.session_state.get("resultado")
    if resultado is None:
        st.stop()
    if resultado["assinatura"] != assinatura:
        st.info("O arquivo ou as opções mudaram desde a última conversão. Clique em “Realizar a conversão” novamente.")
        st.stop()

    df_strap = resultado["df"]
    st.success("Conversão realizada com sucesso")

    st.subheader("Tabela convertida para STRAP")
    st.dataframe(df_strap, hide_index=True)

    st.subheader("Somatório por caso (conferência com o Robot)")
    st.dataframe(
        df_strap.groupby("Case", sort=False)[["FX1", "FX2", "FX3", "MX1", "MX2", "MX3"]].sum().round(int(casas)),
    )

    st.subheader("Comandos por caso")
    casos = list(df_strap["Case"].unique())
    caso = st.selectbox("Caso", casos)
    st.code("\n".join(df_strap.loc[df_strap["Case"] == caso, "Comando"]), language=None)

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Baixar planilha STRAP (.xlsx)",
            data=resultado["excel"],
            file_name="saida_strap.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col2:
        st.download_button(
            "Baixar comandos do caso selecionado (.txt)",
            data="\n".join(df_strap.loc[df_strap["Case"] == caso, "Comando"]) + "\n",
            file_name=f"comandos_caso_{re.sub(r'[^0-9A-Za-z_-]', '_', str(caso))}.txt",
            mime="text/plain",
        )


if __name__ == "__main__":
    main()
