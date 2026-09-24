import json

import pandas as pd

from scripts.relatorio_lanches_prontos import (
    POF_PADRAO,
    TACO_PADRAO,
    carregar_demanda,
    formatar_texto,
    gerar_relatorio,
    main,
)


def _relatorio(demanda=None):
    pof = pd.read_csv(POF_PADRAO, dtype={"descricao_fonte": str})
    taco = pd.read_csv(TACO_PADRAO, usecols=["numero_alimento", "descricao"])
    return {linha["nome_pof"]: linha for linha in gerar_relatorio(pof, taco, demanda)}


def test_lista_os_sanduiches_com_o_peso_da_unidade():
    linhas = _relatorio()
    pesos = {nome: linha["unidade_g"] for nome, linha in linhas.items()}
    assert pesos["HAMBURGUER (SANDUICHE)"] == 125.0
    assert pesos["CHEESBURGUER"] == 140.0
    assert pesos["EGGSBURGUER"] == 175.0
    assert pesos["BAURU"] == 125.0
    assert pesos["MISTO QUENTE OU FRIO"] == 85.0
    assert pesos["SANDUICHE DE PRESUNTO"] == 90.0
    assert pesos["COXINHA"] == 50.0


def test_nao_inclui_a_carne_nem_o_pao_de_hamburguer():
    linhas = _relatorio()
    assert "HAMBURGUER DE CARNE BOVINA" not in linhas
    assert "PAO DE HAMBURGUER" not in linhas


def test_carne_taco_nunca_e_o_mesmo_alimento_que_o_sanduiche():
    linha = _relatorio()["HAMBURGUER (SANDUICHE)"]
    assert linha["taco_mesmo_alimento"] == []
    assert {t["numero_alimento"] for t in linha["taco_para_revisao"]} == {415, 416, 417}


def test_demanda_do_tryvon_prioriza_a_fila(tmp_path):
    arquivo = tmp_path / "demanda.json"
    arquivo.write_text(
        json.dumps(
            {
                "ausencias": [
                    {"normalized_ingredient": "misto quente", "occurrence_count": 3},
                    {"normalized_ingredient": "bauru", "occurrence_count": 5},
                ],
                "ambiguidades": [{"normalized_ingredient": "bauru", "occurrence_count": 2}],
                "correcoes_usuario": [],
            }
        ),
        encoding="utf-8",
    )
    demanda = carregar_demanda(arquivo)
    assert demanda == {"misto quente": 3, "bauru": 7}
    linhas = list(_relatorio(demanda).values())
    assert [linha["nome_pof"] for linha in linhas[:2]] == ["BAURU", "MISTO QUENTE OU FRIO"]
    assert carregar_demanda(None) == {}


def test_texto_marca_ausencia_e_revisao():
    texto = formatar_texto(list(_relatorio({"coxinha": 1}).values()))
    assert "8500303 HAMBURGUER (SANDUICHE) | UNIDADE = 125 g | TACO: não tem" in texto
    assert "revisar (não é o mesmo alimento): 415 Hambúrguer, bovino, cru" in texto
    assert "COXINHA | UNIDADE = 50 g | TACO: não tem | demanda 1" in texto


def test_main_imprime_texto_e_json_sem_gravar_nada(capsys):
    antes = POF_PADRAO.read_bytes(), TACO_PADRAO.read_bytes()
    assert main([]) == 0
    saida = capsys.readouterr().out
    texto, bloco_json = saida.split("\nJSON\n")
    assert texto.startswith("Lanches prontos na POF com medida UNIDADE")
    assert any(linha["nome_pof"] == "BAURU" for linha in json.loads(bloco_json))
    assert (POF_PADRAO.read_bytes(), TACO_PADRAO.read_bytes()) == antes


def test_main_so_json(capsys):
    assert main(["--formato", "json"]) == 0
    assert json.loads(capsys.readouterr().out)
