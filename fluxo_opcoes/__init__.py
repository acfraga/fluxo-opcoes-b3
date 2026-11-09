"""Protótipo de leitura de fluxo e posições em opções de ações da B3.

Uso típico:
    from fluxo_opcoes import Parametros, processar_dia, exportar_excel
    res = processar_dia("dados/2026-11-16", pasta_anterior="dados/2026-11-13")
    exportar_excel(res, "painel_2026-11-16.xlsx")
"""
from .config import CORRETORAS, MAPA_SUBJACENTE, Parametros
from .leitura import identificar_tipo, inventariar, ler_pasta, ler_tabela
from .painel import exportar_excel
from .pipeline import ResultadoDia, processar_dia

__version__ = "0.8.1"
__all__ = ["Parametros", "CORRETORAS", "MAPA_SUBJACENTE", "processar_dia", "ResultadoDia", "exportar_excel",
           "inventariar", "identificar_tipo", "ler_pasta", "ler_tabela"]
