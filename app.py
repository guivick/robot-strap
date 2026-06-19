# Importação das bibliotecas

import pandas as pd
import streamlit as st
import io

# Funções auxiliares
def conversor_forca(unidade_entrada, unidade_saida):
    """
    retorna o multiplicador de conversão entre unidade_entrada e unidade_saida
    """
    multiplicador = {"kN": 1, "kgf": 101.9716, "tf": 0.1019716, "lbf": 224.8089, "kip": 0.2248089}
    forca_kN = 1/multiplicador[unidade_entrada]
    return forca_kN * multiplicador[unidade_saida]

def conversor_distancia(unidade_entrada, unidade_saida):
    """
    retorna o multiplicador de conversão entre unidade_entrada e unidade_saida
    """
    multiplicador = {"m": 1, "cm": 100, "ft": 3.28083, "in": 39.36996}
    dist_m = 1/multiplicador[unidade_entrada]
    return dist_m * multiplicador[unidade_saida]

def conversor_momento(unidade_entrada_forca, unidade_saida_forca, unidade_entrada_dist, unidade_saida_dist):
    """
    Retorna o multiplicador de conversão de momento entre:
    [unidade_entrada_forca]*[unidade_entrada_dist] e [unidade_saida_forca]*[unidade_saida_dist]
    """
    return conversor_forca(unidade_entrada_forca, unidade_saida_forca)*conversor_distancia(unidade_entrada_dist, unidade_saida_dist)


# Leitura do arquivo

# Upload do arquivo com Streamlit
st.title("Robot → STRAP")
st.subheader("Conversão de reações e ações do Robot para comandos do STRAP")
arquivo = st.file_uploader(
    "Selecione o arquivo exportado do Robot",
    type=["csv", "txt"]
)

if arquivo is None:
    st.stop()

arquivo.seek(0)

for i, linha in enumerate(
    arquivo.getvalue().decode("utf-16").splitlines()
):
    if linha.strip("; \n\r\t") == "":
        linha_vazia = i
        break

arquivo.seek(0)

df_robot = pd.read_csv(arquivo, encoding="utf-16", sep=r'[;/]', nrows=linha_vazia-1, engine='python')

# Transformação de coordenadas e unidades
# Coordenadas
opcoes_eixo = ["X", "-X", "Y", "-Y", "Z", "-Z"]

col1, col2, col3 = st.columns(3)

with col1:
    X1_STRAP = st.selectbox(
        "X1 do STRAP corresponde a",
        opcoes_eixo,
        index=0
    )

with col2:
    X2_STRAP = st.selectbox(
        "X2 do STRAP corresponde a",
        opcoes_eixo,
        index=2
    )

with col3:
    X3_STRAP = st.selectbox(
        "X3 do STRAP corresponde a",
        opcoes_eixo,
        index=4
    )

# Informa se é ação ou reação
tipo_planilha = st.radio(
    "A planilha do Robot é de:",
    ["REACTIONS", "LOADS"],
    format_func=lambda x: {
        "REACTIONS": "Reações",
        "LOADS": "Ações"
    }[x],
    index=0,
    horizontal=True
)

if tipo_planilha == "REACTIONS":
    tipo_planilha = 1
else:
    tipo_planilha = -1


unidades_forca = ["kN", "kgf", "tf", "lbf", "kip"]
unidades_comp = ["m", "cm", "ft", "in"]

col1, col2 = st.columns(2)

with col1:
    unidade_forca_robot = st.selectbox(
        "Força no Robot",
        unidades_forca,
        index=0
    )

with col2:
    unidade_forca_strap = st.selectbox(
        "Força no STRAP",
        unidades_forca,
        index=0
    )

col1, col2, col3, col4 = st.columns(4)

with col1:
    unidade_momento_forca_robot = st.selectbox(
        "Momento no Robot",
        unidades_forca,
        index=0,
        key="mom_f_robot"
    )

with col2:
    unidade_momento_comp_robot = st.selectbox(
        "",
        unidades_comp,
        index=0,
        key="mom_c_robot"
    )

with col3:
    unidade_momento_forca_strap = st.selectbox(
        "Momento no STRAP",
        unidades_forca,
        index=0,
        key="mom_f_strap"
    )

