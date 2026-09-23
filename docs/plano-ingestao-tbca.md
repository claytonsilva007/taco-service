# Plano de implementação — ingestão da TBCA como base de referência

**Status:** PROPOSTO
**Criado em:** 23/09/2026
**Escopo:** `taco-service` (pipeline, base de dados, API). O lado consumidor
(adapter e resolvedor no `tryvon-server`) está em
[`tryvon-server/docs/plans/nutrition-tbca-reference-source-plan.md`](https://github.com/claytonsilva007/tryvon-server/blob/main/docs/plans/nutrition-tbca-reference-source-plan.md).

## 1. Objetivo

Adicionar a **Tabela Brasileira de Composição de Alimentos (TBCA, FoRC/USP)**
ao `taco-service` como segunda base de referência, com o mesmo regime da TACO:

- fonte bruta imutável em `data/raw/`;
- pipeline determinístico que gera CSVs processados **reproduzíveis byte a
  byte** e versionados;
- API somente-leitura que responde em tempo compatível com o orçamento do
  `TacoProviderAdapter` do `tryvon-server` (connect 0,5 s / read 1,5 s), de
  modo que o resolvedor de alimentos possa consultá-la como fonte externa.

Fora de escopo: crosswalk TACO ↔ TBCA ↔ POF (mesma regra da POF: não inventar
equivalência por semelhança de nome), e migrar a TACO para o novo
armazenamento (ver seção 12).

## 2. Diagnóstico da fonte

Arquivo recebido: `alimentos.txt` — espelho público da TBCA
(`github.com/raul-rznd/web-scraping-tbca`), já usado como referência na
curadoria do catálogo Tryvon (`lanches_prontos_canonico.json`). JSON Lines,
17,5 MB, um alimento por linha:

```json
{"codigo": "C0113T", "classe": "Leguminosas e derivados",
 "descricao": "Orelha-de-padre, semente, seca, ..., Lablab purpureus (L) Sweet,",
 "nutrientes": [{"Componente": "Energia", "Unidades": "kcal", "Valor por 100g": "121"}, ...]}
```

Perfil medido sobre o arquivo inteiro:

| Item | Resultado | Consequência no pipeline |
|---|---|---|
| Registros | 5.668, `codigo` único | chave natural = código TBCA |
| Formato do código | 6 caracteres (`C0225A`); o código oficial é `BRC0225A` — o espelho **remove o prefixo `BR`** | gravar os dois: `codigo` (espelho) e `codigo_tbca` (`BR` + código). A API aceita ambos na entrada |
| Código fora do padrão | 1 (`A0105N`, papa infantil) | aceitar; não validar prefixo por regex rígida |
| Componentes | 36 nomes / 37 pares (componente, unidade) em 5.662 registros; `Energia` aparece em kJ **e** kcal | pivotar por `(componente, unidade)`, nunca só pelo nome |
| Registros com 74 itens | 5: `C0953F`, `C0955F`, `C0956F`, `C0957F` têm cada nutriente **duplicado com o mesmo valor** (intercalado); `C0237T` tem **dois alimentos misturados** (valores diferentes por par) | duplicata idêntica → deduplicar; duplicata divergente → **quarentena** (fora do CSV, listada no relatório de anomalias) |
| Registro com 40 itens | `C0018A` traz `Gordura de adição`, `Proteína animal`, `Proteína vegetal` | colunas extras aceitas como opcionais (NaN nos demais) |
| Valores | decimal com vírgula (`"66,2"`) | converter para float |
| `tr` (traço) | 5.087 ocorrências | `1e-5`, mesma convenção da TACO (o adapter do Tryvon já reconhece `1e-05` como `trace`) |
| `NA` | 7.737 ocorrências | ausente (vazio no CSV) |
| `-` | 107 ocorrências | ausente; contado à parte no relatório (semântica não documentada pelo espelho) |
| Unidade de colesterol | 270 registros com `g` em vez de `mg`, valores típicos de mg (ex.: pirarucu 42,9) | normalizar para `colesterol_mg` **sem converter** o valor, registrar no relatório; conferir amostra de 10 no site oficial antes do merge (seção 11) |
| `classe` | 18 rótulos; `Pescados e Frutos do mar` (437) e `Pescados e frutos do mar` (47) são a mesma classe | normalizar capitalização por tabela explícita de rótulos, sem heurística |
| Letra final do código × classe | correlacionadas mas **não** 1:1 (`T` aparece em Leguminosas, Miscelâneas, Bebidas, Cereais) | nunca derivar categoria pela letra do código — mesma lição da TACO (alimento 561) |
| `descricao` | todas terminam em `,`; contêm nome científico, marca (`Sadia`), `c/ sal`/`s/ sal`, lista de ingredientes entre parênteses | remover a vírgula final; extrair `base`, `preparo`, `sal` (seção 4.3) |
| Pratos preparados | não há classe equivalente a "Alimentos preparados" da TACO; sopas, risotos, sanduíches estão em "Cereais e derivados" etc. | `tipo` explícito derivado por regra auditável (seção 4.3) |

A fonte não carrega versão da TBCA nem data de coleta. Isso vira metadado
obrigatório do pipeline (seção 3).

## 3. Estágio 1 — fonte bruta (`data/raw/tbca/`)

- `data/raw/tbca/alimentos.jsonl` — o arquivo recebido, **sem nenhuma
  edição** (só renomeado de `.txt` para `.jsonl`). Nunca editar, igual às
  demais fontes.
- `data/raw/tbca/FONTE.md` — procedência: URL do espelho, commit do espelho,
  data da coleta, versão da TBCA que o espelho reproduz, SHA-256 do arquivo,
  termos de uso da TBCA e forma de citação.
- `.gitattributes`: `data/raw/tbca/*.jsonl -text` (o arquivo tem `á`
  escapado e é ASCII puro, mas a regra evita qualquer normalização de EOL).
- `references/originais/urls-originais.txt`: acrescentar a URL oficial da
  TBCA e a do espelho.
- `CITATION.cff` / `README.md`: citação da TBCA.

**Porta de entrada (bloqueante):** confirmar que os termos de uso da TBCA
permitem redistribuir o dado em repositório público. Se não permitirem, o
bruto sai do Git e passa a ser baixado por um `scripts/fetch_tbca.py` que
confere o SHA-256 declarado em `FONTE.md` — o restante do plano não muda.

## 4. Estágio 2 — pipeline `scripts/process_tbca.py`

Mesmo esqueleto de `process_taco.py`/`process_pof.py` (`argparse` com
`--entrada`/`--saida-dir`, `logging`, `main() -> int`).

### 4.1 Saídas (`data/processed/tbca/`, versionadas, LF)

| Arquivo | Conteúdo |
|---|---|
| `tbca_composicao.csv` | uma linha por alimento; colunas de texto + uma coluna por `(componente, unidade)` (formato largo, igual à TACO) |
| `tbca_anomalias.csv` | `codigo, tipo_anomalia, detalhe` — quarentena, deduplicações, colesterol em `g`, valores `-`, componentes extras. Versionado: mudança nele aparece no diff do PR |

Formato largo e não EAV porque o conjunto de componentes é fixo (37 + 3
opcionais), o consumo é sempre "um alimento inteiro" e é o que a API da TACO
já faz. O formato longo, se alguém precisar, sai do SQLite como view
(seção 5).

`to_csv(..., lineterminator="\n", index=False)`, ordenado por `codigo`,
mesma disciplina de reprodutibilidade da TACO.

### 4.2 Colunas

Texto: `codigo`, `codigo_tbca`, `categoria`, `descricao`, `base`, `preparo`,
`qualificadores`, `sal` (`com`/`sem`/vazio), `tipo` (`simples`/`preparado`).

Nutrientes (mapa explícito `COMPONENTES` no script; componente desconhecido
**falha o pipeline**, em vez de virar coluna silenciosa):

| TBCA (componente, unidade) | coluna CSV | campo API |
|---|---|---|
| Energia, kcal / kJ | `energia_kcal`, `energia_kj` | `energy_kcal`, `energy_kj` |
| Umidade, g | `umidade_g` | `moisture_g` |
| Carboidrato total, g | `carboidrato_total_g` | `total_carbohydrate_g` |
| Carboidrato disponível, g | `carboidrato_disponivel_g` | `available_carbohydrate_g` |
| Proteína, g | `proteina_g` | `protein_g` |
| Lipídios, g | `lipidios_g` | `lipids_g` |
| Fibra alimentar, g | `fibra_g` | `dietary_fiber_g` |
| Álcool, g | `alcool_g` | `alcohol_g` |
| Cinzas, g | `cinzas_g` | `ash_g` |
| Colesterol, mg (e `g`, ver §2) | `colesterol_mg` | `cholesterol_mg` |
| Ácidos graxos saturados / mono / poli / trans, g | `saturados_g`, `monoinsaturados_g`, `poliinsaturados_g`, `trans_g` | `saturated_g`, `monounsaturated_g`, `polyunsaturated_g`, `trans_g` |
| Cálcio, Ferro, Sódio, Magnésio, Fósforo, Potássio, Zinco, Cobre, mg | `calcio_mg` … `cobre_mg` | `calcium_mg` … `copper_mg` |
| Selênio, mcg | `selenio_mcg` | `selenium_mcg` |
| Vitamina A (RE) / (RAE), mcg | `RE_mcg`, `RAE_mcg` | `re_mcg`, `rae_mcg` |
| Vitamina D, mcg | `vitamina_d_mcg` | `vitamin_d_mcg` |
| Alfa-tocoferol (Vitamina E), mg | `vitamina_e_mg` | `vitamin_e_mg` |
| Tiamina, Riboflavina, Niacina, Vitamina B6, mg | `tiamina_mg`, `riboflavina_mg`, `niacina_mg`, `vitamina_b6_mg` | `thiamine_mg`, `riboflavin_mg`, `niacin_mg`, `vitamin_b6_mg` |
| Vitamina B12, mcg | `vitamina_b12_mcg` | `vitamin_b12_mcg` |
| Vitamina C, mg | `vitamina_c_mg` | `vitamin_c_mg` |
| Equivalente de folato, mcg | `folato_dfe_mcg` | `folate_dfe_mcg` |
| Sal de adição / Açúcar de adição, g | `sal_adicao_g`, `acucar_adicao_g` | `added_salt_g`, `added_sugar_g` |
| Gordura de adição, Proteína animal, Proteína vegetal, g (opcionais) | `gordura_adicao_g`, `proteina_animal_g`, `proteina_vegetal_g` | `added_fat_g`, `animal_protein_g`, `vegetable_protein_g` |

Nomes de campo compartilhados com a TACO (`protein_g`, `lipids_g`,
`dietary_fiber_g`, `energy_kcal` …) são **iguais de propósito**: o parser do
adapter no Tryvon pode ser o mesmo para as duas fontes.

**Carboidrato:** a TACO publica carboidrato por diferença (inclui fibra) —
equivale ao "carboidrato total" da TBCA. A API expõe os dois campos TBCA e
**não** cria um `carbohydrate_g` ambíguo; o mapeamento para o
`carbohydrate_g` do domínio Tryvon é decisão do adapter (ver plano do
tryvon-server, D2).

### 4.3 Regras de normalização

1. **Leitura:** `json.loads` por linha; linha vazia ignorada; linha inválida
   falha o pipeline com número da linha.
2. **Duplicatas por `(componente, unidade)` no mesmo alimento:** todos os
   valores iguais → mantém um, registra `deduplicado`; valores diferentes →
   alimento vai para **quarentena** (`C0237T` hoje) e fica fora de
   `tbca_composicao.csv`. Nunca escolher um dos valores.
3. **Valores:** `tr` → `1e-5`; `NA` e `-` → NaN; demais → `float` com
   `,` → `.`. Qualquer outro token falha o pipeline.
4. **Colesterol em `g`:** grava em `colesterol_mg` sem conversão e registra
   `colesterol_unidade_g` (condicionado à conferência da seção 11; se a
   amostra mostrar que era mesmo grama, a regra vira `× 1000`).
5. **Categoria:** tabela `CATEGORIAS` com os 18 rótulos da fonte →
   17 rótulos canônicos (unifica "Pescados e frutos do mar"). Rótulo novo
   falha o pipeline.
6. **Descrição:** `strip()` e remoção da vírgula final; espaços múltiplos →
   um. Nada além disso — a descrição é o texto oficial.
7. **`base`/`preparo`/`qualificadores`:** mesma lógica de `process_taco.py`
   (primeiro segmento = base; `PREPAROS` reconhece cru/cozido/frito/…).
   Extrair `PREPAROS` e o extrator para um módulo compartilhado
   (`scripts/normalizacao.py`), usado pelos dois pipelines — sem mudar a saída
   da TACO (o teste byte a byte garante).
8. **`sal`:** `c/ sal` → `com`; `s/ sal` → `sem`; nenhum → vazio. É o
   qualificador que mais separa variantes na TBCA (2.086 "c/ sal", 1.471
   "s/ sal") e o resolvedor precisa dele para não tratar as duas como
   equivalentes.
9. **`tipo`:** `preparado` quando a descrição traz lista de ingredientes entre
   parênteses **ou** o `base` está numa lista explícita `PRATOS_BASE`
   (sopa, risoto, sanduíche, pizza, torta, lasanha, …) versionada no
   script; senão `simples`. A lista e a contagem por `tipo` entram no teste e
   no PR para revisão humana — é regra auditável, não inferência livre.
10. **Validações que falham o pipeline:** código duplicado; energia kcal
    ausente em mais de N% (limite medido no primeiro run); valor negativo;
    soma `umidade + proteina + lipidios + carboidrato_total + cinzas +
    alcool` fora de 95–105 g/100 g (tolerância a calibrar no primeiro run;
    violações acima do limite viram anomalia, não falha, se forem da própria
    fonte).

## 5. Estágio 3 — base de dados de serviço

### 5.1 Decisão: SQLite somente-leitura com FTS5, gerado dentro da imagem

| Opção | Latência | Operação | Busca textual | Veredito |
|---|---|---|---|---|
| DataFrame em memória (como a TACO) | sub-ms por id; busca = `str.contains` linear sobre 5,6 mil linhas (~ms) | zero | substring literal, sem ranking — é a causa do "search anchor" que o adapter do Tryvon teve de inventar (AUD-03 §6.1) | serve, mas repete a limitação da busca |
| **SQLite + FTS5 (escolhida)** | lookup por PK em µs; busca FTS5 `bm25` < 5 ms | zero serviço novo; arquivo de ~15 MB dentro da imagem; `sqlite3` é stdlib | tokens com prefixo, `remove_diacritics`, ranking, AND entre termos, independente de vírgulas | melhor custo/benefício |
| PostgreSQL (o do tryvon ou um novo) | ~1–3 ms + rede | novo serviço/credenciais/migrações; acopla o taco-service ao banco do Tryvon | `unaccent` + `pg_trgm`/tsvector | desproporcional para 5,6 mil linhas estáticas |

O dado muda só quando o pipeline muda, o volume é pequeno (5,6 mil × 43
colunas) e o padrão de acesso é leitura por chave e busca textual. Não há
escrita em runtime — logo não há motivo para um SGBD servidor.

### 5.2 `scripts/build_tbca_db.py`

Gera `data/tbca.sqlite` a partir de `data/processed/tbca/*.csv` (o SQLite
**não** é versionado; `*.sqlite` já está no `.gitignore`). Esquema:

```sql
-- Modelo lógico alimento -> variação -> nutriente (mesma ideia do modelo
-- T_GSN_ALIMENTO / T_GSN_VARIACAO_ALIMENTO / T_GSN_NUTRIENTES_ALIMENTO),
-- com tipos numéricos e um cadastro de componentes em vez de texto repetido.

CREATE TABLE tbca_alimento (            -- "variação" = registro TBCA
  codigo          TEXT PRIMARY KEY,     -- C0225A
  codigo_tbca     TEXT NOT NULL UNIQUE, -- BRC0225A
  categoria       TEXT NOT NULL,
  descricao       TEXT NOT NULL,
  base            TEXT NOT NULL,        -- agrupa variações (≈ T_GSN_ALIMENTO)
  preparo         TEXT,
  qualificadores  TEXT,
  sal             TEXT,
  tipo            TEXT NOT NULL,
  energia_kcal    REAL, energia_kj REAL, proteina_g REAL, ...  -- colunas do §4.2
) WITHOUT ROWID;

CREATE INDEX idx_tbca_base      ON tbca_alimento(base);
CREATE INDEX idx_tbca_categoria ON tbca_alimento(categoria);

CREATE TABLE tbca_componente (          -- cadastro (≈ COMPONENTE/UNIDADE_MEDIDA)
  coluna TEXT PRIMARY KEY, componente TEXT NOT NULL, unidade TEXT NOT NULL,
  campo_api TEXT NOT NULL
);

-- Formato longo derivado, para quem quer EAV (T_GSN_NUTRIENTES_ALIMENTO):
-- view gerada pelo script a partir de tbca_componente, sem duplicar dado.
CREATE VIEW tbca_nutriente AS
  SELECT codigo, 'energia_kcal' AS coluna, energia_kcal AS valor FROM tbca_alimento
  UNION ALL ... ;

-- Busca: descrição e base sem acento, prefixos de 2-4 chars para autocomplete.
CREATE VIRTUAL TABLE tbca_busca USING fts5(
  codigo UNINDEXED, descricao, base,
  tokenize = "unicode61 remove_diacritics 2", prefix = '2 3 4'
);

CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT);
-- fonte, versão TBCA, sha256 do bruto, sha256 dos CSVs, gerado_em
```

Nota: a tabela FTS guarda uma cópia do texto em vez de usar *external
content*, que exige `rowid` inteiro e não combina com `WITHOUT ROWID`. São
5,6 mil linhas, então duplicar o texto custa menos de 1 MB.

Diferenças deliberadas em relação ao modelo Oracle anexado:

- **`VALOR_CEM_G VARCHAR2(20)` → `REAL` + convenção de traço.** Guardar o
  valor como texto obriga todo consumidor a reparsear `"66,2"`, `"tr"`,
  `"NA"`; o pipeline faz isso uma vez.
- **Chave natural em vez de `NUMBER` sequencial.** O código TBCA é estável e é
  o que o catálogo Tryvon já cita (`BRC0245A`); um id sequencial muda a cada
  recarga.
- **"Alimento" (`T_GSN_ALIMENTO`) = coluna `base` indexada**, não tabela:
  a TBCA não publica essa entidade, ela é derivada da descrição; mantê-la
  como coluna evita um id artificial que ninguém referencia.

### 5.3 Onde o SQLite é gerado

- **Imagem Docker:** estágio de build copia `scripts/` e
  `data/processed/`, roda `python scripts/build_tbca_db.py --saida
  /app/data/tbca.sqlite` e só o `.sqlite` + `api/` + CSVs vão para a imagem
  final (multi-stage, `scripts/` não chega ao runtime — mantém o princípio do
  Dockerfile atual).
- **Local/testes:** `api/main.py` (ou `api/tbca.py`) gera o arquivo se
  ausente **ou** se o SHA-256 dos CSVs em `metadados` diferir dos atuais —
  sem passo manual novo para quem roda `run.bat` ou `pytest`.
- **Release:** `build_sqlite.py` passa a incluir as tabelas TBCA no
  `taco.sqlite` anexado ao release.

Conexão: `sqlite3.connect("file:...?mode=ro&immutable=1", uri=True,
check_same_thread=False)`, uma por thread (`threading.local`) — endpoints
síncronos do FastAPI rodam no threadpool.

## 6. API — `/tbca/*`

Código em `api/tbca.py` como `APIRouter(prefix="/tbca", tags=["tbca"])`,
incluído em `api/main.py`. Nenhum endpoint da TACO muda.

| Endpoint | Descrição | Meta p95 (no container) |
|---|---|---|
| `GET /tbca/foods?search=&category=&base_name=&preparation=&salt=&kind=&skip=&limit=` | busca FTS5 ordenada por `bm25` (sem `search`, por `codigo`); resumo: `code`, `tbca_code`, `category`, `description`, `base_name`, `preparation`, `salt`, `kind` | < 10 ms |
| `GET /tbca/foods/{code}` | composição completa; aceita `C0225A` e `BRC0225A` | < 2 ms |
| `POST /tbca/foods/batch` | `{"codes": [...]}` com `min_length=1` (evita a quirk do `/foods/compare` da TACO, que exige 2); códigos inexistentes vão em `not_found`, sem 404 | < 10 ms p/ 25 códigos |
| `GET /tbca/foods/{code}/variants` | mesmo `base` + `qualificadores`, com `preparation`, `salt`, `moisture_g` | < 5 ms |
| `GET /tbca/categories` | categorias e contagem | < 2 ms |
| `GET /tbca/coverage` | cobertura por nutriente (espelha `/coverage`) | < 5 ms |

Convenções herdadas: valores por 100 g; arredondamento `DECIMAL_PLACES = 5`
(preserva o traço `1e-05`); `null` = ausente.

Meta:

- `GET /` lista os endpoints novos; `GET /health` ganha `total_tbca_foods`
  e `tbca_source_version` (do `metadados`), sem remover campos.
- `API_VERSION` e `pyproject.toml` → **1.10.0** (adição compatível).
- `docs/dicionario-dados.md`: seção TBCA com o mapa do §4.2, regras de traço
  e quarentena. `CHANGELOG.md`: entrada `Added`.
- `scripts/build_static_api.py`: gerar `tbca/foods/{code}.json` e
  `tbca/categories.json` (sem busca — o site estático já não tem).

## 7. Testes

- `tests/test_tbca_pipeline.py`
  - reprodutibilidade byte a byte de `tbca_composicao.csv` e
    `tbca_anomalias.csv` (mesmo padrão do teste da TACO, roda no Windows do
    CI);
  - contagens: 5.667 alimentos no CSV, `C0237T` em quarentena, 4
    deduplicados, 270 colesterol em `g`;
  - regressões: `C0113T` com `tr`/`NA` corretos; `C0018A` com as 3 colunas
    opcionais; categoria de pescados unificada; `C0245A` → `codigo_tbca =
    BRC0245A` com os valores já curados no catálogo Tryvon;
  - componente/categoria/token desconhecido falha o pipeline (fixture
    mínima).
- `tests/test_tbca_api.py`
  - `GET /tbca/foods/BRC0245A` e `/C0245A` iguais; 404 para código
    inexistente;
  - busca sem acento (`acai` acha "Açaí"), multi-termo sem vírgula
    (`arroz polido cozido`), filtro `salt=sem`;
  - `batch` com 1 código e com código inexistente;
  - traço serializado como `1e-05`.
- `tests/test_tbca_db.py`: `build_tbca_db` idempotente; regeneração quando o
  SHA dos CSVs muda.
- **Benchmark (não bloqueante no CI):** `scripts/bench_tbca.py` — 1.000
  buscas com termos reais do log de resolução do Tryvon; registra p50/p95 no
  PR.
- Cobertura ≥ 85% continua valendo (`--cov-fail-under=85`).

## 8. Fases e entregas (taco-service)

| Fase | Entrega | Depende de |
|---|---|---|
| T0 | Termos de uso da TBCA confirmados; `data/raw/tbca/` + `FONTE.md` | — |
| T1 | `scripts/normalizacao.py` extraído de `process_taco.py` (saída da TACO idêntica) | — |
| T2 | `process_tbca.py` + CSVs + relatório de anomalias + testes de pipeline | T0, T1 |
| T3 | `build_tbca_db.py` + Dockerfile multi-stage + geração lazy local | T2 |
| T4 | `api/tbca.py` + testes de API + dicionário + CHANGELOG 1.10.0 | T3 |
| T5 | release: `build_sqlite.py` e `build_static_api.py` com TBCA | T4 |

Cada fase é um PR com CI verde (`ruff check`, `ruff format --check`,
`pytest`). Depois de T4 mergeado, o `tryvon-server` fixa o novo SHA
(plano do lado consumidor).

## 9. Desempenho e capacidade

- Dados: 5.667 × ~45 colunas ≈ 255 mil valores; SQLite ~10–15 MB com FTS.
- Memória: page cache do SQLite (padrão 2 MB) + DataFrames da TACO/POF já
  existentes; nenhum DataFrame novo para a TBCA.
- Inicialização: a imagem já traz o `.sqlite` pronto — startup não cresce.
- O orçamento do adapter do Tryvon (read 1,5 s) fica > 100× acima da meta;
  a latência dominante continua sendo rede entre containers.

## 10. Riscos

| Risco | Mitigação |
|---|---|
| Licença não permite redistribuição | fetch com SHA-256 (§3) |
| Espelho desatualizado ou com erros de scraping além dos encontrados | `FONTE.md` com versão; relatório de anomalias versionado; conferência amostral (§11) |
| Regra de `tipo` classificar prato como simples (ou o contrário) | lista explícita e revisada; contagem no teste; o resolvedor do Tryvon não depende só dela (ver plano consumidor) |
| Busca FTS devolver candidatos demais para termos curtos | `limit` máx. 100; ranking `bm25`; o resolvedor continua filtrando com `food_name_matches` |
| Divergência TACO × TBCA para o "mesmo" alimento | não resolvida aqui; política de precedência é do resolvedor (plano consumidor, D1) |

## 11. Decisões em aberto

1. **Colesterol em `g`:** conferir 10 registros no site oficial da TBCA.
   Hipótese atual: rótulo errado, valor em mg.
2. **`-` vs `NA`:** confirmar se `-` significa "não analisado" (tratado
   igual a `NA`) ou "não se aplica". Não muda o valor servido (`null`), só o
   texto do dicionário.
3. **`C0237T`:** buscar os dois registros corretos no site oficial e, se
   possível, corrigir no espelho (upstream) — o bruto aqui não é editado.
4. **Versão da TBCA** reproduzida pelo espelho (necessária para
   `source_version`).

## 12. Evolução posterior (fora deste plano)

- Servir a TACO pelo mesmo SQLite/FTS5 resolveria o problema de busca por
  substring também para ela, mas muda o comportamento de `/foods?search=`,
  que é contrato público — exige plano e versão próprios.
- Medidas caseiras POF por código TBCA: a TBCA publica porções por alimento;
  seria uma segunda fonte de peso de unidade, também sem crosswalk
  inventado.
