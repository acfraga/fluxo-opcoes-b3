"""Leitura dos arquivos do Boletim Diário (ou do feed licenciado) em formato canônico.

Pensado para arquivos baixados manualmente: detecta linha de cabeçalho (ignora
preâmbulos), separador, encoding e decimal com vírgula, identifica o tipo de
arquivo pelo cabeçalho e renomeia colunas para nomes canônicos.
"""
from __future__ import annotations

import csv
import itertools
import re
import unicodedata
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ALIASES, CAMPOS_MINIMOS

_STOP = {"de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "em", "no", "na", "por", "para"}
TIPOS = tuple(ALIASES)


def normalizar(nome) -> str:
    """'Preço do negócio' e 'PrecoNegocio' -> 'preconegocio'."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(nome))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return "".join(t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _STOP)


_ALIAS_NORM = {t: {c: [normalizar(a) for a in al] for c, al in campos.items()} for t, campos in ALIASES.items()}
_TODOS = {a for campos in _ALIAS_NORM.values() for al in campos.values() for a in al}


# ---------------------------------------------------------------------------
# detecção de formato
# ---------------------------------------------------------------------------
def _ler_linhas(path: Path, n: int = 40):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(itertools.islice(f, n)), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Encoding não reconhecido: {path}")


def detectar_formato(path) -> tuple[str, str, int]:
    """Retorna (encoding, separador, índice da linha de cabeçalho)."""
    linhas, enc = _ler_linhas(Path(path))
    melhor = (0, None, None)
    for i, linha in enumerate(linhas):
        for sep in (";", ",", "\t", "|"):
            cels = next(csv.reader([linha.rstrip("\r\n")], delimiter=sep))
            if len(cels) < 3:
                continue
            score = sum(normalizar(c) in _TODOS for c in cels)
            if score > melhor[0]:
                melhor = (score, i, sep)
    if melhor[0] < 2:
        raise ValueError(f"Não encontrei cabeçalho reconhecível em {path}. Veja ALIASES em config.py.")
    return enc, melhor[2], melhor[1]


def mapear_colunas(colunas, tipo: str, override: dict | None = None) -> dict:
    """{coluna_original: campo_canonico}. override = {campo_canonico: coluna_original}."""
    norm = {c: normalizar(c) for c in colunas}
    mapa, usados = {}, set()
    for campo, original in (override or {}).items():
        if original in colunas:
            mapa[original] = campo
            usados.add(original)
    for campo, aliases in _ALIAS_NORM[tipo].items():
        if campo in mapa.values():
            continue
        for a in aliases:
            cand = [c for c in colunas if c not in usados and norm[c] == a]
            if cand:
                mapa[cand[0]] = campo
                usados.add(cand[0])
                break
    return mapa


def _colunas(path, enc, sep, hdr):
    return pd.read_csv(path, sep=sep, skiprows=hdr, nrows=0, encoding=enc).columns.tolist()


def identificar_tipo(path, overrides: dict | None = None) -> dict:
    enc, sep, hdr = detectar_formato(path)
    cols = _colunas(path, enc, sep, hdr)
    melhor = None
    for tipo in TIPOS:
        mapa = mapear_colunas(cols, tipo, (overrides or {}).get(tipo))
        campos = set(mapa.values())
        minimos = CAMPOS_MINIMOS[tipo]
        cobertura = sum(any(c in campos for c in alt) for alt in minimos) / len(minimos)
        chave = (cobertura, len(campos))
        if melhor is None or chave > melhor[0]:
            melhor = (chave, tipo, mapa)
    (cobertura, _), tipo, mapa = melhor
    campos = set(mapa.values())
    faltando = ["/".join(alt) for alt in CAMPOS_MINIMOS[tipo] if not any(c in campos for c in alt)]
    return {
        "arquivo": Path(path).name,
        "tipo": tipo if cobertura == 1 else "não reconhecido",
        "tipo_provavel": tipo,
        "faltando": faltando,
        "cobertura": cobertura,
        "encoding": enc,
        "separador": sep,
        "linha_cabecalho": hdr,
        "mapeadas": {v: k for k, v in mapa.items()},
        "ignoradas": [c for c in cols if c not in mapa],
    }


# ---------------------------------------------------------------------------
# conversões
# ---------------------------------------------------------------------------
def _num(s: pd.Series, ptbr: bool | None = None) -> pd.Series:
    """ptbr=True: ponto é milhar e vírgula é decimal ("35.500" -> 35500; "38,50" -> 38.5).

    A convenção é decidida pelo ARQUIVO (ver ler_tabela), não pela coluna: no arquivo de posições
    da B3 as quantidades vêm com ponto de milhar e sem nenhuma vírgula.
    """
    if s.dtype.kind in "if":
        return s.astype(float)
    t = s.astype(str).str.strip().str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False)
    t = t.where(~t.isin(["", "-", "nan", "NaN", "None", "<NA>"]))
    if ptbr is None:
        ptbr = bool(t.str.contains(",", regex=False, na=False).any())
    if ptbr:
        t = t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(t, errors="coerce")


def _convencao_ptbr(df: pd.DataFrame, sep: str, colunas) -> bool:
    """Arquivo com ';' (padrão do Excel/B3 em português) ou com vírgula em algum número -> pt-BR."""
    if sep == ";":
        return True
    return any(df[c].astype(str).str.contains(",", regex=False).any() for c in colunas if c in df)


def _data(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d = pd.to_datetime(t, format="%Y-%m-%d", errors="coerce")
        if d.notna().mean() < 0.5:
            d2 = pd.to_datetime(t, format="%d/%m/%Y", errors="coerce")
            if d2.notna().mean() > d.notna().mean():
                d = d2
        if d.notna().mean() < 0.5:
            d = pd.to_datetime(t, errors="coerce", dayfirst=True)
    return d.dt.normalize()


def _hora_ms(s: pd.Series) -> pd.Series:
    """HHMMSSNNN (com ou sem zero à esquerda), HHMMSS, HH:MM:SS(.fff) ou data-hora -> ms desde 0h."""
    t = s.astype(str).str.strip()
    out = pd.Series(np.nan, index=t.index, dtype=float)
    dig = t.str.fullmatch(r"\d{5,9}").eq(True)
    if dig.any():
        d = t[dig]
        longo = d.str.len() >= 7
        d = d.where(~longo, d.str.zfill(9)).where(longo, d.str.zfill(6) + "000")
        hh, mm = d.str[0:2].astype(int), d.str[2:4].astype(int)
        ss, ms = d.str[4:6].astype(int), d.str[6:9].astype(int)
        out[dig] = ((hh * 60 + mm) * 60 + ss) * 1000 + ms
    resto = ~dig & t.str.contains(":", regex=False, na=False)
    if resto.any():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if t[resto].str.contains(r"[-/]", regex=True).any():
                dt = pd.to_datetime(t[resto], errors="coerce", dayfirst=True)
                out[resto] = (dt.dt.hour * 3600 + dt.dt.minute * 60 + dt.dt.second) * 1000 + dt.dt.microsecond // 1000
            else:
                out[resto] = pd.to_timedelta(t[resto], errors="coerce").dt.total_seconds() * 1000
    return out


def _txt(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip().str.upper()
    return t.where(~t.isin(["", "-", "–", "—", "NAN", "NONE", "<NA>"]))


def tipo_opcao_de(valor) -> str | None:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return None
    s = normalizar(valor)
    if s in ("c", "call", "opc") or "call" in s or "compra" in s:
        return "C"
    if s in ("p", "put", "opv") or "put" in s or "venda" in s:
        return "P"
    return None


def _tipo_pelo_ticker(ticker: str) -> str | None:
    m = re.match(r"^[A-Z0-9]{4}([A-X])\d", str(ticker))
    if not m:
        return None
    return "C" if m.group(1) <= "L" else "P"


# ---------------------------------------------------------------------------
# pós-processamento por tipo
# ---------------------------------------------------------------------------
def _pos_negocios(df: pd.DataFrame, ptbr: bool | None = None) -> pd.DataFrame:
    df = df.dropna(subset=["ticker"]).copy()
    df["preco"], df["qtd"] = _num(df["preco"], ptbr), _num(df["qtd"], ptbr)
    df["hora_ms"] = _hora_ms(df["hora"])
    df["acao"] = _num(df["acao"], ptbr).fillna(0).astype("int64") if "acao" in df else 0
    for c in ("comprador", "vendedor"):
        df[c] = _num(df[c], ptbr).fillna(-1).astype("int64")
    if "id_negocio" not in df:
        df["id_negocio"] = np.arange(len(df)).astype(str)
    df["id_negocio"] = df["id_negocio"].astype(str).str.strip()
    canc = df.loc[df["acao"] == 2]
    if len(canc):   # remove o cancelamento e o negócio original
        chave = df["ticker"] + "|" + df["id_negocio"]
        df = df[~chave.isin(set(canc["ticker"] + "|" + canc["id_negocio"]))].copy()
    if "data" in df:
        df["data"] = _data(df["data"])
    elif "data_negocio" in df:
        df["data"] = _data(df["data_negocio"])
    df = df.dropna(subset=["preco", "qtd", "hora_ms"])
    df = df[df["qtd"] > 0].copy()
    df["hora_ms"] = df["hora_ms"].astype("int64")
    return df.reset_index(drop=True)


def _pos_posicoes(df: pd.DataFrame, ptbr: bool | None = None) -> pd.DataFrame:
    df = df.dropna(subset=["ticker"]).copy()
    for c in ("oi", "var_oi", "coberta", "travada", "descoberta", "total", "n_clientes_1", "n_clientes_2"):
        if c in df:
            df[c] = _num(df[c], ptbr)
    if "oi" not in df or df["oi"].isna().all():
        df["oi"] = df["total"] if "total" in df else np.nan
    if "total" in df:
        df["oi"] = df["oi"].fillna(df["total"])
    if "data" in df:
        df["data"] = _data(df["data"])
    return df.drop_duplicates("ticker", keep="last").reset_index(drop=True)


def _pos_cadastro(df: pd.DataFrame, ptbr: bool | None = None) -> pd.DataFrame:
    df = df.dropna(subset=["ticker"]).copy()
    for c in ("strike", "multiplicador"):
        if c in df:
            df[c] = _num(df[c], ptbr)
    if "vencimento" in df:
        df["vencimento"] = _data(df["vencimento"])
    for c in ("ativo", "subjacente"):
        if c in df:
            df[c] = _txt(df[c])
    tipo = pd.Series(None, index=df.index, dtype=object)
    for c in ("tipo_opcao", "segmento", "mercado", "categoria"):
        if c in df:
            tipo = tipo.where(tipo.notna(), df[c].map(tipo_opcao_de))
    tipo = tipo.where(tipo.notna(), df["ticker"].map(_tipo_pelo_ticker))
    eh_opcao = df["strike"].notna() if "strike" in df else pd.Series(False, index=df.index)
    df["tipo"] = tipo.where(eh_opcao)
    if "estilo" in df:
        e = df["estilo"].map(normalizar)
        df["estilo"] = np.where(e.str.startswith("am"), "americana", np.where(e.str.startswith("eu"), "europeia", None))
    return df.drop_duplicates("ticker", keep="last").reset_index(drop=True)


def _pos_consolidado(df: pd.DataFrame, ptbr: bool | None = None) -> pd.DataFrame:
    df = df.dropna(subset=["ticker"]).copy()
    for c in ("preco_min", "preco_max", "preco_medio", "preco_ult", "osc", "n_negocios", "qtd", "volume"):
        if c in df:
            df[c] = _num(df[c], ptbr)
    if "data" in df:
        df["data"] = _data(df["data"])
    if "volume" in df:   # se vierem pregão regular e after juntos, fica o de maior volume
        df = df.sort_values("volume", ascending=False)
    return df.drop_duplicates("ticker").reset_index(drop=True)


_POS = {"negocios": _pos_negocios, "posicoes": _pos_posicoes,
        "cadastro": _pos_cadastro, "consolidado": _pos_consolidado}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def ler_tabela(path, tipo: str | None = None, overrides: dict | None = None,
               filtro_tickers=None, chunksize: int = 1_000_000, acoes=None,
               min_notional_acoes: float | None = None) -> pd.DataFrame:
    """Lê um arquivo e devolve colunas canônicas.

    No negócio a negócio, filtro_tickers (opções) e acoes reduzem memória: negócios em `acoes`
    só são mantidos se o notional for >= min_notional_acoes (pernas de estruturas com ação).
    """
    info = identificar_tipo(path, overrides)
    tipo = tipo or info["tipo"]
    if tipo not in _POS:
        raise ValueError(f"{Path(path).name}: tipo não reconhecido. Mapeadas: {info['mapeadas']}")
    enc, sep, hdr = info["encoding"], info["separador"], info["linha_cabecalho"]
    mapa = mapear_colunas(_colunas(path, enc, sep, hdr), tipo, (overrides or {}).get(tipo))
    filtro = set(filtro_tickers) if filtro_tickers is not None else None
    acoes = set(acoes) if acoes is not None else None
    partes = []
    leitor = pd.read_csv(path, sep=sep, skiprows=hdr, header=0, usecols=list(mapa), dtype=str,
                         encoding=enc, chunksize=chunksize, on_bad_lines="skip")
    for ch in leitor:
        ch = ch.rename(columns=mapa)
        ch["ticker"] = ch["ticker"].astype(str).str.strip().str.upper()
        if filtro is not None or acoes is not None:
            manter = ch["ticker"].isin(filtro) if filtro is not None else pd.Series(False, index=ch.index)
            if acoes is not None:
                eh_acao = ch["ticker"].isin(acoes)
                if min_notional_acoes and "preco" in ch and "qtd" in ch:
                    pt = _convencao_ptbr(ch, sep, ("preco", "qtd"))
                    eh_acao &= (_num(ch["preco"], pt) * _num(ch["qtd"], pt)) >= min_notional_acoes
                manter |= eh_acao
            ch = ch[manter]
        if len(ch):
            partes.append(ch)
    df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=list(mapa.values()))
    numericas = [c for c in df.columns if c not in ("ticker", "data", "data_negocio", "vencimento", "hora", "id_negocio",
                                                   "ativo", "subjacente", "tipo_opcao", "estilo", "categoria",
                                                   "segmento", "mercado", "sessao")]
    return _POS[tipo](df, _convencao_ptbr(df, sep, numericas))


def _descompactar(pasta: Path):
    for z in pasta.glob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            for nome in zf.namelist():
                destino = pasta / Path(nome).name
                if nome.lower().endswith((".csv", ".txt")) and not destino.exists():
                    destino.write_bytes(zf.read(nome))


def inventariar(pasta, overrides: dict | None = None) -> pd.DataFrame:
    """Lista os arquivos da pasta com o tipo detectado e o mapeamento de colunas."""
    pasta = Path(pasta)
    _descompactar(pasta)
    linhas = []
    for arq in sorted(p for p in pasta.iterdir() if p.suffix.lower() in (".csv", ".txt")):
        try:
            linhas.append(identificar_tipo(arq, overrides))
        except Exception as e:   # noqa: BLE001
            linhas.append({"arquivo": arq.name, "tipo": "erro", "tipo_provavel": "?", "faltando": [], "cobertura": 0.0,
                           "mapeadas": {}, "ignoradas": [str(e)]})
    return pd.DataFrame(linhas)


def ler_pasta(pasta, overrides: dict | None = None, tipos=TIPOS, filtro_negocios=None,
              min_notional_acoes: float | None = None) -> tuple[dict, pd.DataFrame]:
    """Lê todos os arquivos reconhecidos da pasta. Arquivos do mesmo tipo são concatenados.

    O negócio a negócio é filtrado para as opções do cadastro e, das ações-objeto (mesma raiz),
    só os negócios com notional >= min_notional_acoes. Ou para filtro_negocios, se informado.
    """
    inv = inventariar(pasta, overrides)
    tabelas: dict[str, pd.DataFrame] = {}
    for tipo in ("cadastro", "consolidado", "posicoes", "negocios"):
        if tipo not in tipos:
            continue
        arqs = inv.loc[inv["tipo"] == tipo, "arquivo"].tolist()
        if not arqs:
            continue
        filtro, acoes = None, None
        if tipo == "negocios":
            filtro = filtro_negocios
            if filtro is None and "cadastro" in tabelas:
                ops = tabelas["cadastro"]
                ops = ops[ops["tipo"].isin(["C", "P"])]
                filtro = set(ops["ticker"])
                raiz = ops["ativo"] if "ativo" in ops else ops["ticker"]
                raizes = set(raiz.dropna().astype(str).str[:4])
                if "consolidado" in tabelas:
                    t = tabelas["consolidado"]["ticker"]
                    t = t[t.str.match(r"^[A-Z]{4}(?:3|4|5|6|11)$", na=False)]
                    acoes = set(t[t.str[:4].isin(raizes)])
        partes = [ler_tabela(Path(pasta) / a, tipo, overrides, filtro, acoes=acoes,
                             min_notional_acoes=min_notional_acoes) for a in arqs]
        tabelas[tipo] = pd.concat(partes, ignore_index=True) if len(partes) > 1 else partes[0]
    return tabelas, inv
