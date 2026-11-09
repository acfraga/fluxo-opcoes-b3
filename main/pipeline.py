"""Orquestra o processamento de um pregão."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import CORRETORAS, MAPA_SUBJACENTE, Parametros
from .enriquecimento import Universo, enriquecer_negocios, fluxo_corretoras, montar_universo, perfil_corretoras
from .estruturas import anotar_com_oi, detectar_eventos, quantidade_financiamento, spread_di
from .leitura import inventariar, ler_pasta
from .painel import montar_resumo
from .posicoes import analisar_posicoes, mapa_vencimento, resumir_posicoes


@dataclass
class ResultadoDia:
    data: pd.Timestamp
    params: Parametros
    inventario: pd.DataFrame
    universo: Universo
    negocios: pd.DataFrame       # negócio a negócio enriquecido (opções + ações-objeto)
    perfil: pd.DataFrame         # corretoras por ativo (participação, equilíbrio, classe)
    fluxo: pd.DataFrame          # fluxo líquido por corretora e ativo, sem diretos e sem financiamento
    eventos: pd.DataFrame        # estruturas, blocos e diretos
    pos_series: pd.DataFrame
    pos_ativos: pd.DataFrame
    mapa: pd.DataFrame
    resumo: pd.DataFrame
    avisos: list = field(default_factory=list)

    @property
    def estruturas(self):
        return self.eventos[self.eventos["tipo_evento"] == "estrutura"] if len(self.eventos) else self.eventos

    @property
    def blocos(self):
        return self.eventos[self.eventos["tipo_evento"] == "bloco"] if len(self.eventos) else self.eventos

    @property
    def diretos(self):
        return self.eventos[self.eventos["tipo_evento"] == "direto"] if len(self.eventos) else self.eventos


_DATA_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{2})-(\d{2})-(\d{4})")


def data_no_nome(nome) -> pd.Timestamp | None:
    """Data em nomes como '2026-10-27' ou 'Posições em aberto-27-10-2026.csv'."""
    m = _DATA_RE.search(str(nome))
    if not m:
        return None
    try:
        if m.group(1):
            return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return pd.Timestamp(int(m.group(6)), int(m.group(5)), int(m.group(4)))
    except ValueError:
        return None


def pregao_anterior(pasta_dados, data) -> Path | None:
    """Pasta AAAA-MM-DD mais recente ANTERIOR a `data` que tenha arquivo de posições em aberto."""
    data = pd.Timestamp(data).normalize()
    cands = sorted(((data_no_nome(p.name), p) for p in Path(pasta_dados).iterdir() if p.is_dir()),
                   key=lambda x: x[0] or pd.Timestamp.min, reverse=True)
    for d, p in cands:
        if d is not None and d < data and (inventariar(p)["tipo"] == "posicoes").any():
            return p
    return None


def _diagnostico_universo(tabelas: dict) -> str:
    cad = tabelas["cadastro"]
    ops = cad[cad["tipo"].isin(["C", "P"])]
    if ops.empty:
        return ("Nenhuma série de opção no cadastro (tipo, strike e vencimento vazios). "
                "Confira o mapeamento de colunas com inventariar().")
    raiz = (ops["ativo"] if "ativo" in ops else ops["ticker"].str[:4]).fillna("?").astype(str)
    top = ", ".join(f"{k} ({v})" for k, v in raiz.value_counts().head(10).items())
    msg = f"Nenhuma série de opção de ação foi montada. O cadastro tem {len(ops)} opções, nas raízes: {top}. "
    if (raiz.str.len() >= 4).any():
        return msg + ("As ações-objeto não têm preço no consolidado: inclua 'Negócios consolidados do pregão' "
                      "do capítulo Renda variável.")
    return msg + ("Não há raízes de ações (como PETR): este é o cadastro do capítulo Derivativos de bolsa. "
                  "Baixe 'Cadastro de instrumentos', 'Negócios consolidados do pregão' e 'Negócio a negócio' "
                  "do capítulo Renda variável.")


def _inferir_data(tabelas: dict, pasta) -> pd.Timestamp:
    for tipo in ("negocios", "posicoes", "consolidado", "cadastro"):
        t = tabelas.get(tipo)
        if t is not None and "data" in t and t["data"].notna().any():
            return pd.Timestamp(t["data"].mode().iloc[0]).normalize()
    try:
        return pd.Timestamp(Path(pasta).name).normalize()
    except ValueError as e:
        raise ValueError("Não consegui inferir a data; passe data='AAAA-MM-DD'.") from e


def processar_dia(pasta_dia, pasta_anterior=None, data=None, params: Parametros | None = None,
                  corretoras: dict | None = None, mapa_subjacente: dict | None = None, ativos=None,
                  overrides: dict | None = None, adtv_externo=None, curva_di=None) -> ResultadoDia:
    """pasta_dia: CSVs do pregão (negócio a negócio, posições em aberto, cadastro, negócios consolidados).
    pasta_anterior: posições em aberto do pregão anterior (habilita Δ por componente e Δ de clientes).
    adtv_externo: {ativo: ADTV} (ex.: média de 20 pregões do histórico); sem isso usa o volume do dia.
    curva_di: {DU: taxa a.a.} para o spread das taxas de financiamento; sem ela, usa params.taxa_pre_aa.
    """
    params = params or Parametros()
    corretoras = CORRETORAS if corretoras is None else corretoras
    mapa_subjacente = MAPA_SUBJACENTE if mapa_subjacente is None else mapa_subjacente
    avisos = []

    tabelas, inv = ler_pasta(pasta_dia, overrides, min_notional_acoes=params.min_notional_acao)
    for tipo in ("cadastro", "consolidado"):
        if tipo not in tabelas:
            dica = ""
            nr = inv[inv["tipo"].isin(["não reconhecido", "erro"])]
            if len(nr):
                dica = " Arquivos não reconhecidos: " + "; ".join(
                    f"{r.arquivo} (parece {r.tipo_provavel}; faltou: {', '.join(r.faltando) or '?'})"
                    for r in nr.itertuples())
            raise FileNotFoundError(f"Falta o arquivo de {tipo} em {pasta_dia}.{dica}")
    data = pd.Timestamp(data).normalize() if data else _inferir_data(tabelas, pasta_dia)
    for arq in inv["arquivo"]:
        d = data_no_nome(arq)
        if d is not None and d != data:
            avisos.append(f"O arquivo '{arq}' parece ser de {d:%d/%m/%Y}, mas o pregão é {data:%d/%m/%Y}.")
    if pasta_anterior:
        datas_ant = [data_no_nome(Path(pasta_anterior).name)] + [data_no_nome(a) for a in inventariar(pasta_anterior)["arquivo"]]
        ruins = [d for d in datas_ant if d is not None and d >= data]
        if ruins:
            raise ValueError(f"O pregão anterior ({Path(pasta_anterior).name}) tem data {ruins[0]:%d/%m/%Y}, que não é "
                             f"anterior a {data:%d/%m/%Y}: o ΔOI sairia invertido. Use pregao_anterior(PASTA_DADOS, DATA).")
    neg = tabelas.get("negocios")
    if neg is None:
        avisos.append("Sem negócio a negócio: estruturas, blocos e fluxo por corretora não calculados.")
    univ = montar_universo(tabelas["cadastro"], tabelas["consolidado"], data, params, negocios=neg,
                           mapa_subjacente=mapa_subjacente, adtv_externo=adtv_externo, ativos=ativos,
                           curva_di=curva_di)
    if univ.series.empty:
        raise ValueError(_diagnostico_universo(tabelas))
    cons = tabelas["consolidado"]
    if "volume" not in cons or cons["volume"].isna().all():
        avisos.append("Consolidado sem volume financeiro reconhecido: ADTV indisponível. Veja o inventário.")
    fonte = univ.series["iv_fonte"].value_counts()
    if fonte.drop("mercado", errors="ignore").sum():
        avisos.append("IV sem preço de mercado em parte das séries: " +
                      ", ".join(f"{k}: {v}" for k, v in fonte.items()))
    if adtv_externo is None:
        avisos.append("ADTV = volume financeiro do próprio dia (passe adtv_externo com a média do histórico).")
    if curva_di is None or not len(curva_di):
        avisos.append(f"Sem curva do DI1: Black-Scholes e spread usam taxa_pre_aa = {params.taxa_pre_aa:.2%} (ilustrativa).")

    if neg is not None:
        negx = enriquecer_negocios(neg, univ)
        perfil = perfil_corretoras(negx, params, corretoras)
        eventos = detectar_eventos(negx, perfil, univ.adtv, params, corretoras)
        fin = eventos["financiamento"].eq(True) if len(eventos) else pd.Series(dtype=bool)
        ids_fin = frozenset().union(*eventos.loc[fin, "ids"]) if fin.any() else frozenset()
        fluxo = fluxo_corretoras(negx, perfil, ids_fin)
    else:
        negx, perfil, fluxo = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        eventos = pd.DataFrame(columns=["tipo_evento"])

    pos_d1 = None
    if pasta_anterior:
        t1, _ = ler_pasta(pasta_anterior, overrides, tipos=("posicoes",))
        pos_d1 = t1.get("posicoes")
    if "posicoes" in tabelas:
        if pos_d1 is None:
            avisos.append("Sem posições do pregão anterior: Δ por componente e Δ de clientes indisponíveis "
                          "(usado o campo de variação do OI, se houver).")
        pos_series = analisar_posicoes(tabelas["posicoes"], univ, params, pos_d1,
                                       q_financiamento=quantidade_financiamento(eventos))
        pos_ativos = resumir_posicoes(pos_series, params)
        mapa = mapa_vencimento(pos_series, params)
        eventos = anotar_com_oi(eventos, pos_series, params)
    else:
        avisos.append("Sem arquivo de posições em aberto.")
        pos_series = pos_ativos = mapa = pd.DataFrame()

    eventos = spread_di(eventos, params, curva_di)
    if len(eventos) and "taxa_di_ref" in eventos:
        eventos["referencia_di"] = "DI1 (curva do dia)" if curva_di is not None and len(curva_di) else "taxa_pre_aa"
    resumo = montar_resumo(univ, eventos, pos_ativos, mapa, fluxo)
    return ResultadoDia(data=data, params=params, inventario=inv, universo=univ, negocios=negx, perfil=perfil, fluxo=fluxo,
                        eventos=eventos, pos_series=pos_series, pos_ativos=pos_ativos, mapa=mapa, resumo=resumo,
                        avisos=avisos)
