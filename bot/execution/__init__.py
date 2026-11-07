"""Capa de ejecución de Phoenix: ciclo de vida de trades (dry-run y live).

Esta capa reintroduce ejecución de forma acotada y testeable, manteniendo la
separación entre la lógica pura (state machine) y el broker (dry-run ahora,
live en una fase posterior). No contiene API keys ni lógica de red por sí misma.
"""