with col4:
    unidade_momento_comp_strap = st.selectbox(
        "",
        unidades_comp,
        index=0,
        key="mom_c_strap"
    )

# Correspondência entre nós Robot / STRAP
nodes_iguais = st.checkbox(
    "A numeração dos nós do Robot corresponde à numeração dos nós do STRAP",
    value=True
)
df_correspond_nodes = pd.DataFrame()

if nodes_iguais:

    df_correspond_nodes = pd.DataFrame(
        {
            "Node Robot": sorted(df_robot["Node"].unique())
        }
    )

    df_correspond_nodes["Node STRAP"] = df_correspond_nodes["Node Robot"]
    
else: # Abre uma planilha
    df_correspond_nodes = pd.DataFrame(
    {
        "Node Robot": sorted(df_robot["Node"].unique()),
        "Node STRAP": sorted(df_robot["Node"].unique())
    }
)

    df_correspond_nodes = st.data_editor(
        df_correspond_nodes,
        use_container_width=True,
        num_rows="fixed"
    )

calcular = st.button("Realizar a conversão")

if calcular:
    if arquivo is None:
        st.error("Selecione um arquivo")
        st.stop()

    df_strap = pd.DataFrame()

    df_strap["Node Robot"] = df_robot["Node"]

    df_strap["Node STRAP"] = df_strap["Node Robot"].map(df_correspond_nodes.set_index("Node Robot")["Node STRAP"])

    df_strap["Case"] = df_robot["Case"]

    multiplicador_forca = conversor_forca(unidade_forca_robot, unidade_forca_strap)
    multiplicador_momento = conversor_momento(unidade_momento_forca_robot, unidade_momento_forca_strap, unidade_momento_comp_robot, unidade_momento_comp_strap)

    #X1:
    if X1_STRAP == "X":
        df_strap["FX1"] = -tipo_planilha*df_robot[df_robot.columns[2]]*multiplicador_forca
        df_strap["MX1"] = -tipo_planilha*df_robot[df_robot.columns[5]]*multiplicador_momento
    elif X1_STRAP == "-X":
        df_strap["FX1"] = tipo_planilha*df_robot[df_robot.columns[2]]*multiplicador_forca
        df_strap["MX1"] = tipo_planilha*df_robot[df_robot.columns[5]]*multiplicador_momento
    elif X1_STRAP == "Y":
        df_strap["FX1"] = -tipo_planilha*df_robot[df_robot.columns[3]]*multiplicador_forca
        df_strap["MX1"] = -tipo_planilha*df_robot[df_robot.columns[6]]*multiplicador_momento
    elif X1_STRAP == "-Y":
        df_strap["FX1"] = tipo_planilha*df_robot[df_robot.columns[3]]*multiplicador_forca
        df_strap["MX1"] = tipo_planilha*df_robot[df_robot.columns[6]]*multiplicador_momento
    elif X1_STRAP == "Z":
        df_strap["FX1"] = -tipo_planilha*df_robot[df_robot.columns[4]]*multiplicador_forca
        df_strap["MX1"] = -tipo_planilha*df_robot[df_robot.columns[7]]*multiplicador_momento
    elif X1_STRAP == "-Z":
        df_strap["FX1"] = tipo_planilha*df_robot[df_robot.columns[4]]*multiplicador_forca
        df_strap["MX1"] = tipo_planilha*df_robot[df_robot.columns[7]]*multiplicador_momento
    else:
        print("ERRO! Eixo não permitido! Usar X, Y, Z, -X, -Y ou -Z")

    #X2:
    if X2_STRAP == "X":
        df_strap["FX2"] = -tipo_planilha*df_robot[df_robot.columns[2]]*multiplicador_forca
        df_strap["MX2"] = -tipo_planilha*df_robot[df_robot.columns[5]]*multiplicador_momento
    elif X2_STRAP == "-X":
        df_strap["FX2"] = tipo_planilha*df_robot[df_robot.columns[2]]*multiplicador_forca
        df_strap["MX2"] = tipo_planilha*df_robot[df_robot.columns[5]]*multiplicador_momento
    elif X2_STRAP == "Y":
        df_strap["FX2"] = -tipo_planilha*df_robot[df_robot.columns[3]]*multiplicador_forca
        df_strap["MX2"] = -tipo_planilha*df_robot[df_robot.columns[6]]*multiplicador_momento
    elif X2_STRAP == "-Y":
        df_strap["FX2"] = tipo_planilha*df_robot[df_robot.columns[3]]*multiplicador_forca
        df_strap["MX2"] = tipo_planilha*df_robot[df_robot.columns[6]]*multiplicador_momento
    elif X2_STRAP == "Z":
        df_strap["FX2"] = -tipo_planilha*df_robot[df_robot.columns[4]]*multiplicador_forca
        df_strap["MX2"] = -tipo_planilha*df_robot[df_robot.columns[7]]*multiplicador_momento
    elif X2_STRAP == "-Z":
        df_strap["FX2"] = tipo_planilha*df_robot[df_robot.columns[4]]*multiplicador_forca
        df_strap["MX2"] = tipo_planilha*df_robot[df_robot.columns[7]]*multiplicador_momento
    else:
        print("ERRO! Eixo não permitido! Usar X, Y, Z, -X, -Y ou -Z")

    #X3:
    if X3_STRAP == "X":
        df_strap["FX3"] = -tipo_planilha*df_robot[df_robot.columns[2]]*multiplicador_forca
        df_strap["MX3"] = -tipo_planilha*df_robot[df_robot.columns[5]]*multiplicador_momento
    elif X3_STRAP == "-X":
        df_strap["FX3"] = tipo_planilha*df_robot[df_robot.columns[2]]*multiplicador_forca
        df_strap["MX3"] = tipo_planilha*df_robot[df_robot.columns[5]]*multiplicador_momento
    elif X3_STRAP == "Y":
        df_strap["FX3"] = -tipo_planilha*df_robot[df_robot.columns[3]]*multiplicador_forca
        df_strap["MX3"] = -tipo_planilha*df_robot[df_robot.columns[6]]*multiplicador_momento
    elif X3_STRAP == "-Y":
        df_strap["FX3"] = tipo_planilha*df_robot[df_robot.columns[3]]*multiplicador_forca
        df_strap["MX3"] = tipo_planilha*df_robot[df_robot.columns[6]]*multiplicador_momento
    elif X3_STRAP == "Z":
        df_strap["FX3"] = -tipo_planilha*df_robot[df_robot.columns[4]]*multiplicador_forca
        df_strap["MX3"] = -tipo_planilha*df_robot[df_robot.columns[7]]*multiplicador_momento
    elif X3_STRAP == "-Z":
        df_strap["FX3"] = tipo_planilha*df_robot[df_robot.columns[4]]*multiplicador_forca
        df_strap["MX3"] = tipo_planilha*df_robot[df_robot.columns[7]]*multiplicador_momento
    else:
        print("ERRO! Eixo não permitido! Usar X, Y, Z, -X, -Y ou -Z")

    df_strap = df_strap[["Node Robot", "Node STRAP", "Case", "FX1", "FX2", "FX3", "MX1", "MX2", "MX3"]]
    # df_strap["Comando"] = df_strap.apply(
    #     lambda row: f'FX1 {row["FX1"]} FX2 {row["FX2"]} FX3 {row["FX3"]} MX1 {row["MX1"]} MX2 {row["MX2"]} MX3 {row["MX3"]} N {row["Node"]}', axis=1)

    df_strap['Comando'] = ("FX1 " + df_strap["FX1"].astype('str') +
                        " FX2 " + df_strap["FX2"].astype('str') +
                        " FX3 " + df_strap["FX3"].astype('str') +
                        " MX1 " + df_strap["MX1"].astype('str') +
                        " MX2 " + df_strap["MX2"].astype('str') +
                        " MX3 " + df_strap["MX3"].astype('str') +
                        " N " + df_strap["Node STRAP"].astype('str'))


    st.subheader("Tabela original do Robot")
    st.dataframe(df_robot, use_container_width=True)

    st.subheader("Tabela convertida para STRAP")
    st.dataframe(df_strap, use_container_width=True)

    df_strap.to_excel("saida.xlsx")

    # Opção de baixar o arquivo 

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_strap.to_excel(writer, index=False)

    st.success("Conversão realizada com sucesso")

    st.download_button(
        label="Baixar planilha STRAP",
        data=buffer.getvalue(),
        file_name="saida.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )