"""OpenModelica-specific infrastructure adapters."""

from farcel.infrastructure.openmodelica.compiler import OpenModelicaCompiler
from farcel.infrastructure.openmodelica.environment import OpenModelicaEnvironmentDetector

__all__ = ["OpenModelicaCompiler", "OpenModelicaEnvironmentDetector"]
