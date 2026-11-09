"""Parâmetros e mapeamentos do protótipo.

Tudo que é premissa ou limiar está aqui, para ficar visível e ajustável.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields


@dataclass
class Parametros:
    # --- reconstrução de estruturas -------------------------------------
    janela_ms: int = 1000            # pernas executadas pela mesma corretora em até 1s
    razao_max: int = 3               # razões de quantidade aceitas entre pernas (1:1 até 1:3)
    tol_razao: float = 0.05          # tolerância na razão (5%)
    tol_razao_dia: float = 0.10      # tolerância para pernas montadas separadamente ao longo do dia
    max_pernas: int = 5              # acima disso o agrupamento é tratado como atividade, não estrutura
    churn_min: float = 0.10          # perna com |líquido| < 10% do bruto é giro (comprou e vendeu) e sai
    du_rolagem: int = 5              # perna que vence em até N DU caracteriza rolagem
    min_notional_acao: float = 1_000_000.0  # negócio em ação só entra como perna se >= R$ 1 mi

    # --- universo --------------------------------------------------------
    raizes_excluidas: tuple = ("IBOV",)       # opções de índice: o consolidado traz o índice, sem ADTV utilizável
    agregar_execucoes: bool = True            # soma execuções repetidas da mesma corretora no dia

    # --- tamanho mínimo para reportar --------------------------------------
    criterio_e: bool = True                   # Δ-notional >= mínimo absoluto E >= fração do ADTV
    min_delta_notional: float = 5_000_000.0   # R$ de delta-notional (bruto em estruturas, líquido em blocos)
    min_pct_adtv: float = 0.01                # 1% do ADTV do ativo-objeto
    min_pct_adtv_direto: float = 0.02         # negócios diretos: 2% do ADTV
    min_premio: float = 5_000_000.0           # caminho alternativo: prêmio bruto >= R$ 5 mi
    min_confirmacao_oi: float = 0.5           # "abriu posição": ΔOI cobre >= 50% da quantidade em cada perna

    # --- posições em aberto ----------------------------------------------
    min_var_oi_pct: float = 0.20     # ΔOI sem travas >= 20% do OI anterior
    min_dn_oi: float = 5_000_000.0   # e |ΔOI sem travas| em delta-notional >= R$ 5 mi
    min_pct_adtv_oi: float = 0.01    # e >= 1% do ADTV
    min_pct_adtv_leitura: float = 0.02  # leitura agregada por ativo: >= 2% do ADTV
    max_novos_clientes: int = 3      # com até 3 clientes a mais/menos em algum dos lados
    limiar_ref_oi: float = 0.80      # perna "explicada" por um componente se ele cobre 80% da quantidade
    du_mapa_vencimento: int = 5      # mapa de posicionamento para vencimentos em até 5 DU
    banda_atm: float = 0.03          # strike a até 3% do spot = perto do dinheiro

    # --- heurística de corretora de alto giro (formador ou varejo de massa)
    mm_min_part: float = 0.10        # participa de >= 10% dos negócios do ativo
    mm_max_desequilibrio: float = 0.25  # compra e venda equilibradas
    mm_min_series: int = 8           # em várias séries

    # --- precificação ------------------------------------------------------
    taxa_pre_aa: float = 0.14        # ILUSTRATIVO: atualizar (ex.: DI1 do vencimento via pyield)
    vol_fallback: float = 0.35       # vol usada quando não há preço de mercado nem mediana
    dias_ano: int = 252

    def como_tabela(self):
        return [(f.name, getattr(self, f.name), DESCRICOES.get(f.name, "")) for f in fields(self)]

    def como_dict(self):
        return asdict(self)


DESCRICOES = {
    "janela_ms": "Janela (ms) para agrupar pernas executadas pela mesma corretora",
    "razao_max": "Maior razão de quantidade aceita entre pernas de uma estrutura",
    "tol_razao": "Tolerância relativa na razão de quantidades",
    "tol_razao_dia": "Tolerância na razão de quantidades para estruturas montadas em pernas separadas no dia",
    "max_pernas": "Máximo de pernas de uma estrutura",
    "churn_min": "Fração mínima |líquido|/bruto para a perna não ser descartada como giro",
    "du_rolagem": "Perna com vencimento em até N DU caracteriza rolagem",
    "min_notional_acao": "Notional mínimo (R$) de um negócio em ação para entrar como perna",
    "raizes_excluidas": "Raízes fora do universo (IBOV: o consolidado traz o índice, não um ativo com ADTV)",
    "agregar_execucoes": "Soma execuções repetidas da mesma corretora no dia antes do filtro de tamanho",
    "criterio_e": "Tamanho: Δ-notional >= mínimo absoluto E >= fração do ADTV (False = basta um)",
    "min_delta_notional": "Delta-notional mínimo (R$) para reportar evento",
    "min_pct_adtv": "Delta-notional mínimo como fração do ADTV (estruturas e blocos)",
    "min_pct_adtv_direto": "Delta-notional mínimo como fração do ADTV (negócios diretos)",
    "min_premio": "Prêmio bruto mínimo (R$) que basta sozinho para reportar evento",
    "min_confirmacao_oi": "Fração da quantidade que precisa aparecer como aumento de OI para 'abriu posição'",
    "min_var_oi_pct": "ΔOI sem travas mínimo como fração do OI anterior para entrada concentrada",
    "min_dn_oi": "Delta-notional mínimo (R$) do ΔOI sem travas para entrada concentrada",
    "min_pct_adtv_oi": "ΔOI sem travas mínimo como fração do ADTV para entrada concentrada",
    "min_pct_adtv_leitura": "Variação agregada por ativo mínima, como fração do ADTV, para entrar na leitura",
    "max_novos_clientes": "Variação máxima no número de clientes em algum lado para entrada concentrada",
    "limiar_ref_oi": "Fração da perna que um componente do ΔOI precisa cobrir para anotar (coberta/travada/descoberta)",
    "du_mapa_vencimento": "Vencimentos em até N DU entram no mapa de posicionamento",
    "banda_atm": "Distância máxima strike/spot para considerar perto do dinheiro",
    "mm_min_part": "Participação mínima nos negócios do ativo para alto giro",
    "mm_max_desequilibrio": "Desequilíbrio máximo compra/venda para alto giro",
    "mm_min_series": "Número mínimo de séries negociadas para alto giro",
    "taxa_pre_aa": "Taxa pré anual (252 DU) usada no Black-Scholes — ILUSTRATIVA, atualizar",
    "vol_fallback": "Volatilidade usada quando não há IV de mercado nem mediana",
    "dias_ano": "Convenção de dias úteis por ano",
}

# ---------------------------------------------------------------------------
# Corretoras: preencha com os códigos de participante que você quiser classificar
# (fonte: B3 > Hub de dados públicos > Participantes > Busca de participantes).
# Categorias reconhecidas: "formador", "varejo", "institucional", "estrangeira", "tesouraria".
# Sem mapeamento, vale a heurística de alto giro (perfil_corretoras).
# ---------------------------------------------------------------------------
CORRETORAS: dict[int, tuple[str, str]] = {
    # 1234: ("Nome da corretora", "institucional"),
}

# Classes cujo ponto de vista NÃO é usado para ler estruturas (são contraparte típica)
CLASSES_EXCLUIDAS = {"formador", "alto giro (heurística)"}
# Classes que NÃO entram no "fluxo institucional" do resumo (provedores de liquidez e varejo de massa)
CLASSES_FORA_DO_FLUXO = {"formador", "varejo", "alto giro (heurística)"}
# Preferência de ponto de vista quando o mesmo conjunto de negócios aparece espelhado
RANK_CLASSE = {"estrangeira": 0, "institucional": 0, "tesouraria": 0, "demais": 1, "varejo": 2}

# Raiz -> ação-objeto, quando a heurística (ação mais negociada com a mesma raiz) não servir
MAPA_SUBJACENTE: dict[str, str] = {
    # "PETR": "PETR4",
}

# ---------------------------------------------------------------------------
# Aliases de colunas. Os nomes são normalizados (sem acento, caixa, espaço e
# preposições), então "Preço do negócio" e "PrecoNegocio" casam igual.
# Inclui os nomes dos glossários de dados públicos da B3 e variações prováveis
# de rótulos em português. Se o arquivo real vier diferente, use `overrides`.
# ---------------------------------------------------------------------------
ALIASES = {
    "negocios": {
        "data": ["DataReferencia", "RptDt", "DataDoRelatorio", "Data de referência", "Data"],
        "ticker": ["CodigoInstrumento", "TckrSymb", "Instrumento financeiro", "Instrumento",
                   "Código do instrumento", "Código de negociação", "Ticker", "Símbolo"],
        "acao": ["AcaoAtualizacao", "UpdActn", "Ação de atualização", "Ação"],
        "preco": ["PrecoNegocio", "TradPric", "GrossTradeAmount", "Preço do negócio", "Preço"],
        "qtd": ["QuantidadeNegociada", "TradQty", "TradeQuantity", "Quantidade negociada", "Quantidade"],
        "hora": ["HoraFechamento", "NtryTm", "EntryTime", "Hora do fechamento", "Hora do negócio",
                 "Horário do negócio", "Hora", "Horário"],
        "id_negocio": ["CodigoIdentificadorNegocio", "TradId", "TradeIdentification", "NumeroDoNegocio",
                       "Código identificador do negócio", "Número do negócio", "Identificador do negócio"],
        "sessao": ["TipoSessaoPregao", "TradgSsnId", "TradingSessionIdentification",
                   "Tipo de sessão do pregão", "Tipo de sessão", "Sessão"],
        "data_negocio": ["DataNegocio", "TradDt", "TradeDate", "DataDoPregao", "Data do negócio", "Data do pregão"],
        "comprador": ["CodigoParticipanteComprador", "BuyerParticipantCode", "BuyrPtcptCd",
                      "Código do participante comprador", "Participante comprador", "Corretora compradora", "Comprador"],
        "vendedor": ["CodigoParticipanteVendedor", "SellerParticipantCode", "SellrPtcptCd",
                     "Código do participante vendedor", "Participante vendedor", "Corretora vendedora", "Vendedor"],
    },
    "posicoes": {
        "data": ["RptDt", "DataDoRelatorio", "DataReferencia", "Data"],
        "ticker": ["TckrSymb", "CodigoInstrumento", "Instrumento financeiro", "Instrumento", "Série", "Ticker"],
        "ativo": ["Asst", "Ativo", "Mercadoria"],
        "oi": ["OpnIntrst", "OpenInterest", "Posição em aberto", "Contratos em aberto", "Em aberto"],
        "var_oi": ["VartnOpnIntrst", "VariationOpenInterest", "Variação de contratos em aberto",
                   "Variação da posição em aberto", "Variação"],
        "coberta": ["CvrdQty", "QuantidadeCoberta", "CoveredQuantity", "Posição coberta", "Quantidade coberta",
                    "Coberta", "Coberto"],
        "travada": ["TtlBlckdPos", "TotalDePosicoesBloqueadas", "TotalDePosicoesTravadas", "TotalBlockedPosition",
                    "Posição travada", "Quantidade travada", "Total de posições bloqueadas", "Posições bloqueadas",
                    "Travada", "Trava", "Bloqueada"],
        "descoberta": ["UcvrdQty", "QuantidadeDescoberta", "UncoveredQuantity", "Posição descoberta", "Quantidade descoberta",
                       "Descoberta", "Descoberto"],
        "total": ["TtlPos", "TotalDePosicoes", "TotalPosition", "Total de posições", "Total"],
        # Campos de quantidade de clientes do arquivo de posições (BrrwrQty/LndrQty).
        # Em opções, CONFIRME no primeiro arquivo real qual lado cada um representa.
        "n_clientes_1": ["BrrwrQty", "QuanditdadeDeTomadores", "QuantidadeDeTomadores", "BorrowerQuantity",
                         "Tomadores", "Titulares", "Quantidade de titulares"],
        "n_clientes_2": ["LndrQty", "QuandidaDeDoadores", "QuantidadeDeDoadores", "LenderQuantity",
                         "Doadores", "Lançadores", "Quantidade de lançadores"],
    },
    "cadastro": {
        "data": ["RptDt", "DataDoRelatorio", "DataReferencia", "Data"],
        "ticker": ["TckrSymb", "CodigoInstrumento", "Instrumento financeiro", "Instrumento", "Ticker"],
        "subjacente": ["UndrlygTckrSymb1", "UnderlyingTickerSymbol1", "Símbolo subjacente 1", "Código do ativo subjacente",
                       "Ativo subjacente"],
        "ativo": ["Asst", "Ativo", "Ativo objeto", "Mercadoria"],
        "tipo_opcao": ["OptnTp", "OptionType", "Tipo de opção", "Tipo opção"],
        "strike": ["ExrcPric", "ExercisePrice", "Preço de exercício", "Strike"],
        "vencimento": ["XprtnDt", "ExpirationDate", "Data de expiração", "Data de vencimento", "Vencimento"],
        "estilo": ["OptnStyle", "OptionStyle", "Estilo da opção", "Estilo"],
        "categoria": ["SctyCtgyNm", "SecurityCategoryName", "Categoria do instrumento", "Categoria"],
        "segmento": ["SgmtNm", "SegmentName", "Segmento"],
        "mercado": ["MktNm", "MarketName", "Mercado"],
        "multiplicador": ["CtrctMltplr", "ContractMultiplier", "Multiplicador do contrato", "Multiplicador"],
    },
    "consolidado": {
        "data": ["RptDt", "DataDoRelatorio", "DataReferencia", "Data"],
        "ticker": ["TckrSymb", "CodigoInstrumento", "Instrumento financeiro", "Instrumento", "Ticker"],
        "preco_min": ["MinPric", "Preço mínimo"],
        "preco_max": ["MaxPric", "Preço máximo"],
        "preco_medio": ["TradAvrgPric", "Preço médio"],
        "preco_ult": ["LastPric", "Último preço", "Preço último", "Último negócio", "Preço de fechamento", "Fechamento"],
        "osc": ["OscnPctg", "Oscilação"],
        "n_negocios": ["TradQty", "Quantidade de negócios", "Número de negócios", "Negócios"],
        "qtd": ["FinInstrmQty", "Quantidade de instrumentos", "Quantidade de contratos negociados", "Contratos negociados",
                "Quantidade negociada", "Quantidade de contratos", "Quantidade"],
        "volume": ["NtlFinVol", "Volume financeiro", "Volume financeiro (R$)", "Volume financeiro negociado",
                   "Volume negociado", "Valor negociado", "Volume (R$)", "Volume"],
    },
}

# Campos mínimos para reconhecer cada tipo de arquivo (tuplas = alternativas)
CAMPOS_MINIMOS = {
    "negocios": [("ticker",), ("preco",), ("qtd",), ("hora",), ("comprador",), ("vendedor",)],
    "posicoes": [("ticker",), ("oi", "total"), ("coberta",), ("descoberta",)],
    "cadastro": [("ticker",), ("strike",), ("vencimento",)],
    "consolidado": [("ticker",), ("preco_ult", "preco_medio"), ("volume", "qtd")],
}
