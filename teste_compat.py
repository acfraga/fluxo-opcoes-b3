"""Regressão: os 13 eventos plantados no pregão sintético precisam aparecer (warnings tratados como erro)."""
import sys, tempfile, warnings
warnings.simplefilter("error")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="openpyxl")
import pandas as pd
from fluxo_opcoes import processar_dia, exportar_excel, Parametros, __version__
from fluxo_opcoes.sintetico import gerar

def rodar(curva=None):
    g = gerar(tempfile.mkdtemp())
    res = processar_dia(g["pasta_dia"], g["pasta_anterior"], params=Parametros(), corretoras=g["corretoras"], curva_di=curva)
    ev = res.eventos
    col_tipo = next(c for c in ("tipo", "tipo_evento") if c in ev)
    faltam = [e for e in g["esperado"]
              if not ((ev[col_tipo] == e[0]) & (ev["subjacente"] == e[1]) & (ev["corretora"] == e[2]) & (ev["estrutura"] == e[3])).any()]
    exportar_excel(res, tempfile.mktemp(suffix=".xlsx"), texto_br=True)
    return len(g["esperado"]) - len(faltam), len(g["esperado"]), faltam

print(f"fluxo_opcoes {__version__} | pandas {pd.__version__}")
ok = True
for nome, curva in (("sem curva", None), ("com curva DI1", {12: 0.149, 31: 0.1478, 52: 0.1465, 115: 0.1432, 241: 0.1395})):
    n, tot, faltam = rodar(curva)
    print(f"  {nome}: {n}/{tot} eventos esperados encontrados", "" if not faltam else f"— faltam {faltam}")
    ok &= not faltam
sys.exit(0 if ok else 1)
