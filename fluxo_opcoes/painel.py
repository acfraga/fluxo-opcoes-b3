"""Resumo por ativo e exportação do painel em Excel."""
from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import CLASSES_FORA_DO_FLUXO, Parametros
from .estruturas import _br, fmt_rs

# ---------------------------------------------------------------------------
# resumo
# ---------------------------------------------------------------------------


def _maior(ev: pd.DataFrame, subj: str, filtro=None) -> tuple[int, str, float]:
    if ev is None or ev.empty:
        return 0, "", np.nan
    e = ev[ev["subjacente"] == subj]
    if filtro is not None:
        e = e[filtro(e)]
    if e.empty:
        return 0, "", np.nan
    top = e.loc[e["dn_bruto"].idxmax()]
    quem = f"corretora {top['corretora']}" + (f" ({top['nome_corretora']})" if top.get("nome_corretora") else "")
    ex = top.get("n_execucoes", 1)
    if ex is not None and np.isfinite(ex) and ex > 1:
        quem += f", {int(ex)} execuções"
    if top["tipo_evento"] == "direto":
        txt = f"{top['n_pernas']} perna(s), {quem}, {fmt_rs(top['dn_bruto'])} bruto"
    else:
        txt = f"{top['estrutura']} — {top['leitura']} — {quem}, Δ líq. {fmt_rs(top['dn_liquido'])}"
    return len(e), txt, float(top["dn_bruto"])


def montar_resumo(univ, eventos: pd.DataFrame, pos_ativos: pd.DataFrame, mapa: pd.DataFrame,
                  fluxo_corr: pd.DataFrame) -> pd.DataFrame:
    ativos = sorted(univ.series["subjacente"].unique())
    est = eventos[eventos["tipo_evento"] == "estrutura"] if len(eventos) else eventos
    blo = eventos[eventos["tipo_evento"] == "bloco"] if len(eventos) else eventos
    dire = eventos[eventos["tipo_evento"] == "direto"] if len(eventos) else eventos
    fluxo = pd.Series(dtype=float)
    if fluxo_corr is not None and len(fluxo_corr):
        dentro = ~fluxo_corr["classe"].fillna("demais").isin(CLASSES_FORA_DO_FLUXO)
        fluxo = fluxo_corr[dentro].groupby("subjacente")["dn_liquido"].sum()
    pa = pos_ativos.set_index("subjacente") if len(pos_ativos) else pd.DataFrame()
    linhas = []
    for a in ativos:
        nao_fin = (lambda e: ~e["financiamento"].eq(True))
        n_est, txt_est, dn_est = _maior(est, a, nao_fin)
        n_dir_est = int(((est["subjacente"] == a) & (est["direcao"] != 0)).sum()) if len(est) else 0
        n_fin = int(((est["subjacente"] == a) & est["financiamento"].eq(True)).sum()) if len(est) else 0
        n_fin += int(((dire["subjacente"] == a) & dire["financiamento"].eq(True)).sum()) if len(dire) else 0
        n_blo, txt_blo, dn_blo = _maior(blo, a)
        n_dir = int((dire["subjacente"] == a).sum()) if len(dire) else 0
        tem_conf = len(dire) and "abriu_posicao" in dire.columns
        abriu = ((lambda e: e["abriu_posicao"].eq(True) & ~e["financiamento"].eq(True)) if tem_conf
                 else (lambda e: e["tipo_evento"].isna()))
        n_dir_ab, txt_dir, dn_dir = _maior(dire, a, abriu)   # só diretos que abriram posição entram no score
        pin = ""
        if len(mapa):
            mm = mapa[(mapa["subjacente"] == a) & mapa["pin_candidato"]]
            if len(mm):
                r = mm.iloc[0]
                pin = f"{_br(r['strike'])} ({pd.Timestamp(r['vencimento']):%d/%m}, {int(r['du'])} DU)"
        linhas.append(dict(
            ativo=a, spot=univ.spot.get(a, np.nan), adtv=univ.adtv.get(a, np.nan),
            fluxo_demais=float(fluxo.get(a, 0.0)),
            n_estruturas=n_est, n_direcionais=n_dir_est, n_financiamento=n_fin, maior_estrutura=txt_est,
            n_blocos=n_blo, maior_bloco=txt_blo, n_diretos=n_dir, n_diretos_abertura=n_dir_ab, maior_direto=txt_dir,
            dn_titulares_oi=float(pa["dn_titulares_ex_trava"].get(a, np.nan)) if len(pa) else np.nan,
            leitura_oi=pa["leitura_oi"].get(a, "") if len(pa) else "",
            n_concentradas=int(pa["n_concentradas"].get(a, 0)) if len(pa) else 0,
            pin=pin, _maior_evento=np.nanmax([dn_est, dn_blo, dn_dir, 0.0]),
        ))
    r = pd.DataFrame(linhas)
    if r.empty:
        return r
    base = r[["fluxo_demais", "dn_titulares_oi", "_maior_evento"]].abs().max(axis=1)
    r["score"] = base / r["adtv"]
    return r.drop(columns="_maior_evento").sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
FONTE = "Arial"
FMT = {
    "rs": '#,##0;(#,##0);"-"', "pct": '0.0%;(0.0%);"-"', "preco": "#,##0.00", "qtd": '#,##0;(#,##0);"-"',
    "int": "0", "data": "dd/mm/yyyy", "txt": "@", "iv": "0.0%", "dist": '0.0%;(0.0%);"-"', "num": '0.0;-0.0;"-"',

}
LARG = {"rs": 16, "pct": 10, "preco": 10, "qtd": 14, "int": 8, "data": 11, "txt": 18, "iv": 8, "dist": 9, "num": 8}


def _valor(v):
    if v is None:
        return None
    if isinstance(v, (frozenset, set, list, dict)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return "sim" if v else ""
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _num_br(v: float, fmt: str) -> str:
    """Número como texto no padrão brasileiro (ponto de milhar, vírgula decimal)."""
    if fmt in ("pct", "dist", "iv"):
        return "-" if v == 0 else f"{_br(100 * v, 1)}%"
    if fmt == "preco":
        return _br(v, 2)
    if fmt == "int":
        return _br(v, 0)
    if fmt == "num":
        return _br(v, 1)
    return "-" if v == 0 else _br(v, 0)          # rs, qtd


def _param_br(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if float(v).is_integer():
        return _br(v, 0)
    return _br(v, 6).rstrip("0").rstrip(",")


def _aba(wb: Workbook, nome: str, df: pd.DataFrame, cols: list[tuple], titulo: str,
         nota: str | None = None, formulas: dict | None = None, primeira: bool = False, texto_br: bool = False):
    """cols: [(coluna_df, rótulo, formato)]. formulas: {coluna: ('div'|'absdiv', num, den) | ('maxabs', [cols], den)}."""
    ws = wb.active if primeira else wb.create_sheet(nome)
    ws.title = nome
    ws["A1"] = titulo
    ws["A1"].font = Font(name=FONTE, size=12, bold=True)
    if nota:
        ws["A2"] = nota
        ws["A2"].font = Font(name=FONTE, size=9, italic=True, color="595959")
    hdr = 4
    letra = {c: get_column_letter(j) for j, (c, _, _) in enumerate(cols, start=1)}
    preench = PatternFill("solid", fgColor="1F3864")
    for j, (c, rot, fmt) in enumerate(cols, start=1):
        cel = ws.cell(row=hdr, column=j, value=rot)
        cel.font = Font(name=FONTE, size=10, bold=True, color="FFFFFF")
        cel.fill = preench
        cel.alignment = Alignment(wrap_text=True, vertical="center")
    formulas = formulas or {}
    fonte = Font(name=FONTE, size=10)
    direita = Alignment(horizontal="right", vertical="top")
    for i, rec in enumerate(df.to_dict("records")):
        row = hdr + 1 + i
        for j, (c, _, fmt) in enumerate(cols, start=1):
            if c in formulas and texto_br:      # sem fórmula: o valor sai calculado, como texto
                f = formulas[c]
                den = _valor(rec.get(f[2]))
                if f[0] == "maxabs":
                    num = max((abs(_valor(rec.get(x)) or 0.0) for x in f[1]), default=0.0)
                else:
                    num = _valor(rec.get(f[1]))
                    num = abs(num) if (num is not None and f[0] == "absdiv") else num
                val = num / den if (num is not None and den) else 0.0
            elif c in formulas:
                f = formulas[c]
                if f[0] == "div":
                    val = f"=IFERROR({letra[f[1]]}{row}/{letra[f[2]]}{row},0)"
                elif f[0] == "absdiv":
                    val = f"=IFERROR(ABS({letra[f[1]]}{row})/{letra[f[2]]}{row},0)"
                else:  # maxabs
                    termos = ",".join(f"ABS(N({letra[x]}{row}))" for x in f[1])
                    val = f"=IFERROR(MAX({termos})/{letra[f[2]]}{row},0)"
            else:
                val = _valor(rec.get(c))
            numero = isinstance(val, (int, float)) and not isinstance(val, bool)
            if texto_br and numero and fmt not in ("txt", "data"):
                val = _num_br(val, fmt)
            cel = ws.cell(row=row, column=j, value=val)
            cel.font = fonte
            if texto_br and numero and fmt not in ("txt", "data"):
                cel.number_format, cel.alignment = "@", direita
            else:
                cel.number_format = FMT[fmt]
            if fmt == "txt":
                cel.alignment = Alignment(wrap_text=False, vertical="top")
    for j, (c, rot, fmt) in enumerate(cols, start=1):
        larg = LARG[fmt]
        if fmt == "txt" and c in df:
            maxlen = df[c].astype(str).str.len().max() if len(df) else 0
            larg = int(min(70, max(len(rot) * 0.9, (maxlen or 0) * 0.95, 10)))
        ws.column_dimensions[get_column_letter(j)].width = max(larg, min(len(rot), 22))
    ws.row_dimensions[hdr].height = 30
    ws.freeze_panes = ws.cell(row=hdr + 1, column=2)
    if len(df):
        ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(cols))}{hdr + len(df)}"
    return ws


COLS_RESUMO = [
    ("ativo", "Ativo", "txt"), ("spot", "Spot (R$)", "preco"), ("adtv", "ADTV usado (R$)", "rs"),
    ("score", "Score (maior sinal / ADTV)", "pct"), ("z_max", "Maior |z| do dia (vs. histórico do ativo)", "num"),
    ("z_fluxo_demais", "z do fluxo", "num"), ("z_dn_titulares_oi", "z do ΔOI", "num"),
    ("fluxo_demais", "Fluxo institucional: Δ-notional líquido comprado, exceto formador/varejo/alto giro (R$)", "rs"),
    ("pct_fluxo", "Fluxo / ADTV", "pct"),
    ("n_estruturas", "Estruturas (exceto financiamento)", "int"), ("n_direcionais", "Direcionais", "int"),
    ("n_financiamento", "Financiamentos (estruturas e diretos)", "int"), ("maior_estrutura", "Maior estrutura", "txt"),
    ("n_blocos", "Blocos isolados", "int"), ("maior_bloco", "Maior bloco", "txt"),
    ("n_diretos", "Blocos diretos", "int"), ("n_diretos_abertura", "Diretos que abriram posição", "int"),
    ("maior_direto", "Maior direto que abriu posição", "txt"),
    ("dn_titulares_oi", "ΔOI em Δ-notional dos titulares, exceto travas e financiamento (R$)", "rs"), ("pct_oi", "ΔOI / ADTV", "pct"),
    ("leitura_oi", "Leitura das posições", "txt"), ("n_concentradas", "Séries concentradas", "int"),
    ("pin", "Pin candidato (vencimento próximo)", "txt"),
]

COLS_EVENTO = [
    ("subjacente", "Ativo", "txt"), ("hora", "Hora", "txt"), ("corretora", "Corretora (ponto de vista)", "int"),
    ("nome_corretora", "Nome", "txt"), ("classe", "Classe", "txt"), ("contrapartes_txt", "Contraparte(s)", "txt"),
    ("espelho", "Espelho (fez o oposto)", "txt"), ("estrutura", "Estrutura", "txt"), ("escopo", "Escopo", "txt"),
    ("leitura", "Leitura", "txt"),
    ("financiamento", "Financiamento", "txt"), ("taxa_implicita", "Taxa implícita (a.a., 252)", "pct"),
    ("n_pernas", "Pernas", "int"), ("n_execucoes", "Execuções", "int"),
    ("dn_liquido", "Δ-notional líquido (R$)", "rs"), ("dn_bruto", "Δ-notional bruto (R$)", "rs"),
    ("premio_liquido", "Prêmio líquido pago (R$)", "rs"), ("adtv", "ADTV (R$)", "rs"), ("pct_adtv", "Bruto / ADTV", "pct"),
    ("pernas_txt", "Pernas", "txt"), ("confirmacao_oi", "ΔOI confirma (fração)", "pct"),
    ("abriu_posicao", "Abriu posição", "txt"), ("obs_oi", "ΔOI das séries (dia)", "txt"),
]

COLS_DIRETO = [
    ("subjacente", "Ativo", "txt"), ("hora", "Hora", "txt"), ("corretora", "Corretora (nos dois lados)", "int"),
    ("nome_corretora", "Nome", "txt"), ("classe", "Classe", "txt"), ("n_pernas", "Pernas", "int"),
    ("n_execucoes", "Execuções", "int"), ("estrutura", "Padrão", "txt"), ("leitura", "Dica / leitura", "txt"),
    ("taxa_implicita", "Taxa implícita (a.a., 252)", "pct"), ("dn_bruto", "Δ-notional bruto (R$)", "rs"), ("premio_bruto", "Prêmio bruto (R$)", "rs"),
    ("adtv", "ADTV (R$)", "rs"), ("pct_adtv", "Bruto / ADTV", "pct"), ("pernas_txt", "Pernas (sem sinal)", "txt"),
    ("confirmacao_oi", "ΔOI confirma (fração)", "pct"), ("abriu_posicao", "Abriu posição", "txt"),
    ("obs_oi", "ΔOI das séries (dia)", "txt"),
]

COLS_FIN = [
    ("subjacente", "Ativo", "txt"), ("tipo_evento", "Origem", "txt"), ("hora", "Hora", "txt"),
    ("corretora", "Corretora", "int"), ("estrutura", "Estrutura", "txt"), ("du_fin", "DU até o vencimento", "int"),
    ("valor_final", "Valor no vencimento (R$)", "rs"), ("taxa_implicita", "Taxa implícita (a.a., 252)", "pct"),
    ("taxa_di_ref", "DI do mesmo prazo", "pct"), ("spread_di", "Spread vs DI (p.p.)", "pct"), ("pct_di", "% do DI", "pct"),
    ("n_execucoes", "Execuções", "int"), ("pernas_txt", "Pernas", "txt"),
]

COLS_FLUXO = [
    ("subjacente", "Ativo", "txt"), ("corretora", "Corretora", "int"), ("nome", "Nome", "txt"),
    ("classe", "Classe", "txt"), ("n_negocios", "Negócios", "qtd"), ("n_series", "Séries", "int"),
    ("participacao", "Participação", "pct"), ("desequilibrio", "Desequilíbrio C/V", "pct"),
    ("dn_liquido", "Δ-notional líquido comprado (R$)", "rs"), ("premio_liquido", "Prêmio líquido pago (R$)", "rs"),
]

COLS_SERIES = [
    ("subjacente", "Ativo", "txt"), ("ticker", "Série", "txt"), ("tipo", "Tipo", "txt"), ("strike", "Strike", "preco"),
    ("vencimento", "Vencimento", "data"), ("du", "DU", "int"), ("iv", "IV", "iv"), ("delta", "Delta", "preco"),
    ("oi", "OI", "qtd"), ("d_oi", "ΔOI", "qtd"), ("d_oi_ef", "ΔOI sem travas", "qtd"),
    ("d_coberta", "Δ coberta", "qtd"), ("d_travada", "Δ travada", "qtd"),
    ("d_descoberta", "Δ descoberta", "qtd"), ("d_n_clientes_1", "Δ clientes (campo 1)", "qtd"),
    ("d_n_clientes_2", "Δ clientes (campo 2)", "qtd"), ("motor", "Componente dominante", "txt"),
    ("sentido", "Sentido", "txt"), ("dn_d_oi", "ΔOI em Δ-notional (R$)", "rs"),
    ("dn_d_oi_ex_trava", "ΔOI sem travas em Δ-notional (R$)", "rs"), ("adtv", "ADTV (R$)", "rs"),
    ("pct_adtv", "|ΔOI sem travas| / ADTV", "pct"), ("concentrada", "Entrada concentrada", "txt"),
    ("trava_relevante", "Trava relevante (box?)", "txt"), ("q_financiamento", "Qtd. em financiamento no dia", "qtd"),
    ("explicada_fin", "Explicada por financiamento", "txt"),
]

