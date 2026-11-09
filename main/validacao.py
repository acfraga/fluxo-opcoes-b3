"""Histórico local e validação dos sinais (event study).

O boletim não serve de histórico; acumule os resumos diários (de arquivos baixados
manualmente ou do feed licenciado) e valide quando houver dezenas de pregões.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd


def salvar_historico(res, pasta="historico") -> Path:
    """Acrescenta o resumo e os eventos do dia em CSV (idempotente por data)."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    data = pd.Timestamp(res.data).normalize()
    alvos = {
        "resumo.csv": res.resumo.assign(data=data),
        "eventos.csv": res.eventos.drop(columns=["ids", "pernas", "contrapartes"], errors="ignore").assign(data=data),
        "concentradas.csv": (res.pos_series.loc[res.pos_series["concentrada"],
                                                 ["subjacente", "ticker", "tipo", "strike", "vencimento", "d_oi", "d_oi_ef",
                                                  "motor", "dn_d_oi_ex_trava", "pct_adtv"]].assign(data=data)
                             if len(res.pos_series) and "concentrada" in res.pos_series
                             else pd.DataFrame(columns=["data", "subjacente", "ticker"])),
        "precos.csv": pd.DataFrame({"data": data, "ticker": res.universo.spot.index, "preco": res.universo.spot.values,
                                    "volume": res.universo.volume.reindex(res.universo.spot.index).values}),
    }
    for nome, df in alvos.items():
        arq = pasta / nome
        if arq.exists():
            antigo = pd.read_csv(arq, parse_dates=["data"])
            df = pd.concat([antigo[antigo["data"] != data], df], ignore_index=True)
        df.to_csv(arq, index=False)
    return pasta


def carregar_historico(pasta="historico") -> dict:
    pasta = Path(pasta)
    return {p.stem: pd.read_csv(p, parse_dates=["data"]) for p in pasta.glob("*.csv")}


def adtv_historico(precos: pd.DataFrame, janela: int = 20, ate=None) -> pd.Series:
    """Média do volume financeiro dos últimos `janela` pregões até `ate` (exclusive), por ticker."""
    p = precos if ate is None else precos[precos["data"] < pd.Timestamp(ate)]
    p = p.sort_values("data").groupby("ticker").tail(janela)
    return p.groupby("ticker")["volume"].mean()


def adicionar_zscores(resumo_hist: pd.DataFrame, colunas=("fluxo_demais", "dn_titulares_oi"),
                      janela: int = 60, min_obs: int = 20) -> pd.DataFrame:
    """z-score de cada coluna contra a própria história do ativo (janela anterior, sem olhar o dia)."""
    h = resumo_hist.sort_values(["ativo", "data"]).copy()
    for c in colunas:
        g = h.groupby("ativo")[c]
        media = g.transform(lambda x: x.shift(1).rolling(janela, min_periods=min_obs).mean())
        desvio = g.transform(lambda x: x.shift(1).rolling(janela, min_periods=min_obs).std())
        h[f"z_{c}"] = (h[c] - media) / desvio
    return h


def _bh(p: np.ndarray) -> np.ndarray:
    """Benjamini–Hochberg."""
    p = np.asarray(p, dtype=float)
    n = len(p)
    ordem = np.argsort(p)
    ajust = np.empty(n)
    acum = 1.0
    for rank, i in enumerate(ordem[::-1], start=1):
        k = n - rank + 1
        acum = min(acum, p[i] * n / k)
        ajust[i] = acum
    return np.minimum(ajust, 1.0)


