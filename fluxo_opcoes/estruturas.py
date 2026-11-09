"""Reconstrução de estruturas multi-perna, blocos isolados e negócios diretos.

Lógica: cada negócio é visto do lado do comprador (+) e do vendedor (−). Para cada
corretora que não seja de alto giro/formador, negócios no mesmo ativo-objeto dentro
de uma janela curta formam um agrupamento. Pernas com quantidades proporcionais
(1:1, 1:2...) viram estrutura e são classificadas pela assinatura das pernas.
Negócios diretos (mesma corretora nos dois lados) são tratados à parte: a forma
da estrutura fica indeterminada, porque não se sabe qual cliente fez qual lado.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CLASSES_EXCLUIDAS, RANK_CLASSE, Parametros

ALTA, BAIXA = "alta", "baixa"


# ---------------------------------------------------------------------------
# formatação
# ---------------------------------------------------------------------------
def _br(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_qtd(q: float) -> str:
    q = abs(float(q))
    if q >= 1e9:
        return f"{_br(q / 1e9, 2)} bi"
    if q >= 1e6:
        return f"{_br(q / 1e6, 2)} mi"
    if q >= 1e3:
        return f"{_br(q / 1e3, 0)} mil"
    return _br(q, 0)


def fmt_sinal(v: float) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/d"
    return ("+" if v >= 0 else "−") + fmt_qtd(v)


def fmt_rs(v: float) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/d"
    s = "−" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{s}R$ {_br(a / 1e9, 1)} bi"
    if a >= 1e6:
        return f"{s}R$ {_br(a / 1e6, 1)} mi"
    return f"{s}R$ {_br(a / 1e3, 0)} mil"


def hora_txt(ms: int) -> str:
    ms = int(ms)
    h, resto = divmod(ms, 3_600_000)
    m, resto = divmod(resto, 60_000)
    s, mm = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{mm:03d}"


def _desc_perna(l: dict, com_sinal: bool = True) -> str:
    q = l["q"] if com_sinal else l["bruto"]
    sinal = ("+" if q > 0 else "−") if com_sinal else ""
    preco = _br(l["vwap"], 2)
    if l["tipo"] == "S":
        return f"{sinal}{fmt_qtd(q)} {l['ticker']} (ação) @ {preco}"
    venc = pd.Timestamp(l["vencimento"]).strftime("%d/%m/%y")
    return f"{sinal}{fmt_qtd(q)} {l['ticker']} ({l['tipo']} {_br(l['strike'], 2)} {venc}) @ {preco}"


# ---------------------------------------------------------------------------
# classificação
# ---------------------------------------------------------------------------
def _mesmo(a: dict, b: dict, campo: str) -> bool:
    if campo == "strike":
        return abs(float(a["strike"]) - float(b["strike"])) < 1e-6
    return pd.Timestamp(a["vencimento"]) == pd.Timestamp(b["vencimento"])


def _leitura_delta(dn: float) -> str:
    return (ALTA if dn > 0 else BAIXA) + " (pelo delta líquido)"


def _duas(ops, du_rol, dn):
    a, b = sorted(ops, key=lambda p: (p["tipo"], pd.Timestamp(p["vencimento"]), p["strike"]))
    mv, mk = _mesmo(a, b, "vencimento"), _mesmo(a, b, "strike")
    opostos = np.sign(a["q"]) != np.sign(b["q"])
    razao = max(abs(a["q"]), abs(b["q"])) / min(abs(a["q"]), abs(b["q"]))
    pre = "ratio " if razao > 1.05 else ""
    if a["tipo"] == b["tipo"]:
        t = a["tipo"]
        nome_t = "calls" if t == "C" else "puts"
        if not opostos:
            comprado = a["q"] > 0
            leitura = (ALTA if comprado else BAIXA) if t == "C" else (BAIXA if comprado else ALTA)
            return f"{'compra' if comprado else 'venda'} de duas séries de {nome_t}", leitura, False
        if mv and not mk:   # trava vertical; 'a' é o strike menor
            alta = a["q"] > 0
            return f"{pre}trava de {'alta' if alta else 'baixa'} com {nome_t}", ALTA if alta else BAIXA, False
        if not mv:          # calendário/diagonal; 'a' vence antes
            longe_comprado = b["q"] > 0
            if a["du"] <= du_rol:
                nome = "rolagem" + ("" if mk else " com troca de strike") + \
                       (" de posição comprada" if longe_comprado else " de posição vendida")
                if t == "C":
                    leitura = "alta (mantida)" if longe_comprado else "baixa/vol vendida (mantida)"
                else:
                    leitura = "baixa (mantida)" if longe_comprado else "alta/vol vendida (mantida)"
                return nome, leitura, False
            if mk:
                return f"calendário {'comprado' if longe_comprado else 'vendido'} com {nome_t}", "vol/estrutura a termo", False
            return f"diagonal com {nome_t}", _leitura_delta(dn), False
        return f"não classificada (2 pernas de {nome_t})", _leitura_delta(dn), False
    c, p = (a, b) if a["tipo"] == "C" else (b, a)
    if mv:
        if not opostos:
            comprado = c["q"] > 0
            base = "straddle" if mk else "strangle"
            return f"{pre}{base} {'comprado' if comprado else 'vendido'}", "vol comprada" if comprado else "vol vendida", False
        comprado = c["q"] > 0
        if mk:   # sem a perna em ação, quase sempre é perna de conversão/reversão (arbitragem de financiamento)
            return (f"sintético {'comprado' if comprado else 'vendido'}",
                    "provável perna de conversão/reversão (financiamento)", True)
        return f"{pre}risk reversal de {'alta' if comprado else 'baixa'}", ALTA if comprado else BAIXA, False
    return "call e put em vencimentos distintos", _leitura_delta(dn), False


def _tres(ops, dn):
    tipos = {o["tipo"] for o in ops}
    vencs = {pd.Timestamp(o["vencimento"]) for o in ops}
    if len(tipos) == 1 and len(vencs) == 1:
        a, b, c = sorted(ops, key=lambda p: p["strike"])
        if (abs(abs(b["q"]) - abs(a["q"]) - abs(c["q"])) <= 0.05 * abs(b["q"])
                and np.sign(a["q"]) == np.sign(c["q"]) != np.sign(b["q"])):
            comprada = a["q"] > 0
            return (f"borboleta {'comprada' if comprada else 'vendida'}",
                    "vol vendida (alvo no strike central)" if comprada else "vol comprada", False)
    if len(vencs) == 1 and tipos == {"C", "P"}:
        return "3 pernas call/put (seagull ou collar com trava)", _leitura_delta(dn), False
    return "não classificada (3 pernas)", _leitura_delta(dn), False


def _quatro(ops, dn):
    vencs = {pd.Timestamp(o["vencimento"]) for o in ops}
    calls = sorted([o for o in ops if o["tipo"] == "C"], key=lambda p: p["strike"])
    puts = sorted([o for o in ops if o["tipo"] == "P"], key=lambda p: p["strike"])
    if len(vencs) == 1 and len(calls) == 2 and len(puts) == 2:
        kc, kp = [o["strike"] for o in calls], [o["strike"] for o in puts]
        if np.allclose(kc, kp) and kc[0] < kc[1]:
            s = [np.sign(calls[0]["q"]), np.sign(calls[1]["q"]), np.sign(puts[1]["q"]), np.sign(puts[0]["q"])]
            if s == [1, -1, 1, -1]:
                return "box comprado (aplicação)", "financiamento", True
            if s == [-1, 1, -1, 1]:
                return "box vendido (captação)", "financiamento", True
        if kp[1] <= kc[0] + 1e-9:
            s = [np.sign(puts[0]["q"]), np.sign(puts[1]["q"]), np.sign(calls[0]["q"]), np.sign(calls[1]["q"])]
            nome = "iron butterfly" if abs(kp[1] - kc[0]) < 1e-9 else "iron condor"
            if s == [1, -1, -1, 1]:
                return f"{nome} vendido", "vol vendida", False
            if s == [-1, 1, 1, -1]:
                return f"{nome} comprado", "vol comprada", False
    if len(vencs) == 1 and (len(calls) == 4 or len(puts) == 4):
        s = [np.sign(o["q"]) for o in sorted(ops, key=lambda p: p["strike"])]
        if s == [1, -1, -1, 1]:
            return "condor comprado", "vol vendida", False
        if s == [-1, 1, 1, -1]:
            return "condor vendido", "vol comprada", False
    return "não classificada (4 pernas)", _leitura_delta(dn), False


def _com_acao(ops, s, dn):
    acao_comprada = s["q"] > 0
    if len(ops) == 2 and {o["tipo"] for o in ops} == {"C", "P"} and _mesmo(ops[0], ops[1], "vencimento"):
        c = next(o for o in ops if o["tipo"] == "C")
        p = next(o for o in ops if o["tipo"] == "P")
        if acao_comprada and p["q"] > 0 and c["q"] < 0:
            if _mesmo(c, p, "strike"):
                return "conversão", "financiamento", True
            return "collar (ação + put comprada + call vendida)", "proteção de posição comprada", False
        if not acao_comprada and p["q"] < 0 and c["q"] > 0:
            if _mesmo(c, p, "strike"):
                return "reversão", "financiamento", True
            return "collar reverso", "proteção de posição vendida", False
    if len(ops) == 1:
        o = ops[0]
        if o["tipo"] == "C":
            if acao_comprada and o["q"] < 0:
                if o["strike"] < 0.95 * s["S"]:
                    return "financiamento (ação + call ITM vendida)", "financiamento", True
                return "lançamento coberto", "renda/teto (neutro a alta)", False
            if not acao_comprada and o["q"] > 0:
                return "call comprada + ação vendida", "hedge delta", False
        else:
            if acao_comprada and o["q"] > 0:
                return "put protetora", "proteção de posição comprada", False
            if not acao_comprada and o["q"] < 0:
                return "put vendida + ação vendida", "hedge delta", False
    return "estrutura com ação (outra)", _leitura_delta(dn), False


def classificar(pernas: list[dict], du_rolagem: int = 5):
    """pernas: dicts com tipo ('C','P','S'), strike, vencimento, du, q (com sinal), dn, S.

    Retorna (estrutura, leitura, financiamento). financiamento=None quando ambíguo.
    """
    ops = [p for p in pernas if p["tipo"] in ("C", "P")]
    acoes = [p for p in pernas if p["tipo"] == "S"]
    dn = float(sum(p["dn"] for p in pernas))
    if acoes:
        if len(acoes) == 1:
            return _com_acao(ops, acoes[0], dn)
        return "estrutura com ação (outra)", _leitura_delta(dn), False
    if len(ops) == 2:
        return _duas(ops, du_rolagem, dn)
    if len(ops) == 3:
        return _tres(ops, dn)
    if len(ops) == 4:
        return _quatro(ops, dn)
    return f"não classificada ({len(ops)} pernas)", _leitura_delta(dn), False


def _leitura_bloco(l: dict):
    q = l["q"]
    if l["tipo"] == "C":
        return ("compra de call", ALTA, False) if q > 0 else ("venda de call", "baixa/vol vendida", False)
    if l["tipo"] == "P":
        return ("compra de put", "baixa/proteção", False) if q > 0 else ("venda de put", "alta/vol vendida", False)
    return "bloco em ação", ALTA if q > 0 else BAIXA, False


def direcao(leitura: str) -> int:
    if not isinstance(leitura, str):
        return 0
    if leitura.startswith(ALTA):
        return 1
    if leitura.startswith(BAIXA):
        return -1
    return 0


# ---------------------------------------------------------------------------
# motor de agrupamento
# ---------------------------------------------------------------------------
def _clusterizar(novo_grupo: np.ndarray, horas: np.ndarray, janela: int) -> np.ndarray:
    """Janela contada a partir do primeiro negócio do agrupamento (evita encadear o dia todo)."""
    n = len(horas)
    cid = np.empty(n, dtype=np.int64)
    c, inicio = -1, 0
    for i in range(n):
        h = horas[i]
        if novo_grupo[i] or h - inicio > janela:
            c += 1
            inicio = h
        cid[i] = c
    return cid


def _proporcional(qs, params: Parametros, tol: float | None = None) -> bool:
    tol = params.tol_razao if tol is None else tol
    a = np.abs(np.asarray(qs, dtype=float))
    base = a.min()
    if base <= 0:
        return False
    r = a / base
    k = np.round(r)
    return bool(np.all((k >= 1) & (k <= params.razao_max) & (np.abs(r - k) <= tol * k)))


def passa_tamanho(dn, premio, adtv, params: Parametros, min_pct: float | None = None):
    """Vetorizado. criterio_e: |Δ-notional| >= mínimo absoluto E >= fração do ADTV; ou prêmio >= min_premio."""
    dn, pr, ad = (np.asarray(x, dtype=float) for x in (dn, premio, adtv))
    min_pct = params.min_pct_adtv if min_pct is None else min_pct
    tem = np.isfinite(ad) & (ad > 0)
    pct_ok = np.where(tem, dn >= min_pct * np.where(tem, ad, 1.0), True)
    abs_ok = dn >= params.min_delta_notional
    por_delta = (abs_ok & pct_ok) if params.criterio_e else (abs_ok | (tem & pct_ok))
    return por_delta | (np.nan_to_num(pr) >= params.min_premio)


def info_financiamento(estrutura: str, legs: list[dict]):
    """(taxa a.a. base 252, valor no vencimento em R$, DU) de box, conversão/reversão e financiamento com call ITM.

    Taxa = (valor recebido no vencimento / desembolso hoje)^(252/DU) − 1, com o preço médio de cada perna.
    """
    nada = (np.nan, np.nan, np.nan)
    ops = [l for l in legs if l["tipo"] in ("C", "P")]
    acao = [l for l in legs if l["tipo"] == "S"]
    if not ops:
        return nada
    du = min(l["du"] for l in ops)
    if not du or du <= 0:
        return nada
    q = min(abs(l["q"]) for l in legs)
    mult = ops[0]["mult"]
    premio = sum(l["q"] * l["vwap"] * l["mult"] for l in ops)          # + = pago
    if estrutura.startswith("box"):
        ks = sorted({l["strike"] for l in ops})
        final, hoje = (ks[-1] - ks[0]) * q * mult, abs(premio)
    elif acao and (estrutura in ("conversão", "reversão") or estrutura.startswith("financiamento")):
        k = ops[0]["strike"] if estrutura.startswith("financiamento") else next(l["strike"] for l in ops if l["tipo"] == "C")
        final, hoje = k * q * mult, abs(acao[0]["q"] * acao[0]["vwap"] + premio)
    else:
        return nada
    if hoje <= 0 or final <= 0:
        return nada
    return float((final / hoje) ** (252.0 / du) - 1.0), float(final), float(du)


def taxa_financiamento(estrutura: str, legs: list[dict]) -> float:
    return info_financiamento(estrutura, legs)[0]


def _financiamento_direto(legs: list[dict]):
    """Padrões de financiamento num negócio direto, onde não se sabe o sinal de cada perna.

    box: 2 calls + 2 puts nos mesmos dois strikes; conversão/reversão: call + put no mesmo strike + ação;
    sintético: call + put no mesmo strike, sem ação. Quantidades iguais (±15%); pernas < 10% da maior são
    ignoradas. Devolve (nome, taxa, valor no vencimento em R$, DU) ou None.
    """
    mx = max(l["bruto"] for l in legs)
    princ = [l for l in legs if l["bruto"] >= 0.10 * mx]
    ops = [l for l in princ if l["tipo"] in ("C", "P")]
    ac = [l for l in princ if l["tipo"] == "S"]
    if not ops or max(l["bruto"] for l in princ) / min(l["bruto"] for l in princ) > 1.15:
        return None
    if len({pd.Timestamp(l["vencimento"]) for l in ops}) != 1:
        return None
    calls = sorted([l for l in ops if l["tipo"] == "C"], key=lambda l: l["strike"])
    puts = sorted([l for l in ops if l["tipo"] == "P"], key=lambda l: l["strike"])
    q, du, mult = min(l["bruto"] for l in ops), ops[0]["du"], ops[0]["mult"]
    if (not ac and len(calls) == 2 and len(puts) == 2 and calls[0]["strike"] < calls[1]["strike"]
            and np.allclose([c["strike"] for c in calls], [p["strike"] for p in puts])):
        nome, final = "box", calls[1]["strike"] - calls[0]["strike"]
        hoje = calls[0]["vwap"] - calls[1]["vwap"] + puts[1]["vwap"] - puts[0]["vwap"]
    elif len(calls) == 1 and len(puts) == 1 and abs(calls[0]["strike"] - puts[0]["strike"]) < 1e-6:
        if not ac:
            return "sintético", np.nan, np.nan, du
        if len(ac) != 1:
            return None
        nome, final = "conversão/reversão", calls[0]["strike"]
        hoje = ac[0]["vwap"] + puts[0]["vwap"] - calls[0]["vwap"]
    else:
        return None
    taxa = (final / hoje) ** (252.0 / du) - 1.0 if hoje > 0 and final > 0 and du and du > 0 else np.nan
    return nome, float(taxa), float(final * q * mult), du


def _evento(legs: list[dict], tipo_evento: str, params: Parametros, adtv: pd.Series, n_exec: int = 1,
            escopo: str = "janela") -> dict:
    l0 = legs[0]
    subj = l0["subjacente"]
    taxa = valor_final = du_fin = np.nan
    if tipo_evento == "estrutura":
        estrutura, leitura, fin = classificar(legs, params.du_rolagem)
        if fin:
            taxa, valor_final, du_fin = info_financiamento(estrutura, legs)
            if np.isfinite(taxa):
                leitura = f"financiamento a {_br(100 * taxa, 1)}% a.a."
    else:
        estrutura, leitura, fin = _leitura_bloco(l0)
    ordem = sorted(legs, key=lambda l: (l["tipo"] == "S", l["tipo"], pd.Timestamp(l["vencimento"]) if l["tipo"] != "S"
                                        else pd.Timestamp.min, l["strike"] if l["tipo"] != "S" else 0))
    return dict(
        tipo_evento=tipo_evento, subjacente=subj, hora_ms=int(min(l["hora_ms"] for l in legs)),
        corretora=int(l0["corretora"]), classe=l0["classe"],
        contrapartes=frozenset().union(*[l["contra"] for l in legs]),
        n_pernas=len(legs), n_execucoes=int(n_exec), estrutura=estrutura, leitura=leitura, financiamento=fin,
        taxa_implicita=taxa, valor_final=valor_final, du_fin=du_fin, escopo=escopo,
        dn_liquido=float(sum(l["dn"] for l in legs)), dn_bruto=float(sum(abs(l["dn"]) for l in legs)),
        premio_liquido=float(sum(l["premio"] for l in legs if l["tipo"] != "S")),
        premio_bruto=float(sum(abs(l["premio"]) for l in legs if l["tipo"] != "S")),
        adtv=float(adtv.get(subj, np.nan)),
        pernas_txt=" | ".join(_desc_perna(l) for l in ordem), pernas=ordem, ids=frozenset().union(*[l["ids"] for l in legs]),
    )


_ATTR = ["ticker", "tipo", "strike", "vencimento", "du", "delta", "S", "mult", "preco", "qtd", "dn", "premio"]
_AGG_PERNA = dict(subjacente=("subjacente", "first"), corretora=("corretora", "first"), classe=("classe", "first"),
                  tipo=("tipo", "first"), strike=("strike", "first"), vencimento=("vencimento", "first"),
                  du=("du", "first"), delta=("delta", "first"), S=("S", "first"), mult=("mult", "first"),
                  q=("q", "sum"), bruto=("qtd", "sum"), vol_fin=("vol_fin", "sum"), dn=("dn_s", "sum"),
                  premio=("premio_s", "sum"), hora_ms=("hora_ms", "min"))


def _resolver_classe(perfil: pd.DataFrame, corretoras: dict | None):
    """Classe da corretora NO ATIVO: perfil (já com cadastro + heurística) > cadastro > 'demais'."""
    cm = {}
    if perfil is not None and len(perfil):
        cm = {(s, c): k for s, c, k in zip(perfil["subjacente"], perfil["corretora"], perfil["classe"])}

    def f(subj, cod):
        k = cm.get((subj, cod))
        if k is not None:
            return k
        return corretoras[cod][1] if corretoras and cod in corretoras else "demais"
    return f


def _juntar_pernas(listas: list[list[dict]]) -> list[dict]:
    """Soma, por série, as pernas de várias execuções com a mesma assinatura."""
    acc: dict = {}
    for legs in listas:
        for l in legs:
            a = acc.get(l["ticker"])
            if a is None:
                acc[l["ticker"]] = dict(l, ids=set(l["ids"]), contra=set(l["contra"]))
                continue
            for k in ("q", "bruto", "vol_fin", "dn", "premio"):
                a[k] += l[k]
            a["hora_ms"] = min(a["hora_ms"], l["hora_ms"])
            a["ids"] |= l["ids"]
            a["contra"] |= l["contra"]
    out = []
    for a in acc.values():
        a["vwap"] = a["vol_fin"] / a["bruto"] if a["bruto"] else np.nan
        a["ids"], a["contra"] = frozenset(a["ids"]), frozenset(a["contra"])
        out.append(a)
    return out


def _eventos_por_corretora(base: pd.DataFrame, perfil: pd.DataFrame, params: Parametros,
                           adtv: pd.Series, corretoras: dict | None) -> list[dict]:
    nd = base[~base["direto"]]
    if nd.empty:
        return []
    pos = nd.index.to_numpy()
    bv = pd.DataFrame({
        "pos": np.r_[pos, pos],
        "subjacente": np.r_[nd["subjacente"].to_numpy(), nd["subjacente"].to_numpy()],
        "corretora": np.r_[nd["comprador"].to_numpy(), nd["vendedor"].to_numpy()],
        "contraparte": np.r_[nd["vendedor"].to_numpy(), nd["comprador"].to_numpy()],
        "sinal": np.r_[np.ones(len(nd)), -np.ones(len(nd))],
        "hora_ms": np.r_[nd["hora_ms"].to_numpy(), nd["hora_ms"].to_numpy()],
    })
    cls = _resolver_classe(perfil, corretoras)
    chaves = bv[["subjacente", "corretora"]].drop_duplicates()
    chaves["classe"] = [cls(s_, c_) for s_, c_ in zip(chaves["subjacente"], chaves["corretora"])]
    bv = bv.merge(chaves, on=["subjacente", "corretora"], how="left")
    bv = bv[~bv["classe"].isin(CLASSES_EXCLUIDAS)]
    if bv.empty:
        return []
    bv = bv.sort_values(["subjacente", "corretora", "hora_ms"], kind="mergesort").reset_index(drop=True)
    novo = ((bv["subjacente"] != bv["subjacente"].shift()) | (bv["corretora"] != bv["corretora"].shift())).to_numpy()
    bv["cluster"] = _clusterizar(novo, bv["hora_ms"].to_numpy(), params.janela_ms)
    bv = pd.concat([bv, base.loc[bv["pos"].to_numpy(), _ATTR].reset_index(drop=True)], axis=1)
    bv["q"] = bv["sinal"] * bv["qtd"]
    bv["dn_s"] = bv["sinal"] * bv["dn"]
    bv["premio_s"] = bv["sinal"] * bv["premio"]
    bv["vol_fin"] = bv["qtd"] * bv["preco"]

    pernas = bv.groupby(["cluster", "ticker"], sort=False).agg(**_AGG_PERNA).reset_index()
    pernas["vwap"] = pernas["vol_fin"] / pernas["bruto"]
    pernas = pernas[(pernas["q"] != 0) & (pernas["q"].abs() >= params.churn_min * pernas["bruto"])].copy()
    if pernas.empty:
        return []

    # 1) estruturas: agrupamentos com 2+ pernas proporcionais (sem filtro de tamanho ainda)
    n_legs = pernas.groupby("cluster")["ticker"].transform("size")
    multi = pernas[(n_legs >= 2) & (n_legs <= params.max_pernas)]
    clusters_est: set = set()
    grupos: dict = {}
    if len(multi):
        sel = bv[bv["cluster"].isin(multi["cluster"].unique())]
        ids = sel.groupby(["cluster", "ticker"])["pos"].agg(lambda x: frozenset(x.tolist()))
        contra = sel.groupby(["cluster", "ticker"])["contraparte"].agg(lambda x: frozenset(x.tolist()))
        for cl, sub in multi.groupby("cluster", sort=False):
            legs = sub.to_dict("records")
            if not _proporcional([l["q"] for l in legs], params):
                continue
            for l in legs:
                l["ids"], l["contra"] = ids[(cl, l["ticker"])], contra[(cl, l["ticker"])]
            clusters_est.add(cl)
            nome = classificar(legs, params.du_rolagem)[0]
            chave = (legs[0]["subjacente"], legs[0]["corretora"], nome,
                     tuple(sorted((l["ticker"], int(np.sign(l["q"]))) for l in legs)))
            if not params.agregar_execucoes:
                chave += (cl,)
            grupos.setdefault(chave, []).append(legs)
    eventos = []
    for lista in grupos.values():
        legs = _juntar_pernas(lista) if len(lista) > 1 else lista[0]
        e = _evento(legs, "estrutura", params, adtv, n_exec=len(lista))
        if passa_tamanho(e["dn_bruto"], e["premio_bruto"], e["adtv"], params):
            eventos.append(e)

    # 2) blocos: fluxo líquido em opções fora de estruturas, por corretora e série (somando execuções)
    resto = pernas[~pernas["cluster"].isin(clusters_est) & (pernas["tipo"] != "S")]
    if resto.empty:
        return eventos
    chave_b = ["subjacente", "corretora", "ticker"] + ([] if params.agregar_execucoes else ["cluster"])
    agg = resto.groupby(chave_b, sort=False).agg(
        classe=("classe", "first"), tipo=("tipo", "first"), strike=("strike", "first"),
        vencimento=("vencimento", "first"), du=("du", "first"), delta=("delta", "first"), S=("S", "first"),
        mult=("mult", "first"), q=("q", "sum"), bruto=("bruto", "sum"), vol_fin=("vol_fin", "sum"),
        dn=("dn", "sum"), premio=("premio", "sum"), hora_ms=("hora_ms", "min"),
        n_execucoes=("cluster", "nunique")).reset_index()
    agg = agg[(agg["q"] != 0) & (agg["q"].abs() >= params.churn_min * agg["bruto"])].copy()
    if agg.empty:
        return eventos
    agg["vwap"] = agg["vol_fin"] / agg["bruto"]
    # 2a) estrutura montada em pernas separadas ao longo do dia (mesma corretora e ativo, quantidades proporcionais)
    dia, usados = [], set()
    if params.agregar_execucoes:
        for (subj, cor), g in agg.groupby(["subjacente", "corretora"], sort=False):
            if len(g) < 2:
                continue
            g = g[g["dn"].abs() >= 0.2 * g["dn"].abs().max()]
            if not (2 <= len(g) <= params.max_pernas) or not _proporcional(g["q"], params, tol=params.tol_razao_dia):
                continue
            legs = g.to_dict("records")
            if classificar(legs, params.du_rolagem)[0].startswith("não classificada"):
                continue
            dia.append(legs)
            usados |= {(subj, cor, l["ticker"]) for l in legs}
    livres = [k not in usados for k in zip(agg["subjacente"], agg["corretora"], agg["ticker"])]
    blocos = agg[livres]
    blocos = blocos[passa_tamanho(blocos["dn"].abs(), blocos["premio"].abs(), blocos["subjacente"].map(adtv), params)]
    alvo = pd.concat([blocos[chave_b], pd.DataFrame([{c: l[c] for c in chave_b} for legs in dia for l in legs],
                                                    columns=chave_b)], ignore_index=True).drop_duplicates()
    if alvo.empty:
        return eventos
    sel_b = bv[~bv["cluster"].isin(clusters_est)].merge(alvo, on=chave_b, how="inner")
    ids_b = sel_b.groupby(chave_b)["pos"].agg(lambda x: frozenset(x.tolist()))
    contra_b = sel_b.groupby(chave_b)["contraparte"].agg(lambda x: frozenset(x.tolist()))
    for legs in dia:
        for l in legs:
            k = tuple(l[c] for c in chave_b)
            l["ids"], l["contra"] = ids_b[k], contra_b[k]
        e = _evento(legs, "estrutura", params, adtv, n_exec=int(sum(l["n_execucoes"] for l in legs)), escopo="dia")
        if passa_tamanho(e["dn_bruto"], e["premio_bruto"], e["adtv"], params):
            eventos.append(e)
    for rec in blocos.to_dict("records"):
        k = tuple(rec[c] for c in chave_b)
        rec["ids"], rec["contra"] = ids_b[k], contra_b[k]
        eventos.append(_evento([rec], "bloco", params, adtv, n_exec=rec["n_execucoes"]))
    return eventos


def _eventos_diretos(base: pd.DataFrame, perfil: pd.DataFrame, params: Parametros,
                     adtv: pd.Series, corretoras: dict | None) -> list[dict]:
    d = base[base["direto"]]
    if d.empty:
        return []
    cls = _resolver_classe(perfil, corretoras)
    d = d.sort_values(["subjacente", "comprador", "hora_ms"], kind="mergesort")
    novo = ((d["subjacente"] != d["subjacente"].shift()) | (d["comprador"] != d["comprador"].shift())).to_numpy()
    d = d.assign(cluster=_clusterizar(novo, d["hora_ms"].to_numpy(), params.janela_ms),
                 corretora=d["comprador"], q=d["qtd"], dn_s=d["dn"].abs(), premio_s=d["premio"].abs(),
                 vol_fin=d["qtd"] * d["preco"], pos=d.index)
    d["classe"] = [cls(s_, c_) for s_, c_ in zip(d["subjacente"], d["corretora"])]
    pernas = d.groupby(["cluster", "ticker"], sort=False).agg(**_AGG_PERNA).reset_index()
    ids = d.groupby(["cluster", "ticker"])["pos"].agg(lambda x: frozenset(x.tolist()))
    pernas["vwap"] = pernas["vol_fin"] / pernas["bruto"]
    pernas["premio"] = np.where(pernas["tipo"] != "S", pernas["premio"], 0.0)
    grupos: dict = {}
    for cl, sub in pernas.groupby("cluster", sort=False):
        legs = sub.to_dict("records")
        if all(l["tipo"] == "S" for l in legs):          # cruzamento só em ação não interessa aqui
            continue
        for l in legs:
            l["ids"], l["contra"] = ids[(cl, l["ticker"])], frozenset([l["corretora"]])
        chave = (legs[0]["subjacente"], legs[0]["corretora"], tuple(sorted(l["ticker"] for l in legs)))
        if not params.agregar_execucoes:
            chave += (cl,)
        grupos.setdefault(chave, []).append(legs)
    por_corretora: dict = {}
    for chave in grupos:
        por_corretora.setdefault(chave[:2], []).append(chave)
    finais = []          # (execuções, pernas somadas, padrão de financiamento)
    for chaves in por_corretora.values():
        if len(chaves) > 1:   # o conjunto do dia forma box/conversão? (pernas cruzadas em horários diferentes)
            todas = [legs for c in chaves for legs in grupos[c]]
            uniao = _juntar_pernas(todas)
            pad = _financiamento_direto(uniao)
            if pad is not None:
                finais.append((todas, uniao, pad))
                continue
        for c in chaves:
            lista = grupos[c]
            legs = _juntar_pernas(lista) if len(lista) > 1 else lista[0]
            finais.append((lista, legs, _financiamento_direto(legs)))
    eventos = []
    for lista, legs, pad in finais:
        dn_b, pr_b = float(sum(l["dn"] for l in legs)), float(sum(l["premio"] for l in legs))
        subj = legs[0]["subjacente"]
        if not passa_tamanho(dn_b, pr_b, adtv.get(subj, np.nan), params, min_pct=params.min_pct_adtv_direto):
            continue
        tipos = {l["tipo"] for l in legs}
        taxa = vfinal = du_f = np.nan
        if pad is not None:
            nome, taxa, vfinal, du_f = pad
            estrutura, fin = f"{nome} (direto)", True
            dica = (f"financiamento a {_br(100 * taxa, 1)}% a.a." if np.isfinite(taxa)
                    else "provável perna de conversão/reversão (financiamento)")
        elif len(legs) == 1:
            dica = "uma série, cliente x cliente: direção indeterminada"
        elif "S" in tipos:
            dica = "com ação: pode ser financiamento/conversão ou collar"
        elif tipos == {"C", "P"}:
            dica = "call + put: risk reversal, collar ou straddle/strangle — ver ΔOI das séries"
        else:
            dica = "mesmo tipo: trava ou rolagem — ver ΔOI das séries"
        if pad is None:
            estrutura, fin = "bloco direto (mesma corretora nos dois lados)", None
        ordem = sorted(legs, key=lambda l: (l["tipo"] == "S", l["tipo"], l["strike"] if l["tipo"] != "S" else 0))
        eventos.append(dict(
            tipo_evento="direto", subjacente=subj, hora_ms=int(min(l["hora_ms"] for l in legs)),
            corretora=int(legs[0]["corretora"]), classe=legs[0]["classe"], contrapartes=frozenset([legs[0]["corretora"]]),
            n_pernas=len(legs), n_execucoes=len(lista), estrutura=estrutura, escopo="dia" if len(lista) > 1 else "janela",
            leitura=dica, financiamento=fin, taxa_implicita=taxa, valor_final=vfinal, du_fin=du_f, dn_liquido=np.nan, dn_bruto=dn_b,
            premio_liquido=np.nan, premio_bruto=pr_b, adtv=float(adtv.get(subj, np.nan)),
            pernas_txt=" | ".join(_desc_perna(l, com_sinal=False) for l in ordem),
            pernas=ordem, ids=frozenset().union(*[l["ids"] for l in legs])))
    return eventos


def detectar_eventos(negx: pd.DataFrame, perfil: pd.DataFrame, adtv: pd.Series, params: Parametros,
                     corretoras: dict | None = None) -> pd.DataFrame:
    """Devolve um DataFrame de eventos: tipo_evento in {'estrutura', 'bloco', 'direto'}."""
    base = negx[(negx["tipo"] != "S") | (negx["qtd"] * negx["preco"] >= params.min_notional_acao)]
    eventos = _eventos_por_corretora(base, perfil, params, adtv, corretoras)
    eventos += _eventos_diretos(base, perfil, params, adtv, corretoras)
    if not eventos:
        return pd.DataFrame(columns=["tipo_evento", "subjacente", "hora_ms", "corretora", "classe", "estrutura",
                                     "leitura", "financiamento", "direcao", "dn_liquido", "dn_bruto", "pernas_txt",
                                     "ids", "pernas", "n_execucoes"])
    ev = pd.DataFrame(eventos)
    # espelhos: o mesmo conjunto de negócios visto pelas duas pontas -> fica uma leitura.
    # Preferência: classe (institucional/estrangeira/tesouraria antes), depois a corretora de MENOR
    # participação no ativo (mais provável ser quem toma liquidez), depois o código.
    part = {}
    if len(perfil):
        part = {(s_, c_): p_ for s_, c_, p_ in zip(perfil["subjacente"], perfil["corretora"], perfil["participacao"])}
    ev["rank"] = ev["classe"].map(RANK_CLASSE).fillna(1)
    ev["participacao"] = [part.get((s_, c_), 0.0) for s_, c_ in zip(ev["subjacente"], ev["corretora"])]
    ev = ev.sort_values(["rank", "participacao", "corretora", "hora_ms"]).reset_index(drop=True)
    vistos, espelho, manter = {}, {}, []
    for i, chave in enumerate(ev["ids"]):
        if chave in vistos:
            espelho.setdefault(vistos[chave], []).append(int(ev.at[i, "corretora"]))
        else:
            vistos[chave] = i
            manter.append(i)
    ev["espelho"] = [",".join(map(str, espelho.get(i, []))) for i in range(len(ev))]
    ev = ev.loc[manter]
    # bloco contido numa estrutura já reportada (a contraparte viu só uma perna) sai
    est = ev["tipo_evento"] == "estrutura"
    ids_est = frozenset().union(*ev.loc[est, "ids"]) if est.any() else frozenset()
    contido = (ev["tipo_evento"] == "bloco") & ev["ids"].map(lambda x: x <= ids_est)
    ev = ev[~contido].copy()

    cls = _resolver_classe(perfil, corretoras)
    ev["contrapartes_txt"] = [", ".join(f"{c} ({cls(s_, c)})" if cls(s_, c) != "demais" else str(c) for c in sorted(cs))
                              for s_, cs in zip(ev["subjacente"], ev["contrapartes"])]
    ev["contraparte_alto_giro"] = [any(cls(s_, c) in CLASSES_EXCLUIDAS for c in cs)
                                   for s_, cs in zip(ev["subjacente"], ev["contrapartes"])]
    ev["nome_corretora"] = [corretoras[c][0] if corretoras and c in corretoras else "" for c in ev["corretora"]]
    ev["hora"] = ev["hora_ms"].map(hora_txt)
    ev["pct_adtv"] = ev["dn_bruto"] / ev["adtv"]
    ev["direcao"] = ev["leitura"].map(direcao)
    ev.loc[ev["tipo_evento"] == "direto", "direcao"] = 0
    return ev.sort_values(["tipo_evento", "dn_bruto"], ascending=[True, False]).reset_index(drop=True)


def anotar_com_oi(ev: pd.DataFrame, pos_series: pd.DataFrame, params: Parametros) -> pd.DataFrame:
    """Cruza cada perna com a variação das posições em aberto da série.

    - leitura: se a perna vendida é explicada por aumento de posição coberta, 'risk reversal de baixa'
      vira collar e 'venda de call' vira lançamento coberto;
    - confirmacao_oi: menor fração, entre as pernas, da quantidade que aparece como AUMENTO de OI na série;
      abriu_posicao = confirmacao_oi >= min_confirmacao_oi.
    """
    if ev.empty or pos_series is None or pos_series.empty or "d_oi" not in pos_series:
        return ev
    ps = pos_series.set_index("ticker")
    abrev = {"coberta": "cob", "travada": "trav", "descoberta": "desc"}
    obs, leituras, confs, estruturas = [], [], [], []
    doi_serie = ps["d_oi"]
    for _, e in ev.iterrows():
        partes, venda, conf = [], {}, []
        for l in e["pernas"]:
            if l["tipo"] == "S" or l["ticker"] not in ps.index:
                continue
            r = ps.loc[l["ticker"]]
            comps = [(n, r.get(f"d_{n}")) for n in ("coberta", "travada", "descoberta")]
            det = ", ".join(f"{abrev[n]} {fmt_sinal(v)}" for n, v in comps if v is not None and np.isfinite(v) and v != 0)
            partes.append(f"{l['ticker']}: ΔOI {fmt_sinal(r.get('d_oi'))}" + (f" ({det})" if det else ""))
            doi_perna, qtd = r.get("d_oi"), abs(l["q"])
            conf.append(min(max(doi_perna, 0.0) / qtd, 1.0)
                        if doi_perna is not None and np.isfinite(doi_perna) and qtd > 0 else np.nan)
            if e["tipo_evento"] != "direto" and l["q"] < 0:
                for n, v in comps:
                    if v is not None and np.isfinite(v) and v >= params.limiar_ref_oi * abs(l["q"]):
                        venda[l["tipo"]] = n
                        break
        leitura = e["leitura"]
        est = str(e["estrutura"])
        ops = [l for l in e["pernas"] if l["tipo"] in ("C", "P")]
        if e["tipo_evento"] == "estrutura" and est.startswith(("calendário", "diagonal")) and len(ops) == 2:
            perto, longe = sorted(ops, key=lambda l: pd.Timestamp(l["vencimento"]))
            dp, dl = doi_serie.get(perto["ticker"]), doi_serie.get(longe["ticker"])
            lim = params.min_confirmacao_oi
            if (dp is not None and dl is not None and np.isfinite(dp) and np.isfinite(dl)
                    and dp <= -lim * abs(perto["q"]) and dl >= lim * abs(longe["q"])):
                comprado = longe["q"] > 0
                mk = abs(perto["strike"] - longe["strike"]) < 1e-6
                est = ("rolagem" + ("" if mk else " com troca de strike")
                       + (" de posição comprada" if comprado else " de posição vendida") + " (confirmada pelo ΔOI)")
                if perto["tipo"] == "C":
                    leitura = "alta (mantida)" if comprado else "baixa/vol vendida (mantida)"
                else:
                    leitura = "baixa (mantida)" if comprado else "alta/vol vendida (mantida)"
        if est.startswith("risk reversal de baixa") and venda.get("C") == "coberta":
            leitura = "proteção (collar de quem tem a ação)"
        elif est == "venda de call" and venda.get("C") == "coberta":
            leitura = "lançamento coberto (renda/teto)"
        elif est.startswith(("straddle vendido", "strangle vendido", "venda de put")) and "descoberta" in venda.values():
            leitura = f"{leitura} — a seco"
        obs.append(" | ".join(partes))
        leituras.append(leitura)
        estruturas.append(est)
        confs.append(min(conf) if conf and all(np.isfinite(conf)) else np.nan)
    ev = ev.copy()
    ev["obs_oi"], ev["leitura"], ev["confirmacao_oi"], ev["estrutura"] = obs, leituras, confs, estruturas
    ev["abriu_posicao"] = ev["confirmacao_oi"] >= params.min_confirmacao_oi
    ev["direcao"] = np.where(ev["tipo_evento"] == "direto", 0, ev["leitura"].map(direcao))
    return ev


def quantidade_financiamento(ev: pd.DataFrame) -> pd.Series:
    """Quantidade negociada no dia, por série, em eventos de financiamento (box, conversão, sintético)."""
    if ev is None or ev.empty or "financiamento" not in ev or "pernas" not in ev:
        return pd.Series(dtype=float)
    q: dict = {}
    for pernas in ev.loc[ev["financiamento"].eq(True), "pernas"]:
        for l in pernas:
            if l["tipo"] in ("C", "P"):
                q[l["ticker"]] = q.get(l["ticker"], 0.0) + abs(float(l["q"]))
    return pd.Series(q, dtype=float)


def spread_di(ev: pd.DataFrame, params: Parametros, curva_di=None) -> pd.DataFrame:
    """Spread da taxa implícita de financiamento contra o DI do mesmo prazo.

    curva_di: {DU: taxa a.a.} (ex.: vértices do DI1 do dia, curva_di_pyield), interpolada por flat-forward na base 252.
    Sem curva, a referência é params.taxa_pre_aa para todos os prazos.
    """
    if ev is None or ev.empty or "taxa_implicita" not in ev:
        return ev
    ev = ev.copy()
    du = pd.to_numeric(ev["du_fin"], errors="coerce") if "du_fin" in ev else pd.Series(np.nan, index=ev.index)
    from .taxas import interpolar_flat_forward, pct_do_di
    if curva_di is not None and len(curva_di):
        ref = interpolar_flat_forward(curva_di, du.to_numpy(dtype=float))      # flat-forward, base 252
    else:
        ref = np.where(du.notna(), params.taxa_pre_aa, np.nan)
    taxa = pd.to_numeric(ev["taxa_implicita"], errors="coerce")
    ev["taxa_di_ref"] = ref
    ev["spread_di"] = taxa - ev["taxa_di_ref"]
    ev["pct_di"] = pct_do_di(taxa, ev["taxa_di_ref"])
    return ev
