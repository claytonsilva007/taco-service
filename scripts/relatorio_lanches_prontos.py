"""Relatório somente-leitura: fila de curadoria de lanches prontos da POF.

Lista os alimentos da Tabela de Medidas Referidas da POF que são lanches,
sanduíches e salgados prontos (sanduíche, hambúrguer (sanduíche),
cheesburguer, eggsburguer, bauru, misto, x-*, cheese *, coxinha, pastel,
esfiha, pizza...) com o peso da medida ``UNIDADE`` e se a TACO tem composição
para o **mesmo** alimento.

"Mesmo alimento" é comparação exata da base da descrição TACO (o trecho antes
da primeira vírgula) com o nome POF inteiro, ambos dobrados (minúsculas, sem
acento).
Não é crosswalk POF↔TACO: as descrições TACO que só compartilham a primeira
palavra saem em ``taco_para_revisao``, para decisão humana, nunca como
equivalência.

Com ``--demanda``, cruza a fila com o JSON do relatório de demanda do
tryvon-server (``python -m app.scripts.relatorio_curadoria_nutricional
--formato json``) e ordena pelo número de ocorrências.

Nada é gravado: lê os CSVs processados e imprime texto e/ou JSON.

Uso:
    python scripts/relatorio_lanches_prontos.py
    python scripts/relatorio_lanches_prontos.py --formato json --demanda demanda.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
POF_PADRAO = RAIZ_PROJETO / "data" / "processed" / "pof" / "pof_medidas_caseiras.csv"
TACO_PADRAO = RAIZ_PROJETO / "data" / "processed" / "taco" / "taco_composicao.csv"

# Padrões sobre o nome POF dobrado. "hamburguer (sanduiche)" é o sanduíche;
# "hamburguer de carne bovina" e "pao de hamburguer" ficam de fora de propósito.
PADROES_LANCHE = re.compile(
    r"^sanduiche|^hamburguer \(sanduiche\)$|^cheesburguer$|^eggsburguer$|^bauru$"
    r"|\bmisto\b|^x[- ]|^cheese |^cachorro quente$|^americano$"
    r"|coxinha|pastel|esfiha|esfirra|pizza"
)


def dobrar(texto: str) -> str:
    """Minúsculas, sem acento e com espaços colapsados."""
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def _base_taco(descricao: str) -> str:
    return dobrar(descricao.split(",", 1)[0])


def _primeira_palavra(texto: str) -> str:
    return re.split(r"[\s(,]", texto, maxsplit=1)[0]


def carregar_demanda(caminho: Path | None) -> dict[str, int]:
    """Ocorrências por ingrediente normalizado (todas as seções do relatório)."""
    if caminho is None:
        return {}
    relatorio = json.loads(caminho.read_text(encoding="utf-8"))
    demanda: dict[str, int] = {}
    for entradas in relatorio.values():
        for entrada in entradas:
            nome = dobrar(entrada["normalized_ingredient"])
            demanda[nome] = demanda.get(nome, 0) + int(entrada["occurrence_count"])
    return demanda


def _demanda_do_alimento(nome_pof: str, demanda: dict[str, int]) -> int:
    """Soma as ocorrências dos ingredientes que contêm o nome POF inteiro (ou
    estão contidos nele, palavra a palavra). Só prioriza; não resolve nada."""
    alvo = dobrar(re.sub(r"\(.*?\)", " ", nome_pof))
    total = 0
    for ingrediente, ocorrencias in demanda.items():
        if re.search(rf"\b{re.escape(alvo)}\b", ingrediente) or re.search(
            rf"\b{re.escape(ingrediente)}\b", alvo
        ):
            total += ocorrencias
    return total


def gerar_relatorio(
    pof: pd.DataFrame, taco: pd.DataFrame, demanda: dict[str, int] | None = None
) -> list[dict]:
    """Uma linha por alimento POF de lanche pronto, ordenada por demanda e nome."""
    demanda = demanda or {}
    unidades = pof[
        (pof["medida_caseira"].astype(str) == "True") & (pof["descricao_medida"] == "UNIDADE")
    ]
    bases_taco = [
        (int(row.numero_alimento), row.descricao, _base_taco(row.descricao))
        for row in taco.itertuples()
    ]

    linhas = []
    for (codigo, nome), grupo in unidades.groupby(["codigo_alimento", "descricao_alimento"]):
        nome_dobrado = dobrar(nome)
        if not PADROES_LANCHE.search(nome_dobrado):
            continue
        # Prefere a linha sem preparo ("NAO SE APLICA"); o peso da unidade não
        # varia com o preparo nos lanches prontos da POF.
        grupo = grupo.sort_values("codigo_preparacao", ascending=False)
        linha_pof = grupo.iloc[0]
        # Nome POF inteiro, com o parêntese: "hamburguer (sanduiche)" nunca é a
        # base TACO "hamburguer" (a carne 415–417).
        mesmo = [
            {"numero_alimento": numero, "descricao": descricao}
            for numero, descricao, base in bases_taco
            if base == nome_dobrado
        ]
        palavra = _primeira_palavra(nome_dobrado)
        revisao = [
            {"numero_alimento": numero, "descricao": descricao}
            for numero, descricao, base in bases_taco
            if base != nome_dobrado and _primeira_palavra(base) == palavra
        ]
        linhas.append(
            {
                "codigo_pof": int(codigo),
                "nome_pof": nome,
                "unidade_g": float(linha_pof["quantidade_g"]),
                "descricao_fonte_medida": linha_pof["descricao_fonte"],
                "taco_mesmo_alimento": mesmo,
                "taco_para_revisao": revisao,
                "demanda_ocorrencias": _demanda_do_alimento(nome, demanda),
            }
        )
    return sorted(linhas, key=lambda linha: (-linha["demanda_ocorrencias"], linha["nome_pof"]))


def formatar_texto(linhas: list[dict]) -> str:
    saida = [f"Lanches prontos na POF com medida UNIDADE ({len(linhas)})"]
    for linha in linhas:
        if linha["taco_mesmo_alimento"]:
            taco = "TACO: " + "; ".join(
                f"{t['numero_alimento']} {t['descricao']}" for t in linha["taco_mesmo_alimento"]
            )
        else:
            taco = "TACO: não tem"
        ocorrencias = linha["demanda_ocorrencias"]
        demanda = f" | demanda {ocorrencias}" if ocorrencias else ""
        saida.append(
            f"  {linha['codigo_pof']} {linha['nome_pof']} | UNIDADE = {linha['unidade_g']:g} g"
            f" | {taco}{demanda}"
        )
        if linha["taco_para_revisao"]:
            revisao = "; ".join(
                f"{t['numero_alimento']} {t['descricao']}" for t in linha["taco_para_revisao"]
            )
            saida.append(f"      revisar (não é o mesmo alimento): {revisao}")
    return "\n".join(saida)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pof", type=Path, default=POF_PADRAO)
    parser.add_argument("--taco", type=Path, default=TACO_PADRAO)
    parser.add_argument(
        "--demanda",
        type=Path,
        default=None,
        help="JSON do relatorio_curadoria_nutricional do tryvon-server.",
    )
    parser.add_argument("--formato", choices=("texto", "json", "ambos"), default="ambos")
    args = parser.parse_args(argv)

    pof = pd.read_csv(args.pof, dtype={"descricao_fonte": str})
    taco = pd.read_csv(args.taco, usecols=["numero_alimento", "descricao"])
    linhas = gerar_relatorio(pof, taco, carregar_demanda(args.demanda))

    if args.formato in ("texto", "ambos"):
        print(formatar_texto(linhas))
    if args.formato == "ambos":
        print("\nJSON")
    if args.formato in ("json", "ambos"):
        json.dump(linhas, sys.stdout, ensure_ascii=False, indent=2)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
