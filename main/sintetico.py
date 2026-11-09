"""Pregão SINTÉTICO no layout dos arquivos de dados públicos da B3, com eventos plantados.

Serve para testar o pipeline antes de ter arquivos reais. Preços, volumes e códigos
de corretora são inventados.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .precificacao import FERIADOS_ANBIMA, bs, dias_uteis, taxa_continua

LETRAS_CALL, LETRAS_PUT = "ABCDEFGHIJKL", "MNOPQRSTUVWX"

ATIVOS = {
    "PETR4": dict(raiz="PETR", S=38.0, vol=0.32, passo=1.0, kmin=30, kmax=46, volume=1.6e9),
    "VALE3": dict(raiz="VALE", S=62.0, vol=0.28, passo=2.0, kmin=50, kmax=74, volume=1.2e9),
    "ITUB4": dict(raiz="ITUB", S=36.0, vol=0.22, passo=1.0, kmin=30, kmax=42, volume=0.9e9),
    "BOVA11": dict(raiz="BOVA", S=130.0, vol=0.20, passo=2.0, kmin=116, kmax=144, volume=0.7e9),
}

CORRETORAS_SINTETICAS = {
    9001: ("Formador A", "formador"), 9002: ("Formador B", "formador"),
    1001: ("Varejo A", "varejo"), 1002: ("Varejo B", "varejo"), 1003: ("Varejo C", "varejo"), 1004: ("Varejo D", "varejo"),
    2001: ("Estrangeira A", "estrangeira"), 2002: ("Institucional A", "institucional"),
    2003: ("Tesouraria A", "tesouraria"), 2004: ("Institucional B", "institucional"),
    2005: ("Institucional C", "institucional"), 2006: ("Tesouraria B", "tesouraria"),
    3001: ("Institucional D", "institucional"), 3002: ("Institucional E", "institucional"),
    3003: ("Institucional F", "institucional"),
}

# (tipo_evento, ativo, corretora, estrutura esperada)
ESPERADO = [
    ("estrutura", "PETR4", 2001, "risk reversal de baixa"),
    ("estrutura", "VALE3", 2002, "trava de alta com calls"),
    ("estrutura", "BOVA11", 2003, "box comprado (aplicação)"),
    ("estrutura", "PETR4", 2002, "rolagem com troca de strike de posição comprada"),
    ("estrutura", "ITUB4", 2001, "straddle vendido"),
    ("estrutura", "PETR4", 2005, "conversão"),
    ("direto", "VALE3", 3001, "bloco direto (mesma corretora nos dois lados)"),
    ("bloco", "ITUB4", 2004, "compra de call"),
    ("bloco", "VALE3", 2005, "venda de put"),      # E7: 10 lotes pequenos somados no dia
    ("direto", "ITUB4", 3002, "box (direto)"),      # E10: box cruzado na mesma corretora
    ("estrutura", "PETR4", 2006, "rolagem de posição vendida"),   # E11: pernas em horários diferentes
    ("estrutura", "VALE3", 2004, "rolagem de posição vendida (confirmada pelo ΔOI)"),   # E12: calendário + ΔOI
    ("direto", "BOVA11", 3003, "sintético (direto)"),   # E13: call e put cruzadas em horários diferentes
]


def terceira_sexta(ano: int, mes: int) -> pd.Timestamp:
    ini = pd.Timestamp(ano, mes, 1)
    v = pd.date_range(ini, ini + pd.offsets.MonthEnd(0), freq="W-FRI")[2]
    feriados = set(pd.to_datetime(FERIADOS_ANBIMA))
    while v in feriados or v.weekday() >= 5:     # feriado: antecipa (convenção do sintético)
        v -= pd.Timedelta(days=1)
    return v


def ticker_opcao(raiz: str, tipo: str, venc: pd.Timestamp, strike: float) -> str:
    letra = (LETRAS_CALL if tipo == "C" else LETRAS_PUT)[venc.month - 1]
    return f"{raiz}{letra}{int(round(strike * 10))}"


def _vol(base, S, K):
    x = np.log(K / S)
    return base - 0.25 * x + 0.6 * x * x


def _ms(h, m, s, mmm=0):
    return ((h * 60 + m) * 60 + s) * 1000 + mmm


def _br(x, nd=2):
    return f"{x:.{nd}f}".replace(".", ",")


def gerar(pasta_base="dados_sinteticos", data="2026-11-16", data_anterior="2026-11-13",
          seed: int = 7, taxa: float = 0.14) -> dict:
    rng = np.random.default_rng(seed)
    data, d1 = pd.Timestamp(data), pd.Timestamp(data_anterior)
    vencs, ano, mes = [], data.year, data.month
    while len(vencs) < 3:
        v = terceira_sexta(ano, mes)
        if v > data:
            vencs.append(v)
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    nov, dez, jan = vencs
    r = taxa_continua(taxa)

    # ---------------- séries -------------------------------------------------
    linhas = []
    for ativo, a in ATIVOS.items():
        for v in vencs:
            du = float(dias_uteis(data, v)[0])
            T = max(du, 0.5) / 252
            for K in np.arange(a["kmin"], a["kmax"] + 1e-9, a["passo"]):
                for tipo in ("C", "P"):
                    sig = _vol(a["vol"], a["S"], K)
                    preco = float(bs(a["S"], K, T, r, sig, tipo == "C")[0])
                    linhas.append(dict(ticker=ticker_opcao(a["raiz"], tipo, v, K), ativo=ativo, raiz=a["raiz"],
                                       tipo=tipo, strike=float(K), venc=v, preco=max(round(preco, 2), 0.01)))
    S = pd.DataFrame(linhas).set_index("ticker")
    tk = ticker_opcao

    # ---------------- negócios -----------------------------------------------
    negs, seq = [], {}

    def add(t, preco, qtd, ms, comprador, vendedor, acao=0, tid=None):
        if tid is None:
            seq[t] = seq.get(t, 0) + 10
            tid = seq[t]
        negs.append(dict(ticker=t, preco=round(max(float(preco), 0.01), 2), qtd=int(qtd), ms=int(ms),
                         comprador=int(comprador), vendedor=int(vendedor), acao=acao, id=tid))
        return tid

    def P(t):
        return S.loc[t, "preco"]

    MM, VAR, INST = [9001, 9002], [1001, 1002, 1003, 1004], [2001, 2002, 2003, 2004, 2005, 2006, 3001]
    abre, fecha = _ms(10, 0, 0), _ms(17, 54, 59, 999)
    peso_venc = {nov: 0.5, dez: 0.4, jan: 0.1}
    for ativo, a in ATIVOS.items():
        sub = S[S["ativo"] == ativo]
        w = np.exp(-(np.log(sub["strike"] / a["S"]) / 0.08) ** 2).to_numpy() * sub["venc"].map(peso_venc).to_numpy()
        for j in rng.choice(len(sub), size=2500, p=w / w.sum()):
            t = sub.index[j]
            mm_, va = int(rng.choice(MM)), int(rng.choice(VAR))
            c, v = (mm_, va) if rng.random() < 0.5 else (va, mm_)
            add(t, sub["preco"].iloc[j] * (1 + rng.normal(0, 0.03)), 100 * rng.integers(1, 51),
                rng.integers(abre, fecha), c, v)
        todos = MM + VAR + INST
        for _ in range(4000):
            c, v = rng.choice(todos, size=2, replace=False)
            add(ativo, a["S"] * (1 + rng.normal(0, 0.003)), 100 * rng.integers(1, 31), rng.integers(abre, fecha), c, v)

    def fills(t, total, n, t0, comprador, vendedor):
        for _ in range(n):
            add(t, P(t), total // n, t0, comprador, vendedor)

    # E1 PETR4: 2001 compra put 34 e vende call 42 (dez) contra formador -> risk reversal de baixa
    fills(tk("PETR", "P", dez, 34), 2_000_000, 4, _ms(11, 15, 3, 120), 2001, 9001)
    fills(tk("PETR", "C", dez, 42), 2_000_000, 4, _ms(11, 15, 3, 410), 9001, 2001)
    # E2 VALE3: 2002 compra call 62 de um formador e vende call 68 a outro -> trava de alta
    fills(tk("VALE", "C", dez, 62), 1_000_000, 2, _ms(14, 2, 10, 5), 2002, 9001)
    fills(tk("VALE", "C", dez, 68), 1_000_000, 2, _ms(14, 2, 10, 250), 9002, 2002)
    # E3 BOVA11: box 124/136 entre 2003 e 2004 -> financiamento
    t0 = _ms(15, 30, 0)
    add(tk("BOVA", "C", dez, 124), P(tk("BOVA", "C", dez, 124)), 300_000, t0, 2003, 2004)
    add(tk("BOVA", "C", dez, 136), P(tk("BOVA", "C", dez, 136)), 300_000, t0, 2004, 2003)
    add(tk("BOVA", "P", dez, 136), P(tk("BOVA", "P", dez, 136)), 300_000, t0, 2003, 2004)
    add(tk("BOVA", "P", dez, 124), P(tk("BOVA", "P", dez, 124)), 300_000, t0, 2004, 2003)
    # E4 PETR4: 2002 vende call 38 (nov, 3 DU) e compra call 40 (dez) -> rolagem de posição comprada
    add(tk("PETR", "C", nov, 38), P(tk("PETR", "C", nov, 38)), 800_000, _ms(10, 45, 20, 100), 9002, 2002)
    add(tk("PETR", "C", dez, 40), P(tk("PETR", "C", dez, 40)), 800_000, _ms(10, 45, 20, 150), 2002, 9002)
    # E5 ITUB4: 2001 vende call e put 36 (dez) -> straddle vendido
    add(tk("ITUB", "C", dez, 36), P(tk("ITUB", "C", dez, 36)), 500_000, _ms(13, 20, 45, 500), 9002, 2001)
    add(tk("ITUB", "P", dez, 36), P(tk("ITUB", "P", dez, 36)), 500_000, _ms(13, 20, 45, 700), 9002, 2001)
    # E6 VALE3: direto 3001 x 3001, put 56 + call 68 (no ΔOI: put descoberta, call coberta)
    add(tk("VALE", "P", dez, 56), P(tk("VALE", "P", dez, 56)), 1_500_000, _ms(16, 5, 0), 3001, 3001)
    add(tk("VALE", "C", dez, 68), P(tk("VALE", "C", dez, 68)), 1_500_000, _ms(16, 5, 0), 3001, 3001)
    # E7 VALE3: 2005 vende put 58 a seco em 10 lotes ao longo de 40 min (só aparece no fluxo e no ΔOI)
    for k in range(10):
        add(tk("VALE", "P", dez, 58), P(tk("VALE", "P", dez, 58)), 250_000, _ms(15, 0, 0) + k * 240_000,
            int(rng.choice(MM)), 2005)
    # E8 ITUB4: 2004 compra 3 mi de calls 38 de corretora de varejo -> bloco isolado
    add(tk("ITUB", "C", dez, 38), P(tk("ITUB", "C", dez, 38)), 3_000_000, _ms(12, 0, 0), 2004, 1001)
    # E9 PETR4: conversão entre 2005 e 2006 (ação + put 38 comprada + call 38 vendida)
    add("PETR4", 38.00, 1_000_000, _ms(16, 30, 0, 0), 2005, 2006)
    add(tk("PETR", "P", dez, 38), P(tk("PETR", "P", dez, 38)), 1_000_000, _ms(16, 30, 0, 50), 2005, 2006)
    add(tk("PETR", "C", dez, 38), P(tk("PETR", "C", dez, 38)), 1_000_000, _ms(16, 30, 0, 90), 2006, 2005)
    # E10 ITUB4: box 33/39 cruzado na mesma corretora (3002 x 3002) -> financiamento (direto)
    for tipo_, k_ in (("C", 33), ("C", 39), ("P", 33), ("P", 39)):
        t_ = tk("ITUB", tipo_, dez, k_)
        add(t_, P(t_), 2_000_000, _ms(11, 0, 0), 3002, 3002)
    # E11 PETR4: 2006 recompra calls 39 de nov (10h20) e vende calls 39 de dez (13h40) -> rolagem montada no dia
    add(tk("PETR", "C", nov, 39), P(tk("PETR", "C", nov, 39)), 2_000_000, _ms(10, 20, 0), 2006, 9001)
    add(tk("PETR", "C", dez, 39), P(tk("PETR", "C", dez, 39)), 2_000_000, _ms(13, 40, 0), 9002, 2006)
    # E12 VALE3: 2004 recompra calls 64 de dez (11h30) e vende calls 64 de jan (15h10): calendário com DU > 5
    #     que o ΔOI (dez encerra, jan abre) confirma como rolagem de call vendida — o caso do dia 28/10
    add(tk("VALE", "C", dez, 64), P(tk("VALE", "C", dez, 64)), 1_500_000, _ms(11, 30, 0), 2004, 9001)
    add(tk("VALE", "C", jan, 64), P(tk("VALE", "C", jan, 64)), 1_500_000, _ms(15, 10, 0), 9002, 2004)
    # E13 BOVA11: 3003 cruza calls 130 (10h30) e puts 130 (14h) de dez separadamente -> sintético no dia
    add(tk("BOVA", "C", dez, 130), P(tk("BOVA", "C", dez, 130)), 1_000_000, _ms(10, 30, 0), 3003, 3003)
    add(tk("BOVA", "P", dez, 130), P(tk("BOVA", "P", dez, 130)), 1_000_000, _ms(14, 0, 0), 3003, 3003)
    # negócio cancelado: não pode aparecer em nada
    t_c = tk("PETR", "C", dez, 44)
    tid = add(t_c, P(t_c), 1_500_000, _ms(10, 10, 10), 2003, 1002)
    add(t_c, P(t_c), 1_500_000, _ms(10, 10, 10), 2003, 1002, acao=2, tid=tid)

    neg = pd.DataFrame(negs).sort_values("ms", kind="mergesort").reset_index(drop=True)

    pasta_d, pasta_a = Path(pasta_base) / data.strftime("%Y-%m-%d"), Path(pasta_base) / d1.strftime("%Y-%m-%d")
    pasta_d.mkdir(parents=True, exist_ok=True)
    pasta_a.mkdir(parents=True, exist_ok=True)
    ds, d1s = data.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")

    # negócio a negócio (layout TradeIntradayFile, ';', decimal com vírgula, linha de título antes do cabeçalho)
    def hhmmssmmm(ms):
        h, rr = divmod(ms, 3_600_000)
        m, rr = divmod(rr, 60_000)
        s, mmm = divmod(rr, 1000)
        return f"{h:02d}{m:02d}{s:02d}{mmm:03d}".lstrip("0")

    with open(pasta_d / "negocio_a_negocio_listados.csv", "w", encoding="utf-8") as f:
        f.write(f"Negócio a negócio - Listados - {data:%d/%m/%Y} (SINTÉTICO)\n")
        f.write("DataReferencia;CodigoInstrumento;AcaoAtualizacao;PrecoNegocio;QuantidadeNegociada;HoraFechamento;"
                "CodigoIdentificadorNegocio;TipoSessaoPregao;DataNegocio;CodigoParticipanteComprador;"
                "CodigoParticipanteVendedor\n")
        for r_ in neg.itertuples(index=False):
            f.write(f"{ds};{r_.ticker};{r_.acao};{_br(r_.preco)};{r_.qtd};{hhmmssmmm(r_.ms)};{r_.id};1;{ds};"
                    f"{r_.comprador};{r_.vendedor}\n")

    # negócios consolidados (layout TradeInformationConsolidated)
    ok = neg[~neg.set_index(["ticker", "id"]).index.isin(neg.loc[neg["acao"] == 2].set_index(["ticker", "id"]).index)]
    ok = ok.assign(fin=ok["preco"] * ok["qtd"])
    g = ok.sort_values("ms").groupby("ticker").agg(MinPric=("preco", "min"), MaxPric=("preco", "max"),
                                                   LastPric=("preco", "last"), TradQty=("preco", "size"),
                                                   FinInstrmQty=("qtd", "sum"), NtlFinVol=("fin", "sum"))
    g["TradAvrgPric"] = g["NtlFinVol"] / g["FinInstrmQty"]
    for ativo, a in ATIVOS.items():
        g.loc[ativo, ["LastPric", "TradAvrgPric", "NtlFinVol"]] = [a["S"], a["S"], a["volume"]]
        g.loc[ativo, "FinInstrmQty"] = round(a["volume"] / a["S"])
    with open(pasta_d / "negocios_consolidados.csv", "w", encoding="utf-8") as f:
        f.write("RptDt;TckrSymb;ISIN;SgmtNm;MinPric;MaxPric;TradAvrgPric;LastPric;OscnPctg;AdjstdQt;AdjstdQtTax;"
                "RefPric;TradQty;FinInstrmQty;NtlFinVol\n")
        for t, r_ in g.iterrows():
            seg = "CASH" if t in ATIVOS else ("EQUITY CALL" if S.loc[t, "tipo"] == "C" else "EQUITY PUT")
            f.write(f"{ds};{t};BRSINT{abs(hash(t)) % 10**6:06d};{seg};{_br(r_.MinPric)};{_br(r_.MaxPric)};"
                    f"{_br(r_.TradAvrgPric, 4)};{_br(r_.LastPric)};;;;;{int(r_.TradQty)};{int(r_.FinInstrmQty)};"
                    f"{_br(r_.NtlFinVol)}\n")

    # cadastro de instrumentos (subconjunto do layout InstrumentsConsolidated)
    with open(pasta_d / "cadastro_instrumentos.csv", "w", encoding="utf-8") as f:
        f.write("RptDt;TckrSymb;Asst;AsstDesc;SgmtNm;MktNm;SctyCtgyNm;XprtnDt;OptnTp;CtrctMltplr;ExrcPric;OptnStyle\n")
        for ativo, a in ATIVOS.items():
            f.write(f"{ds};{ativo};{a['raiz']};{ativo} SINTETICO;CASH;CASH;SHARES;;;;;\n")
        for t, r_ in S.iterrows():
            c = r_.tipo == "C"
            f.write(f"{ds};{t};{r_.raiz};{r_.ativo} SINTETICO;{'EQUITY CALL' if c else 'EQUITY PUT'};OPTIONS;"
                    f"OPTION ON EQUITIES;{r_.venc:%Y-%m-%d};{'Call' if c else 'Put'};1;{_br(r_.strike)};"
                    f"{'AMER' if c else 'EURO'}\n")

    # posições em aberto (layout DerivativesOpenPosition) em D-1 e D
    n = len(S)
    oi = rng.uniform(50_000, 2_000_000, n) // 100 * 100
    cob = np.where(S["tipo"].to_numpy() == "C", oi * rng.uniform(0.1, 0.4, n) // 100 * 100, 0.0)
    trav = oi * rng.uniform(0.05, 0.25, n) // 100 * 100
    a1 = pd.DataFrame({"oi": oi, "cob": cob, "trav": trav, "n1": rng.integers(5, 300, n),
                       "n2": rng.integers(5, 200, n)}, index=S.index)
    plant = {
        tk("PETR", "P", dez, 34): (0, 0, 2_000_000, 1, 1),
        tk("PETR", "C", dez, 42): (2_000_000, 0, 0, 1, 1),
        tk("VALE", "C", dez, 62): (0, 0, 1_000_000, 1, 1),
        tk("VALE", "C", dez, 68): (1_500_000, 1_000_000, 0, 2, 2),
        tk("BOVA", "C", dez, 124): (0, 300_000, 0, 1, 1),
        tk("BOVA", "C", dez, 136): (0, 300_000, 0, 1, 1),
        tk("BOVA", "P", dez, 136): (0, 300_000, 0, 1, 1),
        tk("BOVA", "P", dez, 124): (0, 300_000, 0, 1, 1),
        tk("PETR", "C", nov, 38): (0, 0, -800_000, -1, -1),
        tk("PETR", "C", dez, 40): (0, 0, 800_000, 1, 1),
        tk("ITUB", "C", dez, 36): (0, 0, 500_000, 1, 1),
        tk("ITUB", "P", dez, 36): (0, 0, 500_000, 1, 1),
        tk("VALE", "P", dez, 56): (0, 0, 1_500_000, 1, 1),
        tk("VALE", "P", dez, 58): (0, 0, 2_500_000, 2, 1),
        tk("ITUB", "C", dez, 38): (3_000_000, 0, 0, 1, 1),
        tk("PETR", "C", dez, 38): (1_000_000, 0, 0, 1, 1),
        tk("PETR", "P", dez, 38): (0, 0, 1_000_000, 1, 1),
        tk("ITUB", "C", dez, 33): (0, 2_000_000, 0, 1, 1),
        tk("ITUB", "C", dez, 39): (0, 2_000_000, 0, 1, 1),
        tk("ITUB", "P", dez, 33): (0, 2_000_000, 0, 1, 1),
        tk("ITUB", "P", dez, 39): (0, 2_000_000, 0, 1, 1),
        tk("PETR", "C", nov, 39): (0, 0, -2_000_000, -1, -1),
        tk("PETR", "C", dez, 39): (0, 0, 2_000_000, 1, 1),
        tk("VALE", "C", dez, 64): (0, 0, -1_500_000, -1, -1),
        tk("VALE", "C", jan, 64): (0, 0, 1_500_000, 1, 1),
        tk("BOVA", "C", dez, 130): (0, 0, 1_000_000, 1, 1),   # E13: sintético cruzado aparece no OI
        tk("BOVA", "P", dez, 130): (0, 0, 1_000_000, 1, 1),
    }
    for t in plant:
        a1.loc[t, ["oi", "cob", "trav"]] = [600_000, 200_000 if S.loc[t, "tipo"] == "C" else 0, 100_000]
    a1.loc[tk("PETR", "C", nov, 38), ["oi", "cob", "trav"]] = [6_800_000, 1_500_000, 1_000_000]
    a1.loc[tk("PETR", "P", nov, 38), ["oi", "cob", "trav"]] = [4_000_000, 0, 800_000]
    a1.loc[tk("PETR", "C", nov, 39), ["oi", "cob", "trav"]] = [3_000_000, 500_000, 300_000]
    a1.loc[tk("VALE", "C", dez, 64), ["oi", "cob", "trav"]] = [2_000_000, 200_000, 100_000]
    a1["desc"] = a1["oi"] - a1["cob"] - a1["trav"]

    a0 = a1.copy()
    ruido = ~a0.index.isin(list(plant))
    d = (a0["oi"] * rng.uniform(-0.005, 0.005, n) // 100 * 100).clip(lower=-a0["desc"])
    a0.loc[ruido, "desc"] += d[ruido]
    a0.loc[ruido, "oi"] += d[ruido]
    a0.loc[ruido, "n1"] = (a0.loc[ruido, "n1"] + rng.integers(-2, 3, ruido.sum())).clip(lower=1)
    a0.loc[ruido, "n2"] = (a0.loc[ruido, "n2"] + rng.integers(-2, 3, ruido.sum())).clip(lower=1)
    for t, (dc, dt, dd, dn1, dn2) in plant.items():
        a0.loc[t, ["cob", "trav", "desc"]] += [dc, dt, dd]
        a0.loc[t, "oi"] += dc + dt + dd
        a0.loc[t, ["n1", "n2"]] += [dn1, dn2]

    def escrever_oi(df, pasta, dstr, var):
        with open(pasta / "posicoes_em_aberto.csv", "w", encoding="utf-8") as f:
            f.write("RptDt;TckrSymb;ISIN;Asst;XprtnCd;SgmtNm;OpnIntrst;VartnOpnIntrst;DstrbtnId;CvrdQty;"
                    "TtlBlckdPos;UcvrdQty;TtlPos;BrrwrQty;LndrQty;CurQty;FwdPric\n")
            for t, r_ in df.iterrows():
                f.write(f"{dstr};{t};;{S.loc[t, 'raiz']};;OPTIONS;{int(r_.oi)};{int(var[t])};;{int(r_.cob)};"
                        f"{int(r_.trav)};{int(r_.desc)};{int(r_.oi)};{int(r_.n1)};{int(r_.n2)};;\n")

    escrever_oi(a1, pasta_a, d1s, pd.Series(0, index=a1.index))
    escrever_oi(a0, pasta_d, ds, a0["oi"] - a1["oi"])
    return {"pasta_dia": str(pasta_d), "pasta_anterior": str(pasta_a), "data": ds,
            "corretoras": CORRETORAS_SINTETICAS, "esperado": ESPERADO}