def event_study(sinais: pd.DataFrame, precos: pd.DataFrame, horizontes=(1, 5, 20),
                benchmark: str | None = "BOVA11") -> pd.DataFrame:
    """sinais: colunas data, ativo, direcao (+1/−1) e, opcionalmente, tipo.
    precos: formato longo (data, ticker, preco), como em historico/precos.csv.

    Retorno anormal = retorno do ativo − retorno do benchmark, de D (fechamento) a D+h,
    multiplicado pela direção. Também reporta a razão de vol realizada depois/antes.

    Para não inflar o t: (1) sinais do mesmo tipo e ativo cujas janelas se sobrepõem são
    descartados (fica o primeiro); (2) o retorno anormal é agregado por data e o teste é
    feito entre datas, já que sinais do mesmo dia são correlacionados.
    """
    wide = precos.pivot_table(index="data", columns="ticker", values="preco").sort_index()
    ret = np.log(wide).diff()
    datas = wide.index
    pos = {d: i for i, d in enumerate(datas)}
    s = sinais.copy()
    if "tipo" not in s:
        s["tipo"] = "sinal"
    linhas = []
    for _, r in s.iterrows():
        i = pos.get(pd.Timestamp(r["data"]))
        if i is None or r["ativo"] not in wide:
            continue
        for h in horizontes:
            if i + h >= len(datas):
                continue
            ra = math.log(wide[r["ativo"]].iloc[i + h] / wide[r["ativo"]].iloc[i])
            rb = 0.0
            if benchmark and benchmark in wide and benchmark != r["ativo"]:
                rb = math.log(wide[benchmark].iloc[i + h] / wide[benchmark].iloc[i])
            antes = ret[r["ativo"]].iloc[max(1, i - h + 1): i + 1].std()
            depois = ret[r["ativo"]].iloc[i + 1: i + h + 1].std()
            linhas.append(dict(tipo=r["tipo"], ativo=r["ativo"], i=i, h=h, car=r["direcao"] * (ra - rb),
                               vol_ratio=depois / antes if antes and antes > 0 else np.nan))
    if not linhas:
        return pd.DataFrame()
    obs = pd.DataFrame(linhas).sort_values("i")
    manter = []
    for _, g in obs.groupby(["tipo", "ativo", "h"], sort=False):
        ultimo = None
        for idx, i, h in zip(g.index, g["i"], g["h"]):
            if ultimo is None or i >= ultimo + h:
                manter.append(idx)
                ultimo = i
    obs = obs.loc[manter]
    por_data = obs.groupby(["tipo", "h", "i"]).agg(car=("car", "mean"), n=("car", "size"),
                                                   vol_ratio=("vol_ratio", "median")).reset_index()
    res = por_data.groupby(["tipo", "h"]).agg(
        n_sinais=("n", "sum"), n_datas=("car", "size"), car_medio=("car", "mean"), desvio=("car", "std"),
        acerto=("car", lambda x: (x > 0).mean()), vol_ratio_mediana=("vol_ratio", "median")).reset_index()
    res["t"] = res["car_medio"] / (res["desvio"] / np.sqrt(res["n_datas"]))
    res["p"] = [math.erfc(abs(t) / math.sqrt(2)) if np.isfinite(t) else np.nan for t in res["t"]]
    ok = res["p"].notna()
    res["p_bh"] = np.nan
    if ok.any():
        res.loc[ok, "p_bh"] = _bh(res.loc[ok, "p"].to_numpy())
    return res


def z_do_dia(resumo: pd.DataFrame, hist_resumo: pd.DataFrame, data, colunas=("fluxo_demais", "dn_titulares_oi"),
             min_obs: int = 10, janela: int = 60) -> pd.DataFrame:
    """z-score de cada ativo no dia contra o PRÓPRIO histórico (pregões anteriores a `data`, até `janela`).

    Mede o que é anormal para aquele ativo: R$ 300 mi de ΔOI é rotina em BOVA11 e evento raro numa mid cap.
    Reordena o resumo pelo maior |z| quando houver histórico suficiente (min_obs pregões).
    """
    data = pd.Timestamp(data).normalize()
    h = hist_resumo[hist_resumo["data"] < data].sort_values("data").groupby("ativo").tail(janela)
    r = resumo.copy()
    zc = []
    for c in colunas:
        if c not in h or c not in r:
            continue
        st = h.groupby("ativo")[c].agg(["mean", "std", "count"])
        m, sd, n = (r["ativo"].map(st[k]) for k in ("mean", "std", "count"))
        r[f"z_{c}"] = ((r[c] - m) / sd).where((n >= min_obs) & (sd > 0))
        zc.append(f"z_{c}")
    if zc:
        r["z_max"] = r[zc].abs().max(axis=1)
        if r["z_max"].notna().any():
            r = r.sort_values("z_max", ascending=False, na_position="last").reset_index(drop=True)
    return r


def _sequencia(valores) -> int:
    """Pregões seguidos, até o último, com o mesmo sinal (+n comprador/positivo, −n vendedor/negativo)."""
    s = np.sign(np.asarray(valores, dtype=float))
    s = s[~np.isnan(s)]
    if len(s) == 0 or s[-1] == 0:
        return 0
    n = 0
    for v in s[::-1]:
        if v != s[-1]:
            break
        n += 1
    return int(n * s[-1])