COLS_ATIVOS = [
    ("subjacente", "Ativo", "txt"), ("spot", "Spot (R$)", "preco"), ("adtv", "ADTV (R$)", "rs"),
    ("dn_titulares", "Δ-notional titulares, total (R$)", "rs"),
    ("dn_titulares_ex_trava", "Δ-notional titulares, exceto travas e financiamento (R$)", "rs"),
    ("pct_adtv", "|Exceto travas| / ADTV", "pct"),
    ("dn_calls", "…em calls, exceto travas (R$)", "rs"), ("dn_puts", "…em puts, exceto travas (R$)", "rs"),
    ("lanc_desc_calls", "Δ lançadores a seco — calls (R$)", "rs"),
    ("lanc_desc_puts", "Δ lançadores a seco — puts (R$)", "rs"),
    ("lanc_cob_calls", "Δ lançadores cobertos — calls (R$)", "rs"), ("d_travada", "Δ travada (qtd)", "qtd"),
    ("nocional_trava", "Nocional travado adicionado (R$)", "rs"),
    ("n_explicadas_fin", "Séries explicadas por financiamento", "int"),
    ("n_concentradas", "Séries concentradas", "int"), ("leitura_oi", "Leitura", "txt"),
]

COLS_MAPA = [
    ("subjacente", "Ativo", "txt"), ("vencimento", "Vencimento", "data"), ("du", "DU", "int"),
    ("S", "Spot", "preco"), ("strike", "Strike", "preco"), ("dist", "Strike/spot − 1", "dist"),
    ("oi_calls", "OI calls", "qtd"), ("oi_puts", "OI puts", "qtd"), ("desc_calls", "Descoberta calls", "qtd"),
    ("desc_puts", "Descoberta puts", "qtd"), ("gama_1pct", "Gama de todo o OI por 1% (R$ de Δ)", "rs"),
    ("hedge_1pct_desc", "Hedge dos lançadores a seco por 1% (R$)", "rs"), ("pin_candidato", "Pin candidato", "txt"),
]

NOTAS = [
    "Leitura heurística de dados públicos: não é recomendação e não identifica o cliente final.",
    "Código de participante = corretora, não o comitente. 'Alto giro' = participação alta, compra/venda equilibradas e "
    "muitas séries (formador ou varejo de massa); o ponto de vista dessas corretoras não é usado para ler estruturas.",
    "Estruturas: pernas da mesma corretora no mesmo ativo dentro da janela, com quantidades proporcionais. "
    "A leitura é do ponto de vista da corretora indicada; a coluna 'Espelho' traz quem fez o oposto quando também não é alto giro.",
    "Negócios diretos (mesma corretora nos dois lados): forma e direção indeterminadas; 'Abriu posição' = o aumento de OI "
    "das séries cobre ao menos a fração configurada da quantidade. Só esses entram no score.",
    "Opções sobre o índice (raiz IBOV) ficam fora: o consolidado traz o índice, sem ADTV utilizável.",
    "Tamanho: Δ-notional acima do mínimo absoluto E da fração do ADTV (ou prêmio acima do mínimo). Execuções repetidas "
    "da mesma corretora no dia são somadas antes do filtro (estruturas iguais; blocos = fluxo líquido por série).",
    "Corretora cadastrada que age como alto giro num ativo (participação alta, compra/venda equilibradas) é tratada como "
    "alto giro naquele ativo, exceto quando cadastrada como formador.",
    "Entrada concentrada é medida sem a parcela travada; travas/box relevantes aparecem à parte (provável financiamento).",
    "Série cujo ΔOI é explicado (>= 50%) por box/conversão/sintético negociados no dia fica fora da concentração e das "
    "leituras por ativo; a contagem aparece em 'Séries explicadas por financiamento'.",
    "Spread vs DI: taxa implícita menos o DI do mesmo prazo (curva_di) ou, sem curva, menos taxa_pre_aa.",
    "Sintético (call e put no mesmo strike, sem a ação) é tratado como provável perna de conversão/reversão: financiamento, "
    "sem direção. Diretos com padrão de box, conversão ou sintético (inclusive somando os diretos da corretora no dia) "
    "também são financiamento e ficam fora do score.",
    "Escopo 'dia' = estrutura montada pela mesma corretora em pernas executadas em horários diferentes. Calendário cuja "
    "perna curta encerra posição e a longa abre (pelo ΔOI) vira 'rolagem (confirmada pelo ΔOI)'.",
    "Fluxo institucional = soma do Δ-notional líquido comprado em opções pelas corretoras que não são formador, varejo "
    "ou alto giro, sem negócios diretos e sem pernas de box/conversão.",
    "Taxa implícita de box/conversão/financiamento: (valor no vencimento / desembolso)^(252/DU) − 1, com preços médios das pernas.",
    "Δ-notional = quantidade × delta × spot × multiplicador. Delta por Black-Scholes com IV do último preço (ou mediana), "
    "prazo em DU/252 e taxa pré do prazo pela curva do DI1 (sem curva, taxa_pre_aa).",
    "Posições: 'titulares' recebem o delta do ΔOI; lançadores têm o oposto. Campos de clientes 1/2 = BrrwrQty/LndrQty do "
    "arquivo de posições — confirmar qual lado cada um representa em opções.",
    "Mapa de vencimento: hedge por 1% supõe que lançadores a seco façam delta hedge, o que nem sempre é verdade.",
]


