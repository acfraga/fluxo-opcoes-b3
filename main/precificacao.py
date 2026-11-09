"""Precificação mínima para converter quantidades em delta-notional.

Black-Scholes com taxa contínua equivalente à pré 252 DU, prazo em DU/252 e sem
dividendos (a B3 ajusta strikes por proventos). Puts europeias e calls americanas
sem dividendo relevante ficam bem aproximadas; puts americanas fundas ITM, não.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:  # scipy é opcional
    from scipy.special import ndtr as _ndtr
except Exception:  # noqa: BLE001
    _ndtr = None

# Feriados nacionais (calendário ANBIMA) 2024–2028. Para outros anos, estenda a
# lista ou troque dias_uteis por pyield.bday.count.
FERIADOS_ANBIMA = np.array([
    "2024-01-01", "2024-02-12", "2024-02-13", "2024-03-29", "2024-04-21", "2024-05-01", "2024-05-30",
    "2024-09-07", "2024-10-12", "2024-11-02", "2024-11-15", "2024-11-20", "2024-12-25",
    "2025-01-01", "2025-03-03", "2025-03-04", "2025-04-18", "2025-04-21", "2025-05-01", "2025-06-19",
    "2025-09-07", "2025-10-12", "2025-11-02", "2025-11-15", "2025-11-20", "2025-12-25",
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-04-03", "2026-04-21", "2026-05-01", "2026-06-04",
    "2026-09-07", "2026-10-12", "2026-11-02", "2026-11-15", "2026-11-20", "2026-12-25",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-04-21", "2027-05-01", "2027-05-27",
    "2027-09-07", "2027-10-12", "2027-11-02", "2027-11-15", "2027-11-20", "2027-12-25",
    "2028-01-01", "2028-02-28", "2028-02-29", "2028-04-14", "2028-04-21", "2028-05-01", "2028-06-15",
    "2028-09-07", "2028-10-12", "2028-11-02", "2028-11-15", "2028-11-20", "2028-12-25",
], dtype="datetime64[D]")


def _d64(x):
    if isinstance(x, (pd.Series, pd.Index, np.ndarray, list)):
        return pd.to_datetime(pd.Series(x)).dt.normalize().to_numpy().astype("datetime64[D]")
    return np.datetime64(pd.Timestamp(x).date(), "D")


def dias_uteis(inicio, fim) -> np.ndarray:
    """DU entre inicio (inclusive) e fim (exclusive), como np.busday_count. NaT -> NaN."""
    a, b = _d64(inicio), _d64(fim)
    a_arr, b_arr = np.broadcast_arrays(np.atleast_1d(a), np.atleast_1d(b))
    ok = ~(np.isnat(a_arr) | np.isnat(b_arr))
    out = np.full(a_arr.shape, np.nan)
    out[ok] = np.busday_count(a_arr[ok], b_arr[ok], holidays=FERIADOS_ANBIMA)
    return out


def ncdf(x):
    x = np.asarray(x, dtype=float)
    if _ndtr is not None:
        return _ndtr(x)
    # Abramowitz & Stegun 26.2.17 (|erro| < 7.5e-8)
    p, b = 0.2316419, (0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429)
    ax = np.abs(x)
    t = 1.0 / (1.0 + p * ax)
    poli = t * (b[0] + t * (b[1] + t * (b[2] + t * (b[3] + t * b[4]))))
    n = 1.0 - npdf(ax) * poli
    return np.where(x >= 0, n, 1.0 - n)


def npdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def bs(S, K, T, r, sigma, call):
    """Retorna (preço, delta, gama) vetorizados. r contínuo; T em anos (DU/252)."""
    S, K, T, sigma = (np.asarray(v, dtype=float) for v in (S, K, T, sigma))
    call = np.asarray(call, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        sq = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / sq
        d2 = d1 - sq
        desc = np.exp(-r * T)
        preco = np.where(call, S * ncdf(d1) - K * desc * ncdf(d2), K * desc * ncdf(-d2) - S * ncdf(-d1))
        delta = np.where(call, ncdf(d1), ncdf(d1) - 1.0)
        gama = npdf(d1) / (S * sq)
    return preco, delta, gama


def vol_implicita(preco, S, K, T, r, call, lo: float = 0.01, hi: float = 4.0, iteracoes: int = 60):
    """Bisseção vetorizada. NaN quando o preço está fora do intervalo [preço(lo), preço(hi)]."""
    preco = np.asarray(preco, dtype=float)
    a, b = np.full(preco.shape, lo), np.full(preco.shape, hi)
    pa, pb = bs(S, K, T, r, a, call)[0], bs(S, K, T, r, b, call)[0]
    ok = np.isfinite(preco) & (preco > pa) & (preco < pb)
    for _ in range(iteracoes):
        m = 0.5 * (a + b)
        acima = bs(S, K, T, r, m, call)[0] > preco
        b, a = np.where(acima, m, b), np.where(acima, a, m)
    iv = 0.5 * (a + b)
    iv[~ok] = np.nan
    return iv


def taxa_continua(taxa_pre_aa: float) -> float:
    """Pré 252 DU -> taxa contínua equivalente na base DU/252."""
    return float(np.log1p(taxa_pre_aa))
