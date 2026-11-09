# fluxo_opcoes — fluxo e posições em opções de ações (B3)

Protótipo em Python que lê o **negócio a negócio** e as **posições em aberto** do Boletim Diário do Mercado e destaca estruturas, blocos grandes e mudanças de posicionamento em opções de ações.

## Instalação

```bash
pip install git+https://github.com/acfraga/fluxo-opcoes-b3.git
# com a curva do DI1 via pyield:
pip install "fluxo-opcoes-b3[di] @ git+https://github.com/acfraga/fluxo-opcoes-b3.git"
```

Ou clone o repositório e rode o notebook `fluxo_opcoes_b3.ipynb` na pasta dele. Regressão: `python teste_compat.py`.

## Aviso

Leitura heurística de dados públicos para fins educativos, sem recomendação de investimento. O código de participante identifica a corretora, não o cliente. O repositório não contém nem baixa arquivos da B3: os dados ficam fora do controle de versão (`.gitignore`).

## Conteúdo

```
fluxo_opcoes/              pacote
  config.py                parâmetros, aliases de colunas, corretoras, raiz -> ação-objeto
  leitura.py               detecção de tipo/cabeçalho/separador/decimal/zip e colunas canônicas
  precificacao.py          DU (calendário ANBIMA 2024–2028), Black-Scholes, IV, delta, gama
  taxas.py                 curva do DI1 via pyield, interpolação flat-forward, % do DI
  enriquecimento.py        universo de séries, perfil e fluxo por corretora
  estruturas.py            agrupamento, classificação, blocos, diretos, cruzamento com ΔOI
  posicoes.py              ΔOI por componente, concentração, mapa de vencimento
  painel.py                resumo por ativo e exportação em Excel
  pipeline.py              processar_dia()
  validacao.py             histórico, ADTV, z-scores, event study
  sintetico.py             pregão sintético com eventos plantados (teste)
fluxo_opcoes_b3.ipynb      notebook passo a passo
teste_compat.py            regressão: 13 eventos plantados, com e sem curva do DI1
pyproject.toml             instalação com pip (extras: di, rapido, notebook)
.github/workflows/         testes automáticos a cada envio (Python 3.10 e 3.12)
painel_exemplo_SINTETICO.xlsx
requirements.txt
```

## Começo rápido

1. `pip install -r requirements.txt`
2. Abra `fluxo_opcoes_b3.ipynb` com `MODO_SINTETICO = True` e rode tudo: gera um pregão fictício e o painel.
3. Para dados reais, coloque os arquivos do pregão em `dados/AAAA-MM-DD/` e o arquivo de posições em aberto do pregão anterior na pasta da data anterior. Depois use `MODO_SINTETICO = False`.

Uso direto:

```python
from fluxo_opcoes import processar_dia, exportar_excel
res = processar_dia("dados/2026-11-16", pasta_anterior="dados/2026-11-13")
exportar_excel(res, "painel_2026-11-16.xlsx")
```

Arquivos por pregão (nomes livres; o tipo é detectado pelo cabeçalho):

- capítulo **Renda variável**: Cadastro de instrumentos, Negócios consolidados do pregão (regular) e Negócio a negócio;
- capítulo **Derivativos de bolsa**: Posições em aberto (também do pregão anterior).

Cadastro, consolidado e negócio a negócio de Derivativos de bolsa (ex.: `..._NEGOCIOSAVISTA_DRV.txt`) não têm opções de ações: no cadastro de 27/10/2026 eram 7.760 opções, todas sobre DOL, IDI, binárias, commodities e decisões de juros.

## Dados e uso

A página do boletim restringe o uso à consulta informativa e veda coleta automatizada, inclusive por ferramentas de IA. O pacote não baixa nada: só lê arquivos colocados na pasta. Para produção e histórico, o caminho é dado licenciado (UP2DATA ou o vendor da casa), alinhado com compliance. Contato de dados da B3 para instituições: dataservices@b3.com.br.

## Checklist no primeiro arquivo real

- [ ] `inventariar(pasta)`: todos os arquivos com cobertura 1,0 e o tipo certo (a coluna `faltando` diz qual campo mínimo não foi achado). Se um rótulo não casar, use `overrides={"posicoes": {"n_clientes_1": "Nome da coluna"}}` (formato `{tipo: {campo_canonico: coluna}}`).
- [ ] Campos de clientes do arquivo de posições (`n_clientes_1` = BrrwrQty, `n_clientes_2` = LndrQty): confirme qual representa titulares e qual representa lançadores em opções, comparando uma série com a consulta da B3. A flag de concentração usa os dois lados e não depende disso; a interpretação depende.
- [ ] Tipo da opção, strike e vencimento lidos do cadastro (série sem esses campos fica de fora).
- [ ] Ação-objeto de cada raiz. A heurística escolhe a ação mais negociada com a mesma raiz; corrija em `MAPA_SUBJACENTE` se precisar.
- [ ] Multiplicador do contrato (opções de ações: 1).
- [ ] Hora do negócio com milissegundos (`HHMMSSmmm` ou `HH:MM:SS.fff`).
- [ ] Curva pré: com o `pyield` instalado, o notebook busca a curva de ajuste do DI1 do dia (`CURVA_DI = "pyield"`). Sem ela, vale `taxa_pre_aa` (14%, ilustrativa), e o pipeline avisa.
- [ ] Códigos de corretora em `CORRETORAS` (`formador`, `varejo`, `institucional`, `estrangeira`, `tesouraria`). Sem isso, vale a heurística de alto giro.
- [ ] Feriados: o calendário embutido cobre 2024–2028.
- [ ] Pontuação no Excel: o .xlsx guarda números, e o Excel mostra ponto e vírgula conforme a configuração do computador. Se aparecer no padrão americano, ajuste o Excel/Windows para português (Brasil) ou exporte com `texto_br=True` (números como texto no padrão brasileiro, sem ordenação numérica nem fórmulas).