def resumo_periodo(pasta="historico", inicio=None, fim=None, min_obs_z: int = 10, janela_z: int = 60) -> dict:
    """Visão de vários pregões a partir do histórico salvo: persistência por ativo, eventos direcionais
    (sem financiamento) e séries com entrada concentrada em mais de um pregão."""
    h = carregar_historico(pasta)
    if "resumo" not in h:
        raise FileNotFoundError(f"Sem histórico em {pasta}: rode salvar_historico() a cada pregão.")
    def recorte(df):
        if df is None or df.empty:
            return pd.DataFrame() if df is None else df
        m = pd.Series(True, index=df.index)
        if inicio is not None:
            m &= df["data"] >= pd.Timestamp(inicio)
        if fim is not None:
            m &= df["data"] <= pd.Timestamp(fim)
        return df[m]
    r, e, c = recorte(h["resumo"]), recorte(h.get("eventos")), recorte(h.get("concentradas"))
    leit = r["leitura_oi"].fillna("").astype(str)
    sinal = ((r["n_estruturas"] > 0) | (r["n_blocos"] > 0) | (r.get("n_diretos_abertura", 0) > 0)
             | (r["n_concentradas"] > 0) | ~leit.isin(["", "sem variação relevante"]))
    g = r.assign(sinal=sinal.astype(int)).groupby("ativo").agg(
        dias=("data", "nunique"), dias_com_sinal=("sinal", "sum"), score_max=("score", "max"),
        score_medio=("score", "mean"), fluxo_inst=("fluxo_demais", "sum"), dn_oi=("dn_titulares_oi", "sum"),
        adtv_medio=("adtv", "mean"), estruturas=("n_estruturas", "sum"), blocos=("n_blocos", "sum"),
        diretos_abertura=("n_diretos_abertura", "sum"), concentradas=("n_concentradas", "sum"))
    g["fluxo_pct_adtv"] = g["fluxo_inst"] / g["adtv_medio"]
    g["oi_pct_adtv"] = g["dn_oi"] / g["adtv_medio"]
    txt: dict = {}
    for _, row in r[~leit.isin(["", "sem variação relevante"])].sort_values("data").iterrows():
        txt.setdefault(row["ativo"], []).append(f"{row['data']:%d/%m}: {row['leitura_oi']}")
    g["leituras"] = [" | ".join(txt.get(a, [])) for a in g.index]
    # anormalidade contra o próprio histórico e consistência de direção
    z = adicionar_zscores(r, colunas=("fluxo_demais", "dn_titulares_oi"), janela=janela_z, min_obs=min_obs_z)
    extra = {}
    for a, d in z.sort_values("data").groupby("ativo"):
        extra[a] = dict(
            dias_z_fluxo=int((d["z_fluxo_demais"].abs() >= 2).sum()), dias_z_oi=int((d["z_dn_titulares_oi"].abs() >= 2).sum()),
            z_ult_fluxo=d["z_fluxo_demais"].iloc[-1], z_ult_oi=d["z_dn_titulares_oi"].iloc[-1],
            pct_fluxo_pos=float((d["fluxo_demais"] > 0).mean()), pct_oi_pos=float((d["dn_titulares_oi"] > 0).mean()),
            seq_oi=_sequencia(d["dn_titulares_oi"]), seq_fluxo=_sequencia(d["fluxo_demais"]))
    g = g.join(pd.DataFrame.from_dict(extra, orient="index"))
    g["dias_anormais"] = g["dias_z_fluxo"] + g["dias_z_oi"]
    g["_ordem"] = g["oi_pct_adtv"].abs()
    g = g.reset_index().sort_values(["dias_anormais", "_ordem"], ascending=False).drop(columns="_ordem")
    ev = pd.DataFrame()
    if e is not None and len(e):
        fin = e["financiamento"].astype(str).str.lower().eq("true") if "financiamento" in e else False
        ev = e[e["tipo_evento"].isin(["estrutura", "bloco"]) & (e["direcao"] != 0) & ~fin].sort_values(["subjacente", "data"])
    corr = pd.DataFrame()
    if len(ev):
        e2 = ev.assign(alta=(ev["direcao"] > 0).astype(int), baixa=(ev["direcao"] < 0).astype(int))
        corr = e2.groupby(["subjacente", "corretora"]).agg(
            dias=("data", "nunique"), eventos=("data", "size"), dn_liquido=("dn_liquido", "sum"),
            dias_alta=("data", lambda x: x[e2.loc[x.index, "alta"] == 1].nunique()),
            dias_baixa=("data", lambda x: x[e2.loc[x.index, "baixa"] == 1].nunique()),
            datas=("data", lambda x: ", ".join(sorted({f"{d:%d/%m}" for d in x})))).reset_index()
        corr["mao_dupla"] = (corr["dias_alta"] >= 2) & (corr["dias_baixa"] >= 2)
        corr["pct_adtv"] = corr["dn_liquido"] / corr["subjacente"].map(g.set_index("ativo")["adtv_medio"])
        corr = corr[corr["dias"] >= 2].assign(_o=lambda d: d["pct_adtv"].abs()).sort_values(
            ["mao_dupla", "dias", "_o"], ascending=[True, False, False]).drop(columns="_o")
    series = pd.DataFrame()
    if c is not None and len(c):
        series = c.groupby(["subjacente", "ticker"]).agg(
            dias=("data", "nunique"), d_oi_total=("d_oi", "sum"), dn_total=("dn_d_oi_ex_trava", "sum"),
            datas=("data", lambda x: ", ".join(sorted(f"{d:%d/%m}" for d in x))),
            motores=("motor", lambda x: ", ".join(sorted(set(map(str, x)))))).reset_index()
        series = series[series["dias"] >= 2].sort_values(["dias", "dn_total"], ascending=False)
    return {"ativos": g, "eventos": ev, "series": series, "corretoras": corr, "financiamento": resumo_financiamento(e)}


