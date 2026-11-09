"""Curva pré (DI1) para o Black-Scholes e para comparar as taxas implícitas de financiamento.

A referência certa para um box ou uma conversão de N dias úteis é a taxa pré do mesmo prazo
(curva do DI1, que embute o CDI esperado até o vencimento), não o CDI de um dia.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def interpolar_flat_forward(curva, du):
    """Taxa(s) a.a. (base 252) para `du` dias úteis, por flat-forward entre os vértices de `curva` ({DU: taxa}).

    Abaixo do primeiro vértice repete a primeira taxa; acima do último, a última (mesma convenção do
    Interpolador do pyield com extrapolar=True).
    """
    c = pd.Series(curva, dtype=float).dropna().sort_index()
    c = c[c.index.to_numpy(dtype=float) > 0]
    escalar = np.ndim(du) == 0
    d_all = np.atleast_1d(np.asarray(du, dtype=float))
    out = np.full(d_all.shape, np.nan)
    if c.empty:
        return float(out[0]) if escalar else out
    x, r = c.index.to_numpy(dtype=float), c.to_numpy(dtype=float)
    fat = (1.0 + r) ** (x / 252.0)
    ok = np.isfinite(d_all) & (d_all > 0)
    d = d_all[ok]
    i = np.searchsorted(x, d, side="left")
    res = np.empty(d.shape)
    abaixo, acima = i == 0, i >= len(x)
    res[abaixo], res[acima] = r[0], r[-1]
    meio = ~(abaixo | acima)
    if meio.any():
        j = i[meio]
        x0, x1, f0, f1 = x[j - 1], x[j], fat[j - 1], fat[j]
        f = f0 * (f1 / f0) ** ((d[meio] - x0) / (x1 - x0))
        res[meio] = f ** (252.0 / d[meio]) - 1.0
    out[ok] = res
    return float(out[0]) if escalar else out


def pct_do_di(taxa, taxa_di):
    """Taxa pré expressa em % do DI do mesmo prazo, pela razão das taxas diárias (base 252)."""
    t, d = np.asarray(taxa, dtype=float), np.asarray(taxa_di, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return ((1 + t) ** (1 / 252) - 1) / ((1 + d) ** (1 / 252) - 1)


def curva_di_pyield(data, tipo_taxa: str = "ajuste") -> pd.Series:
    """Vértices do DI1 no pregão `data` via pyield: Series {DU: taxa decimal}.

    tipo_taxa: "ajuste" (padrão, taxa de ajuste da B3) ou "fechamento". Aceita a API em português
    (pyield >= 0.6x: di1.dados, dias_uteis, taxa_ajuste) e a antiga em inglês (di1.data, BDaysToExp, SettlementRate).
    """
    try:
        import pyield as yd
    except ImportError as e:
        raise ImportError("pyield não está instalado: pip install pyield") from e
    dia = pd.Timestamp(data).date()
    buscar = getattr(yd.di1, "dados", None) or getattr(yd.di1, "data")
    df = buscar(dia)                       # polars (versões novas) ou pandas (antigas); sem exigir pyarrow
    if df is None or len(df) == 0:
        raise ValueError(f"O pyield não trouxe dados de DI1 para {dia:%d/%m/%Y} (feriado, data futura ou sem rede).")
    cols = list(df.columns)
    col_du = next((c for c in ("dias_uteis", "BDaysToExp") if c in cols), None)
    alvo = ("taxa_ajuste", "SettlementRate") if tipo_taxa == "ajuste" else ("taxa_fechamento", "LastRate")
    col_tx = next((c for c in alvo if c in cols), None)
    if col_du is None or col_tx is None:
        raise ValueError(f"Colunas de DU/taxa não encontradas no retorno do pyield: {cols}")
    coluna = (lambda c: df.get_column(c).to_list()) if hasattr(df, "get_column") else (lambda c: df[c].tolist())
    s = (pd.Series(pd.to_numeric(coluna(col_tx), errors="coerce"), index=pd.to_numeric(coluna(col_du), errors="coerce"))
         .dropna())
    s = s[~s.index.duplicated()].astype(float).sort_index()
    if len(s) and s.median() > 1:          # versões que devolvem em %
        s = s / 100.0
    s = s[s.index > 0]
    s.index = s.index.astype(int)
    s.name = f"DI1 {tipo_taxa} {dia:%d/%m/%Y}"
    return s


def cdi_pyield(data) -> float:
    """CDI (DI over) anualizado na base 252 no dia, via pyield (série do Banco Central). NaN se indisponível."""
    try:
        import pyield as yd
        f = getattr(yd, "di_over", None) or getattr(getattr(yd, "bc", None), "di_over")
        return float(f(pd.Timestamp(data).date()))
    except Exception:  # noqa: BLE001
        return float("nan")