## Como funciona

- **Estruturas.** Cada negócio é visto pela corretora compradora (+) e pela vendedora (−). Para corretoras que não são formador nem alto giro, negócios do mesmo ativo-objeto numa janela de 1 s formam um agrupamento. Pernas com quantidades proporcionais (até 1:3) são classificadas: travas, ratio, risk reversal, sintético, straddle/strangle, rolagem, calendário/diagonal, borboleta, condor/iron condor, box, conversão/reversão, collar e lançamento coberto. Box e conversão saem como financiamento, com a taxa implícita (a.a., base 252). Execuções repetidas da mesma estrutura pela mesma corretora no dia são somadas antes do filtro de tamanho.
- **Blocos.** Fluxo líquido de cada corretora (fora formador e alto giro) em cada série no dia, somando as execuções; entra se o Δ-notional líquido for ≥ R$ 5 mi E ≥ 1% do ADTV (ou prêmio ≥ R$ 5 mi).
- **Espelhos.** Quando as duas pontas não são alto giro, a leitura fica com a de classe preferida e menor participação no ativo; a outra aparece na coluna "Espelho".
- **Diretos.** Mesma corretora nos dois lados: forma e direção indeterminadas; corte em 2% do ADTV. "Abriu posição" marca os que aparecem como aumento de OI (≥ 50% da quantidade em cada perna); só esses entram no score. Padrões de box, conversão/reversão e sintético (inclusive somando os diretos da corretora no dia) são classificados como financiamento, com taxa implícita, e ficam fora do score.
- **Cruzamento com ΔOI.** Se a perna vendida é explicada por aumento de posição coberta, "risk reversal de baixa" vira "collar de quem tem a ação" e "venda de call" vira "lançamento coberto".
- **Posições.** ΔOI por componente (coberta, travada, descoberta). Delta-notional dos titulares (exceto travas) e dos lançadores a seco e cobertos. Entrada concentrada, medida sem a parcela travada: ΔOI ≥ 20% do OI anterior, ≥ R$ 5 mi E ≥ 1% do ADTV em delta-notional, com até 3 clientes de variação em algum lado. Travas e box relevantes aparecem à parte, como provável financiamento.
- **Fluxo institucional.** Δ-notional líquido comprado em opções pelas corretoras que não são formador, varejo ou alto giro, sem diretos e sem pernas de box/conversão.
- **Mapa de vencimento.** Vencimento em até 5 DU; o strike perto do dinheiro com maior OI é o candidato a pin; hedge por 1% supõe que lançadores a seco façam delta hedge.

## Testes feitos

- Pregão sintético (`sintetico.py`) com 8 eventos plantados: todos detectados, sem falso positivo, negócio cancelado removido. Vale com e sem mapeamento de corretoras e sem o pregão anterior.
- pandas 2.2 e 3.0, com avisos tratados como erro (`python teste_compat.py`).
- Interpolação flat-forward igual à do `Interpolador` do pyield 0.63 (diferença < 1e-14). A busca no pyield foi testada com retorno simulado: o ambiente de desenvolvimento não acessa a B3.
- Leitura com rótulos em português, separador vírgula, hora com dois-pontos, encoding cp1252, linhas de título antes do cabeçalho e arquivo zip.
- Painel recalculado no LibreOffice sem erros de fórmula.
- Cadastro real do boletim (Derivativos de bolsa, 27/10/2026): reconhecido, e o diagnóstico aponta a ausência de opções de ações.

## Limitações

- Pernas diferentes executadas com minutos de intervalo não formam estrutura (execuções repetidas da mesma estrutura, sim, são somadas).
- Código de participante é a corretora, não o cliente; o arquivo público não traz o lado agressor.
- "Alto giro" mistura formador e varejo de massa; o mapeamento manual resolve.
- Delta por Black-Scholes sem dividendos, com IV do último preço; puts americanas muito ITM ficam aproximadas.
- Até haver histórico, o ADTV é o volume do próprio dia (depois use `adtv_historico`).
- Os limiares são pontos de partida: calibre com z-scores e valide com event study (60+ pregões).
- Event study: descarta sinais sobrepostos do mesmo ativo e testa entre datas, mas segue sendo um teste simples; com poucos pregões e muitos tipos de sinal, trate significância com cautela.

## Próximos passos sugeridos