def resumo_financiamento(eventos: pd.DataFrame | None) -> pd.DataFrame:
    """Por pregão e tipo: operações de financiamento com taxa, valor e taxa implícita contra o DI do mesmo prazo.

    Box (só opções) é a taxa mais limpa. Conversão inclui a ação: a taxa embute dividendos/JCP até o vencimento e o
    aluguel do papel, e por isso costuma sair abaixo do DI sem que o financiamento seja "barato"."""
    if eventos is None or eventos.empty or "taxa_implicita" not in eventos:
        return pd.DataFrame()
    fin = eventos["financiamento"].astype(str).str.lower().eq("true") if "financiamento" in eventos else False
    e = eventos[fin & pd.to_numeric(eventos["taxa_implicita"], errors="coerce").notna()].copy()
    if e.empty:
        return pd.DataFrame()
    for c in ("taxa_implicita", "taxa_di_ref", "spread_di", "pct_di", "valor_final"):
        e[c] = pd.to_numeric(e[c], errors="coerce") if c in e else np.nan
    ref = e["referencia_di"] if "referencia_di" in e else pd.Series("taxa_pre_aa ou curva do dia", index=e.index)
    est = e["estrutura"].astype(str) if "estrutura" in e else pd.Series("", index=e.index)
    tipo = np.select([est.str.contains("box"), est.str.contains("conversão")], ["box (só opções)", "conversão (com a ação)"],
                     "outro")
    g = e.assign(referencia=ref, tipo=tipo).groupby(["data", "tipo"]).agg(
        n=("taxa_implicita", "size"), valor=("valor_final", "sum"), taxa_mediana=("taxa_implicita", "median"),
        di_mediano=("taxa_di_ref", "median"), spread_mediano=("spread_di", "median"), pct_di_mediano=("pct_di", "median"),
        referencia=("referencia", "first")).reset_index()
    return g.sort_values(["data", "tipo"])


def recalcular_spread_historico(pasta="historico", fonte=None, tipo_taxa: str = "ajuste") -> pd.DataFrame:
    """Refaz taxa_di_ref, spread_di e pct_di dos eventos de financiamento já salvos, com a curva do DI1 de cada
    pregão (padrão: pyield). Não reprocessa os pregões; regrava eventos.csv. Devolve o status por data."""
    from .taxas import curva_di_pyield, interpolar_flat_forward, pct_do_di
    fonte = fonte or (lambda d: curva_di_pyield(d, tipo_taxa))
    arq = Path(pasta) / "eventos.csv"
    e = pd.read_csv(arq, parse_dates=["data"])
    fin = e["financiamento"].astype(str).str.lower().eq("true") & pd.to_numeric(e["du_fin"], errors="coerce").notna()
    for c in ("taxa_di_ref", "spread_di", "pct_di", "referencia_di"):
        if c not in e:
            e[c] = np.nan
    e["referencia_di"] = e["referencia_di"].astype(object)
    status = []
    for d in sorted(e.loc[fin, "data"].unique()):
        m = fin & (e["data"] == d)
        try:
            curva = fonte(pd.Timestamp(d))
            ref = interpolar_flat_forward(curva, pd.to_numeric(e.loc[m, "du_fin"], errors="coerce").to_numpy(dtype=float))
            taxa = pd.to_numeric(e.loc[m, "taxa_implicita"], errors="coerce").to_numpy(dtype=float)
            e.loc[m, "taxa_di_ref"], e.loc[m, "spread_di"], e.loc[m, "pct_di"] = ref, taxa - ref, pct_do_di(taxa, ref)
            e.loc[m, "referencia_di"] = f"DI1 {tipo_taxa}"
            status.append((pd.Timestamp(d), int(m.sum()), "ok"))
        except Exception as exc:  # noqa: BLE001
            status.append((pd.Timestamp(d), int(m.sum()), f"sem curva: {exc}"))
    e.to_csv(arq, index=False)
    return pd.DataFrame(status, columns=["data", "operacoes", "status"])
