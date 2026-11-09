"""Leitura das posições em aberto (coberta / travada / descoberta).

Convenções:
- dn_d_oi: delta-notional adicionado aos TITULARES pela variação de OI (lançadores têm o oposto).
- dn_d_oi_ex_trava: o mesmo, sem a parte do ΔOI explicada por posição travada (spreads/box).
- lanc_desc_*: delta-notional dos LANÇADORES a seco pela variação da posição descoberta.
- 'concentrada': ΔOI grande com variação pequena do número de clientes em algum dos lados.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Parametros
from .enriquecimento import Universo
from .estruturas import fmt_rs

_COMPS = ["oi", "coberta", "travada", "descoberta", "n_clientes_1", "n_clientes_2"]


def analisar_posicoes(pos_d: pd.DataFrame, univ: Universo, params: Parametros,
                      pos_d1: pd.DataFrame | None = None, q_financiamento: pd.Series | None = None) -> pd.DataFrame:
    """q_financiamento: quantidade negociada no dia em box/conversão/sintético, por série. Série cujo ΔOI é
    explicado (>= 50%) por financiamento fica fora da concentração e das leituras por ativo."""
    s = univ.series
    p = pos_d[pos_d["ticker"].isin(s.index)].copy()
    for c in _COMPS:
        if c not in p:
            p[c] = np.nan
    if pos_d1 is not None and len(pos_d1):
        a = pos_d1.drop_duplicates("ticker").set_index("ticker")
        nova = ~p["ticker"].isin(a.index)          # série nova: anterior = 0
        for c in _COMPS:
            ant = p["ticker"].map(a[c]) if c in a else pd.Series(np.nan, index=p.index)
            p[f"d_{c}"] = p[c] - ant.where(~nova, 0.0)
    else:
        p["d_oi"] = p["var_oi"] if "var_oi" in p else np.nan
        for c in _COMPS[1:]:
            p[f"d_{c}"] = np.nan
    p = p.join(s[["subjacente", "tipo", "strike", "vencimento", "du", "S", "delta", "gama", "mult", "iv"]], on="ticker")
    p["adtv"] = p["subjacente"].map(univ.adtv)
    fator = p["delta"] * p["S"] * p["mult"]
    p["dn_oi"] = p["oi"] * fator
    p["dn_d_oi"] = p["d_oi"] * fator
    # sem a parte do ΔOI explicada por posição travada (box/travas: o titular é a outra ponta de um spread)
    p["dn_d_oi_ex_trava"] = (p["d_oi"] - p["d_travada"].fillna(0)) * fator
    p["d_oi_ef"] = p["d_oi"] - p["d_travada"].fillna(0)
    p["pct_adtv"] = p["dn_d_oi_ex_trava"].abs() / p["adtv"]          # sobre o ΔOI sem travas
    p["gama_1pct"] = p["oi"] * p["gama"] * p["S"] ** 2 * 0.01 * p["mult"]
    p["hedge_1pct_desc"] = p["descoberta"] * p["gama"] * p["S"] ** 2 * 0.01 * p["mult"]

    m = np.abs(np.nan_to_num(p[["d_coberta", "d_travada", "d_descoberta"]].to_numpy(dtype=float), nan=0.0))
    nomes = np.array(["coberta", "travada", "descoberta"])
    p["motor"] = np.where(m.max(axis=1) > 0, nomes[m.argmax(axis=1)], None)

    # concentração medida SEM a parcela travada: box e travas (financiamento) dominariam a lista
    oi_ant = (p["oi"] - p["d_oi"]).clip(lower=1)
    tamanho = ((p["d_oi_ef"].abs() >= params.min_var_oi_pct * oi_ant)
               & (p["dn_d_oi_ex_trava"].abs() >= params.min_dn_oi) & (p["pct_adtv"] >= params.min_pct_adtv_oi))
    nocional_trava = (p["d_travada"] * p["S"] * p["mult"]).abs()
    p["trava_relevante"] = ((nocional_trava >= params.min_dn_oi)
                            & (nocional_trava >= params.min_pct_adtv_oi * p["adtv"])).eq(True)
    poucos = (p["d_n_clientes_1"].abs() <= params.max_novos_clientes) | \
             (p["d_n_clientes_2"].abs() <= params.max_novos_clientes)
    qf = p["ticker"].map(q_financiamento) if q_financiamento is not None and len(q_financiamento) else None
    p["q_financiamento"] = qf.fillna(0.0).to_numpy() if qf is not None else 0.0
    p["explicada_fin"] = ((p["q_financiamento"] > 0) & (p["q_financiamento"] >= 0.5 * p["d_oi"].abs())).eq(True)
    p["concentrada"] = (tamanho & poucos).eq(True) & ~p["explicada_fin"]
    p["sentido"] = np.where(p["d_oi"] > 0, "abertura", np.where(p["d_oi"] < 0, "encerramento", ""))
    return p.sort_values(["subjacente", "vencimento", "tipo", "strike"]).reset_index(drop=True)


def resumir_posicoes(p: pd.DataFrame, params: Parametros) -> pd.DataFrame:
    if p.empty:
        return pd.DataFrame()
    ok = ~p["explicada_fin"].to_numpy(dtype=bool) if "explicada_fin" in p else np.ones(len(p), dtype=bool)
    c, pt = (p["tipo"] == "C").to_numpy() & ok, (p["tipo"] == "P").to_numpy() & ok
    fator = (p["delta"] * p["S"] * p["mult"]).to_numpy()
    q = p.assign(
        dn_ex=np.where(ok, p["dn_d_oi_ex_trava"], 0.0), n_fin=(~ok).astype(int),
        dn_calls=np.where(c, p["dn_d_oi_ex_trava"], 0.0),
        dn_puts=np.where(pt, p["dn_d_oi_ex_trava"], 0.0),
        lanc_desc_calls=np.where(c, -p["d_descoberta"].to_numpy() * fator, 0.0),
        lanc_desc_puts=np.where(pt, -p["d_descoberta"].to_numpy() * fator, 0.0),
        lanc_cob_calls=np.where(c, -p["d_coberta"].to_numpy() * fator, 0.0),
        nocional_trava=p["d_travada"] * p["S"] * p["mult"],
        conc=p["concentrada"].astype(int),
    )
    g = q.groupby("subjacente").agg(
        spot=("S", "first"), adtv=("adtv", "first"), dn_titulares=("dn_d_oi", "sum"),
        dn_titulares_ex_trava=("dn_ex", "sum"), n_explicadas_fin=("n_fin", "sum"),
        dn_calls=("dn_calls", "sum"), dn_puts=("dn_puts", "sum"),
        lanc_desc_calls=("lanc_desc_calls", "sum"), lanc_desc_puts=("lanc_desc_puts", "sum"),
        lanc_cob_calls=("lanc_cob_calls", "sum"), d_travada=("d_travada", "sum"),
        nocional_trava=("nocional_trava", "sum"),
        n_concentradas=("conc", "sum"))
    limiar = np.maximum(params.min_dn_oi, params.min_pct_adtv_leitura * g["adtv"].fillna(0))
    leituras = []
    for (_, r), thr in zip(g.iterrows(), limiar):
        f = []
        if r["lanc_desc_puts"] >= thr:
            f.append(f"venda de put a seco ↑ ({fmt_rs(r['lanc_desc_puts'])} Δ dos lançadores; alta/vol vendida)")
        if r["lanc_desc_calls"] <= -thr:
            f.append(f"venda de call a seco ↑ ({fmt_rs(r['lanc_desc_calls'])}; baixa/vol vendida)")
        if r["lanc_cob_calls"] <= -thr:
            f.append("lançamento coberto ↑ (renda/teto)")
        if r["dn_calls"] >= thr:
            f.append(f"titulares de calls ↑ ({fmt_rs(r['dn_calls'])}; alta)")
        if r["dn_calls"] <= -thr:
            f.append("calls encerradas ↓")
        if r["dn_puts"] <= -thr:
            f.append(f"titulares de puts ↑ ({fmt_rs(r['dn_puts'])}; baixa/proteção)")
        if r["dn_puts"] >= thr:
            f.append("puts encerradas ↓")
        if abs(r["nocional_trava"]) >= thr:
            f.append(f"travas/box {'↑' if r['nocional_trava'] > 0 else '↓'} ({fmt_rs(r['nocional_trava'])} de nocional; "
                     "provável financiamento)")
        if r["n_concentradas"] > 0:
            f.append(f"{int(r['n_concentradas'])} série(s) com entrada concentrada")
        if r["n_explicadas_fin"] > 0:
            f.append(f"{int(r['n_explicadas_fin'])} série(s) explicada(s) por financiamento, fora da leitura")
        leituras.append("; ".join(f) if f else "sem variação relevante")
    g["leitura_oi"] = leituras
    g["pct_adtv"] = g["dn_titulares_ex_trava"].abs() / g["adtv"]
    return g.reset_index()


def mapa_vencimento(p: pd.DataFrame, params: Parametros, banda_mapa: float = 0.10) -> pd.DataFrame:
    """Vencimento mais próximo (até N DU): OI e posição descoberta por strike, gama e candidato a pin."""
    m = p[p["du"] <= params.du_mapa_vencimento]
    if m.empty:
        return pd.DataFrame()
    m = m[m["vencimento"] == m.groupby("subjacente")["vencimento"].transform("min")]
    c, pt = (m["tipo"] == "C").to_numpy(), (m["tipo"] == "P").to_numpy()
    t = m.assign(oi_calls=np.where(c, m["oi"], 0.0), oi_puts=np.where(pt, m["oi"], 0.0),
                 desc_calls=np.where(c, m["descoberta"], 0.0), desc_puts=np.where(pt, m["descoberta"], 0.0))
    g = t.groupby(["subjacente", "vencimento", "strike"], as_index=False).agg(
        S=("S", "first"), du=("du", "first"), oi_calls=("oi_calls", "sum"), oi_puts=("oi_puts", "sum"),
        desc_calls=("desc_calls", "sum"), desc_puts=("desc_puts", "sum"),
        gama_1pct=("gama_1pct", "sum"), hedge_1pct_desc=("hedge_1pct_desc", "sum"))
    g["dist"] = g["strike"] / g["S"] - 1
    g = g[g["dist"].abs() <= banda_mapa].copy()
    g["oi_total"] = g["oi_calls"] + g["oi_puts"]
    g["pin_candidato"] = False
    perto = g[g["dist"].abs() <= params.banda_atm]
    if len(perto):
        g.loc[perto.groupby("subjacente")["oi_total"].idxmax().to_numpy(), "pin_candidato"] = True
    return g.sort_values(["subjacente", "strike"]).reset_index(drop=True)