- Tabela "Negociação de estratégias" do boletim, se cobrir opções de ações.
- Cruzar com posições de aluguel (BTB) para separar hedge de aposta.

## Calibração com dados reais (27 e 28/10/2026)

- **Bug corrigido:** o arquivo de posições escreve quantidades com ponto de milhar e sem vírgula; o leitor decidia o decimal coluna a coluna e lia "35.500" como 35,5. Agora a convenção é decidida pelo arquivo (separador `;` = padrão brasileiro). As abas de posições dos painéis gerados antes disso estão erradas.
- **Índice fora (`raizes_excluidas=("IBOV",)`):** o consolidado traz a linha do índice, sem ADTV utilizável, e as opções de IBOV dominavam o painel.
- **Tamanho com critério E (`criterio_e=True`):** Δ-notional ≥ R$ 5 mi E ≥ 1% do ADTV (2% para diretos). Antes bastava um dos dois, o que deixava passar evento de R$ 5 mi em PETR4 e evento de R$ 4 mil em ação ilíquida.
- **Execuções somadas (`agregar_execucoes=True`):** os grandes fatiam ordens (a mesma trava repetida em segundos; até 21 execuções na mesma série).
- **Comportamento acima do rótulo:** corretora cadastrada que age como alto giro num ativo é tratada como tal ali (exceto formador).
- **Concentração sem travas:** box em calls/puts muito dentro do dinheiro dominava a lista. Em 28/10: 45 séries concentradas (eram 122) e leitura relevante em 40 de 172 ativos.
- **Diretos confirmados por ΔOI:** em 28/10, dos 160 diretos acima de 2% do ADTV, 7 aparecem como abertura de posição.
- **Pregão anterior (revisão de 26–30/10):** os painéis de 26 e 27/10 usaram o dia seguinte como \"anterior\", invertendo o ΔOI. O notebook agora acha sozinho a pasta anterior mais recente (`pregao_anterior`) e o pipeline recusa pasta ou arquivo com data igual ou posterior ao pregão.
- **Financiamento cruzado:** em 28–30/10, 47 dos 68 diretos que abriram posição eram box, conversão ou sintético (R$ 6,6 bi de R$ 8,0 bi em Δ bruto). Agora vão para a aba Financiamento, com taxa implícita, e saem do score. Sintético sem a ação também é tratado como perna de conversão.
- **Pernas no dia:** estrutura montada pela mesma corretora em horários diferentes vira uma estrutura só (escopo \"dia\"); calendário com encerramento na perna curta e abertura na longa (pelo ΔOI) vira \"rolagem\".
- **Financiamento fora das posições (revisão 26–30/10):** 58 das 137 séries concentradas e 25 das 59 leituras de titulares por ativo da semana eram explicadas por box/conversão/sintético do próprio dia. Série cujo ΔOI é explicado (>= 50%) por financiamento agora fica fora da concentração e das leituras.
- **Spread contra o DI (0.8.0):** a taxa implícita do financiamento é comparada com o DI do mesmo prazo, pela curva de ajuste do DI1 do pregão (`taxas.curva_di_pyield`), interpolada por flat-forward na base 252, e também expressa em % do DI (razão das taxas diárias). A mesma curva dá a taxa de cada série no Black-Scholes. `recalcular_spread_historico()` refaz o spread dos pregões já salvos sem reprocessá-los, e a visão do período ganhou a aba Financiamento (taxa mediana, DI do mesmo prazo, spread e % do DI por pregão). O CDI de um dia (`cdi_pyield`) serve só de contexto: para um box de N dias úteis, a referência é a taxa pré de N dias.
- **Box x conversão (0.8.1, revisão de 26/10–04/11):** separados por tipo, box (só opções) saiu a cerca de 113% do DI (8 operações, R$ 1,0 bi) e conversão (com a ação) a 95% (105 operações, R$ 7,0 bi), com BRAP4 a 48% e GOAU4/GGBR4 a 84–86%. A taxa da conversão embute dividendos/JCP até o vencimento e o aluguel da ação, então não é taxa de financiamento limpa. A aba Financiamento da visão do período agora traz uma linha por tipo; compare box com box.
- **Visão do período:** `resumo_periodo()` + `exportar_periodo()` (seção 10 do notebook) juntam os pregões do histórico: persistência por ativo, eventos direcionais e séries concentradas em mais de um pregão.
- **Anormalidade contra o próprio histórico (05–30/10):** com 19 pregões, "pregões com sinal" saturou (19/19 nos ativos grandes) e BOVA11 concentrou metade dos eventos. O Resumo diário ganhou o z de fluxo e ΔOI contra o histórico do ativo (`z_do_dia`, a partir de 10 pregões) e passa a ser ordenado pelo maior |z|. A visão do período conta pregões anormais, consistência de direção e sequência atual, e lista corretoras recorrentes marcando as de mão dupla (alta e baixa no mesmo ativo: típico de liquidez/hedge).
- **Limite da classificação da B3:** coberta/travada/descoberta é apurada por conta. Um box com pernas em contas diferentes aparece como venda a seco; em BOVA11, onde o financiamento é grande, leia "a seco" com cautela.
