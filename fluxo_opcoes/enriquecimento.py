"""Monta o universo de séries (com gregas) e enriquece o negócio a negócio."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import CLASSES_EXCLUIDAS, Parametros
from .precificacao import bs, dias_uteis, taxa_continua, vol_implicita
from .taxas import interpolar_flat_forward

PADRAO_ACAO = r"^[A-Z]{4}(?:3|4|5|6|11)$"


@dataclass
class Universo:
    series: pd.DataFrame      # índice = ticker da opção
    spot: pd.Series           # ticker -> preço (todos os instrumentos do consolidado)
    volume: pd.Series         # ticker -> volume financeiro do dia
    adtv: pd.Series           # ação-objeto -> ADTV usado na normalização
    data: pd.Timestamp
    r: float


def precos_e_volumes(consolidado: pd.DataFrame, negocios: pd.DataFrame | None = None):
    c = consolidado.drop_duplicates("ticker").set_index("ticker")
    preco = c["preco_ult"] if "preco_ult" in c else pd.Series(np.nan, index=c.index)
    if "preco_medio" in c:
        preco = preco.fillna(c["preco_medio"])
    volume = c["volume"] if "volume" in c else pd.Series(np.nan, index=c.index)
    if negocios is not None and len(negocios):
        ult = negocios.sort_values("hora_ms").groupby("ticker")["preco"].last()
        vol_n = (negocios["preco"] * negocios["qtd"]).groupby(negocios["ticker"]).sum()
        preco, volume = preco.combine_first(ult), volume.combine_first(vol_n)
    return preco.astype(float), volume.astype(float)


def mapear_subjacentes(raizes: pd.Series, volume: pd.Series, mapa_manual: dict | None = None) -> pd.Series:
    """Raiz (ex. 'PETR') -> ação-objeto. Padrão: a ação mais negociada com a mesma raiz."""
    manual = {k.upper(): v.upper() for k, v in (mapa_manual or {}).items()}
    idx = volume.index.to_series()
    acoes = volume[idx.str.match(PADRAO_ACAO, na=False).to_numpy()].dropna()
    cache: dict = {}

    def resolver(r):
        if r is None or (isinstance(r, float) and np.isnan(r)):
            return None
        r = str(r).upper().strip()
        if r not in cache:
            if r in manual or r[:4] in manual:
                cache[r] = manual.get(r, manual.get(r[:4]))
            elif re.match(PADRAO_ACAO, r) and r in volume.index:
                cache[r] = r
            elif len(r) >= 4:
                cand = acoes[acoes.index.str[:4] == r[:4]]
                cache[r] = cand.idxmax() if len(cand) else None
            else:   # raízes de 3 letras (DOL, IDI, BGI, ETH...) não são opções de ações
                cache[r] = None
        return cache[r]

    return raizes.map(resolver)


def montar_universo(cadastro: pd.DataFrame, consolidado: pd.DataFrame, data, params: Parametros,
                    negocios: pd.DataFrame | None = None, mapa_subjacente: dict | None = None,
                    adtv_externo: dict | pd.Series | None = None, ativos=None, curva_di=None) -> Universo:
    """curva_di: {DU: taxa} do DI1; com ela, cada série usa a taxa pré do seu prazo no Black-Scholes."""
    data = pd.Timestamp(data).normalize()
    preco, volume = precos_e_volumes(consolidado, negocios)
    cad = cadastro[cadastro["tipo"].isin(["C", "P"])].copy()
    cad = cad.dropna(subset=["strike", "vencimento"])
    raiz = cad["subjacente"] if "subjacente" in cad else pd.Series(None, index=cad.index, dtype=object)
    if "ativo" in cad:
        raiz = raiz.where(raiz.notna(), cad["ativo"])
    raiz = raiz.where(raiz.notna(), cad["ticker"].str[:4])
    excluir = {r.upper() for r in (params.raizes_excluidas or ())}
    manter = ~raiz.astype(str).str[:4].isin(excluir)
    cad, raiz = cad[manter], raiz[manter]
    cad["subjacente"] = mapear_subjacentes(raiz, volume, mapa_subjacente)
    cad = cad.dropna(subset=["subjacente"])
    if ativos:
        cad = cad[cad["subjacente"].isin([a.upper() for a in ativos])]
    cad["S"] = cad["subjacente"].map(preco)
    cad = cad[(cad["S"] > 0) & (cad["vencimento"] >= data)].copy()

    cad["du"] = dias_uteis(data, cad["vencimento"])
    cad["T"] = np.maximum(cad["du"], 0.5) / params.dias_ano
    mult = cad["multiplicador"] if "multiplicador" in cad else pd.Series(1.0, index=cad.index)
    cad["mult"] = mult.where(mult > 0, 1.0).fillna(1.0)
    if curva_di is not None and len(curva_di):
        cad["taxa_pre"] = interpolar_flat_forward(curva_di, np.maximum(cad["du"].to_numpy(dtype=float), 1.0))
    else:
        cad["taxa_pre"] = params.taxa_pre_aa
    r = np.log1p(cad["taxa_pre"].to_numpy(dtype=float))
    call = (cad["tipo"] == "C").to_numpy()

    cad["preco_opcao"] = cad["ticker"].map(preco)
    cad["iv"] = vol_implicita(cad["preco_opcao"], cad["S"], cad["strike"], cad["T"], r, call)
    cad["iv_fonte"] = np.where(cad["iv"].notna(), "mercado", None)
    for chave, rotulo in ((["subjacente", "vencimento"], "mediana do vencimento"), (["subjacente"], "mediana do ativo")):
        med = cad.groupby(chave)["iv"].transform("median")
        falta = cad["iv"].isna() & med.notna()
        cad.loc[falta, "iv_fonte"] = rotulo
        cad["iv"] = cad["iv"].fillna(med)
    cad.loc[cad["iv"].isna(), "iv_fonte"] = "parâmetro"
    cad["iv"] = cad["iv"].fillna(params.vol_fallback)

    _, delta, gama = bs(cad["S"], cad["strike"], cad["T"], r, cad["iv"], call)
    cad["delta"], cad["gama"] = delta, gama

    subj = pd.Index(cad["subjacente"].unique())
    if adtv_externo is not None:
        adtv = pd.Series(adtv_externo, dtype=float).reindex(subj).fillna(volume.reindex(subj))
    else:
        adtv = volume.reindex(subj)
    cols = ["ticker", "subjacente", "tipo", "strike", "vencimento", "estilo", "du", "T", "mult", "S", "taxa_pre",
            "preco_opcao", "iv", "iv_fonte", "delta", "gama"]
    cad = cad[[c for c in cols if c in cad]].set_index("ticker")
    r_ref = float(np.nanmedian(r)) if np.size(r) else taxa_continua(params.taxa_pre_aa)
    return Universo(series=cad, spot=preco, volume=volume, adtv=adtv.astype(float), data=data, r=r_ref)


def enriquecer_negocios(neg: pd.DataFrame, univ: Universo) -> pd.DataFrame:
    """Anexa atributos da série a cada negócio. Ações-objeto entram com tipo 'S' e delta 1."""
    s = univ.series
    cols = ["subjacente", "tipo", "strike", "vencimento", "du", "delta", "S", "mult"]
    op = neg[neg["ticker"].isin(s.index)].join(s[cols], on="ticker")
    ac = neg[neg["ticker"].isin(set(s["subjacente"]))].copy()
    ac["subjacente"], ac["tipo"], ac["strike"], ac["du"] = ac["ticker"], "S", np.nan, np.nan
    ac["vencimento"] = pd.NaT
    ac["delta"], ac["S"], ac["mult"] = 1.0, ac["ticker"].map(univ.spot), 1.0
    partes = [p for p in (op, ac) if len(p)]
    out = pd.concat(partes, ignore_index=True) if partes else op.iloc[:0]
    out["dn"] = out["qtd"] * out["delta"] * out["S"] * out["mult"]   # ponto de vista do comprador
    out["premio"] = out["qtd"] * out["preco"] * out["mult"]
    out["direto"] = out["comprador"] == out["vendedor"]
    return out


def classe_corretora(cod: int, alto_giro: bool, corretoras: dict | None) -> str:
    if corretoras and cod in corretoras:
        return corretoras[cod][1]
    return "alto giro (heurística)" if alto_giro else "demais"


def nome_corretora(cod: int, corretoras: dict | None) -> str:
    if corretoras and cod in corretoras:
        return corretoras[cod][0]
    return "" if cod >= 0 else "anônimo"


def _lados(o: pd.DataFrame) -> pd.DataFrame:
    """Cada negócio vira duas linhas: comprador (+) e vendedor (−)."""
    z, u = np.zeros(len(o)), np.ones(len(o))
    return pd.DataFrame({
        "subjacente": np.r_[o["subjacente"].to_numpy(), o["subjacente"].to_numpy()],
        "corretora": np.r_[o["comprador"].to_numpy(), o["vendedor"].to_numpy()],
        "ticker": np.r_[o["ticker"].to_numpy(), o["ticker"].to_numpy()],
        "compra": np.r_[u, z],
        "q_compra": np.r_[o["qtd"].to_numpy(), z],
        "q_venda": np.r_[z, o["qtd"].to_numpy()],
        "dn": np.r_[o["dn"].to_numpy(), -o["dn"].to_numpy()],
        "premio": np.r_[o["premio"].to_numpy(), -o["premio"].to_numpy()],
    })


def fluxo_corretoras(negx: pd.DataFrame, perfil: pd.DataFrame, excluir=frozenset()) -> pd.DataFrame:
    """Fluxo líquido em opções por corretora e ativo, sem negócios diretos e sem os negócios em `excluir`
    (ex.: pernas de box/conversão, que são financiamento e não posição)."""
    o = negx[(negx["tipo"] != "S") & ~negx["direto"]]
    if excluir:
        o = o[~o.index.isin(list(excluir))]
    if o.empty or perfil is None or perfil.empty:
        return pd.DataFrame(columns=["subjacente", "corretora", "dn_liquido", "classe", "excluida_leitura"])
    f = _lados(o).groupby(["subjacente", "corretora"]).agg(
        n_negocios=("ticker", "size"), n_series=("ticker", "nunique"),
        dn_liquido=("dn", "sum"), premio_liquido=("premio", "sum")).reset_index()
    info = perfil[["subjacente", "corretora", "nome", "classe", "participacao", "desequilibrio", "excluida_leitura"]]
    return f.merge(info, on=["subjacente", "corretora"], how="left")


def perfil_corretoras(negx: pd.DataFrame, params: Parametros, corretoras: dict | None = None) -> pd.DataFrame:
    """Participação, equilíbrio compra/venda e fluxo líquido por corretora e ativo (só opções, sem diretos).

    'alto giro' = participação alta, compra e venda equilibradas e muitas séries:
    típico de formador de mercado, mas também de corretora de varejo de massa.
    """
    o = negx[(negx["tipo"] != "S") & ~negx["direto"]]
    if o.empty:
        return pd.DataFrame(columns=["subjacente", "corretora", "classe", "participacao", "excluida_leitura"])
    n_total = o.groupby("subjacente").size()
    p = _lados(o).groupby(["subjacente", "corretora"]).agg(
        n_negocios=("ticker", "size"), n_series=("ticker", "nunique"),
        n_compras=("compra", "sum"), q_compra=("q_compra", "sum"), q_venda=("q_venda", "sum"),
        dn_liquido=("dn", "sum"), premio_liquido=("premio", "sum")).reset_index()
    p["participacao"] = p["n_negocios"] / p["subjacente"].map(n_total)
    # equilíbrio por CONTAGEM de negócios: um bloco grande não descaracteriza quem provê liquidez o dia todo
    p["desequilibrio"] = (2 * p["n_compras"] - p["n_negocios"]).abs() / p["n_negocios"]
    p["alto_giro"] = ((p["participacao"] >= params.mm_min_part)
                      & (p["desequilibrio"] <= params.mm_max_desequilibrio)
                      & (p["n_series"] >= params.mm_min_series))
    # o comportamento no ativo vence o rótulo: corretora cadastrada (exceto formador) que age como
    # provedora de liquidez naquele ativo é tratada como alto giro ali
    p["classe_cadastro"] = [corretoras[c][1] if corretoras and c in corretoras else "" for c in p["corretora"]]
    p["classe"] = [("alto giro (heurística)" if g and k != "formador" else (k or "demais"))
                   for k, g in zip(p["classe_cadastro"], p["alto_giro"])]
    p["nome"] = [nome_corretora(c, corretoras) for c in p["corretora"]]
    p["excluida_leitura"] = p["classe"].isin(CLASSES_EXCLUIDAS)
    return p.sort_values(["subjacente", "n_negocios"], ascending=[True, False]).reset_index(drop=True)