def exportar_excel(res, caminho, params: Parametros | None = None, aviso: str | None = None,
                   texto_br: bool = False) -> str:
    """res: ResultadoDia (pipeline). Grava o painel e devolve o caminho.

    O Excel mostra ponto/vírgula conforme a configuração do computador de quem abre o arquivo: o .xlsx guarda
    números, não a pontuação. texto_br=True grava os números já formatados no padrão brasileiro, como texto,
    para aparecerem iguais em qualquer computador (perde ordenação numérica e as fórmulas de % do ADTV).
    """
    params = params or res.params
    data = pd.Timestamp(res.data).strftime("%d/%m/%Y")
    pre = f"{aviso} — " if aviso else ""
    wb = Workbook()
    resumo = res.resumo.copy()
    _aba(wb, "Resumo", resumo, COLS_RESUMO, f"{pre}Fluxo e posições em opções — {data}",
         "Ordenado por score = maior entre |fluxo|, |ΔOI titulares| e maior evento, dividido pelo ADTV.",
         formulas={"pct_fluxo": ("absdiv", "fluxo_demais", "adtv"), "pct_oi": ("absdiv", "dn_titulares_oi", "adtv")},
         primeira=True, texto_br=texto_br)
    ev = res.eventos
    est = ev[(ev["tipo_evento"] == "estrutura")] if len(ev) else ev
    blo = ev[(ev["tipo_evento"] == "bloco")] if len(ev) else ev
    dire = ev[(ev["tipo_evento"] == "direto")] if len(ev) else ev
    if len(dire) and "abriu_posicao" in dire:
        dire = dire.sort_values(["abriu_posicao", "dn_bruto"], ascending=[False, False])
    f_pct = {"pct_adtv": ("absdiv", "dn_bruto", "adtv")}
    _aba(wb, "Estruturas", est, COLS_EVENTO, f"{pre}Estruturas multi-perna — {data}",
         "Pernas da mesma corretora em até a janela configurada, com quantidades proporcionais.", f_pct, texto_br=texto_br)
    _aba(wb, "Blocos isolados", blo, COLS_EVENTO, f"{pre}Blocos em uma série — {data}",
         "Agrupamentos de uma perna acima do tamanho mínimo.", f_pct, texto_br=texto_br)
    _aba(wb, "Blocos diretos", dire, COLS_DIRETO, f"{pre}Negócios diretos (mesma corretora nos dois lados) — {data}",
         "Forma e direção indeterminadas: confira o ΔOI das séries.", f_pct, texto_br=texto_br)
    fin = ev[ev["financiamento"].eq(True)].sort_values("valor_final", ascending=False) if len(ev) else ev
    _aba(wb, "Financiamento", fin, COLS_FIN, f"{pre}Financiamento via opções (box, conversão, sintético) — {data}",
         "Taxa = (valor no vencimento / desembolso)^(252/DU) − 1 com preços médios das pernas. Conversão embute dividendos/JCP e aluguel da ação: compare box com box.", texto_br=texto_br)
    fl = res.fluxo.copy()
    if len(fl):
        fl["ordem"] = fl["dn_liquido"].abs()
        fl = fl.sort_values(["subjacente", "ordem"], ascending=[True, False]).groupby("subjacente").head(10)
    _aba(wb, "Fluxo por corretora", fl, COLS_FLUXO, f"{pre}Top 10 corretoras por |Δ-notional líquido| em opções — {data}",
         "Sem negócios diretos e sem pernas de financiamento (box/conversão). Soma de todas as corretoras de um ativo = 0.", texto_br=texto_br)
    ps = res.pos_series
    if len(ps):
        ps = ps[(ps["d_oi"].fillna(0) != 0)].copy()
        ps["ordem"] = ps["dn_d_oi_ex_trava"].abs()
        ps = ps.sort_values(["concentrada", "ordem"], ascending=[False, False])
    f_ps = {"pct_adtv": ("absdiv", "dn_d_oi_ex_trava", "adtv")}
    _aba(wb, "Posições - séries", ps, COLS_SERIES, f"{pre}Variação das posições em aberto por série — {data}",
         "Concentradas primeiro. Séries sem variação omitidas.", f_ps, texto_br=texto_br)
    _aba(wb, "Posições - ativos", res.pos_ativos, COLS_ATIVOS, f"{pre}Variação das posições por ativo — {data}",
         "Titulares recebem o delta do ΔOI; lançadores a seco/cobertos em sinal de lançador.",
         {"pct_adtv": ("absdiv", "dn_titulares_ex_trava", "adtv")}, texto_br=texto_br)
    _aba(wb, "Mapa de vencimento", res.mapa, COLS_MAPA, f"{pre}Posicionamento no vencimento mais próximo — {data}",
         "Strikes a até 10% do spot. Pin candidato = maior OI a até a banda ATM configurada.", texto_br=texto_br)
    ws = wb.create_sheet("Parâmetros e notas")
    ws["A1"] = f"{pre}Parâmetros e notas"
    ws["A1"].font = Font(name=FONTE, size=12, bold=True)
    for j, rot in enumerate(("Parâmetro", "Valor", "Descrição"), start=1):
        c = ws.cell(row=3, column=j, value=rot)
        c.font = Font(name=FONTE, size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
    r = 4
    for nome, valor, desc in params.como_tabela():
        if isinstance(valor, (tuple, list, set, frozenset)):
            valor = ", ".join(map(str, valor)) or "(nenhuma)"
        if texto_br:
            valor = _param_br(valor)
        for j, v in enumerate((nome, valor, desc), start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.font = Font(name=FONTE, size=10, color="0000FF" if j == 2 else "000000")
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Notas").font = Font(name=FONTE, size=10, bold=True)
    for n in NOTAS + list(getattr(res, "avisos", [])):
        r += 1
        ws.cell(row=r, column=1, value=f"• {n}").font = Font(name=FONTE, size=10)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 90
    wb.save(caminho)
    return str(caminho)


COLS_PER_ATIVOS = [
    ("ativo", "Ativo", "txt"), ("dias", "Pregões", "int"), ("dias_anormais", "Pregões anormais (|z| ≥ 2, fluxo + ΔOI)", "int"),
    ("z_ult_oi", "z do ΔOI no último pregão", "num"), ("z_ult_fluxo", "z do fluxo no último pregão", "num"),
    ("seq_oi", "Sequência atual do ΔOI (pregões)", "int"), ("pct_oi_pos", "% pregões com ΔOI titulares > 0", "pct"),
    ("pct_fluxo_pos", "% pregões com fluxo comprador", "pct"), ("dias_com_sinal", "Pregões com sinal", "int"),
    ("score_max", "Score máximo", "pct"), ("score_medio", "Score médio", "pct"),
    ("fluxo_inst", "Fluxo institucional somado (R$)", "rs"), ("fluxo_pct_adtv", "Fluxo / ADTV médio", "pct"),
    ("dn_oi", "ΔOI titulares somado, exceto travas e financiamento (R$)", "rs"), ("oi_pct_adtv", "ΔOI / ADTV médio", "pct"),
    ("estruturas", "Estruturas", "int"), ("blocos", "Blocos", "int"), ("diretos_abertura", "Diretos que abriram posição", "int"),
    ("concentradas", "Séries concentradas", "int"), ("leituras", "Leituras das posições, por pregão", "txt"),
]
COLS_PER_EVENTOS = [
    ("data", "Data", "data"), ("subjacente", "Ativo", "txt"), ("hora", "Hora", "txt"), ("corretora", "Corretora", "int"),
    ("tipo_evento", "Tipo", "txt"), ("estrutura", "Estrutura", "txt"), ("leitura", "Leitura", "txt"),
    ("n_execucoes", "Execuções", "int"), ("dn_liquido", "Δ-notional líquido (R$)", "rs"), ("pct_adtv", "Bruto / ADTV", "pct"),
    ("abriu_posicao", "Abriu posição", "txt"), ("pernas_txt", "Pernas", "txt"),
]
COLS_PER_CORR = [
    ("subjacente", "Ativo", "txt"), ("corretora", "Corretora", "int"), ("dias", "Pregões com evento", "int"),
    ("eventos", "Eventos", "int"), ("dias_alta", "Pregões de alta", "int"), ("dias_baixa", "Pregões de baixa", "int"),
    ("mao_dupla", "Mão dupla (provável liquidez/hedge)", "txt"), ("dn_liquido", "Δ-notional líquido no período (R$)", "rs"),
    ("pct_adtv", "Líquido / ADTV médio", "pct"), ("datas", "Datas", "txt"),
]

COLS_PER_FIN = [
    ("data", "Data", "data"), ("tipo", "Tipo", "txt"), ("n", "Operações", "int"), ("valor", "Valor no vencimento (R$)", "rs"),
    ("taxa_mediana", "Taxa implícita mediana", "pct"), ("di_mediano", "DI do mesmo prazo (mediana)", "pct"),
    ("spread_mediano", "Spread mediano (p.p.)", "pct"), ("pct_di_mediano", "% do DI (mediana)", "pct"),
    ("referencia", "Referência usada", "txt"),
]

COLS_PER_SERIES = [
    ("subjacente", "Ativo", "txt"), ("ticker", "Série", "txt"), ("dias", "Pregões com entrada concentrada", "int"),
    ("datas", "Datas", "txt"), ("d_oi_total", "ΔOI somado", "qtd"), ("dn_total", "ΔOI sem travas somado (R$)", "rs"),
    ("motores", "Componentes", "txt"),
]


def exportar_periodo(per: dict, caminho, texto_br: bool = False) -> str:
    """Grava a visão de período (resumo_periodo) em Excel."""
    wb = Workbook()
    ini = per["ativos"]
    _aba(wb, "Ativos", per["ativos"], COLS_PER_ATIVOS, "Persistência por ativo no período",
         "Ordenado por pregões anormais (|z| >= 2 contra o próprio histórico) e |ΔOI|/ADTV. z exige ~10 pregões anteriores.",
         primeira=True, texto_br=texto_br)
    if len(per.get("corretoras", [])):
        _aba(wb, "Corretoras recorrentes", per["corretoras"], COLS_PER_CORR,
             "Corretoras com eventos direcionais no mesmo ativo em 2+ pregões",
             "Mão dupla = alta e baixa em 2+ pregões cada: típico de liquidez/hedge, não de posição. Ordenado sem mão dupla primeiro.",
             texto_br=texto_br)
    _aba(wb, "Eventos direcionais", per["eventos"], COLS_PER_EVENTOS, "Estruturas e blocos direcionais (sem financiamento)",
         "Ordenado por ativo e data.", texto_br=texto_br)
    if len(per.get("financiamento", [])):
        _aba(wb, "Financiamento", per["financiamento"], COLS_PER_FIN, "Financiamento via opções: taxa implícita contra o DI, por pregão",
             "DI do mesmo prazo pela curva do DI1 (flat-forward, base 252). Box (só opções) é a taxa limpa; conversão "
             "embute dividendos/JCP até o vencimento e o aluguel da ação, e tende a sair abaixo do DI.",
             texto_br=texto_br)
    _aba(wb, "Séries recorrentes", per["series"], COLS_PER_SERIES, "Séries com entrada concentrada em 2+ pregões",
         "Requer o arquivo concentradas.csv no histórico (gravado a partir desta versão).", texto_br=texto_br)
    wb.save(caminho)
    return str(caminho)
