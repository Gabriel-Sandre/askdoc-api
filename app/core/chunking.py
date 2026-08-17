"""Divisão de texto em chunks com offsets preservados.

O detalhe que importa: cada chunk carrega `char_start`/`char_end` do documento
original. É isso que permite a resposta citar *exatamente* de onde a informação
veio, e o front-end destacar o trecho no documento — em vez de devolver um
"segundo o documento X" genérico e não verificável.

Estratégia: cortes preferencialmente em fronteira de parágrafo, depois de
sentença, depois de palavra, e só em último caso no meio de uma palavra.
"""

import re
from dataclasses import dataclass

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"[.!?…](?=\s)")
_WHITESPACE = re.compile(r"\s")


@dataclass(frozen=True, slots=True)
class TextChunk:
    ordinal: int
    text: str
    char_start: int
    char_end: int

    @property
    def length(self) -> int:
        return self.char_end - self.char_start


def normalize(text: str) -> str:
    """Normaliza quebras de linha e espaços em branco redundantes.

    Feito ANTES do chunking para que os offsets se refiram sempre ao texto
    normalizado, que é o mesmo que guardamos no banco.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_break(text: str, start: int, hard_end: int, soft_floor: int) -> int:
    """Melhor ponto de corte dentro de [soft_floor, hard_end]."""
    window = text[start:hard_end]
    offset_floor = soft_floor - start

    for pattern in (_PARAGRAPH_BREAK, _SENTENCE_END):
        best = -1
        for match in pattern.finditer(window):
            if match.end() >= offset_floor:
                best = match.end()
                break
            best = match.end()
        if best >= offset_floor:
            return start + best

    space = window.rfind(" ")
    if space >= offset_floor:
        return start + space + 1

    return hard_end  # nenhuma fronteira decente: corta no limite duro


def chunk_text(
    text: str, *, chunk_size: int = 900, overlap: int = 150
) -> list[TextChunk]:
    """Quebra `text` em pedaços de ~`chunk_size` caracteres com sobreposição.

    A sobreposição existe para não perder uma resposta que caia exatamente
    em cima de uma fronteira de chunk.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size precisa ser positivo")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap precisa estar em [0, chunk_size)")

    text = normalize(text)
    if not text:
        return []

    chunks: list[TextChunk] = []
    start = 0
    ordinal = 0
    total = len(text)

    while start < total:
        hard_end = min(start + chunk_size, total)
        if hard_end == total:
            end = total
        else:
            # aceita cortar a partir de 60% do tamanho alvo
            end = _find_break(text, start, hard_end, start + int(chunk_size * 0.6))

        body = text[start:end].strip()
        if body:
            leading = len(text[start:end]) - len(text[start:end].lstrip())
            trailing = len(text[start:end]) - len(text[start:end].rstrip())
            chunks.append(
                TextChunk(
                    ordinal=ordinal,
                    text=body,
                    char_start=start + leading,
                    char_end=end - trailing,
                )
            )
            ordinal += 1

        if end >= total:
            break

        next_start = end - overlap
        # garante progresso mesmo em casos patológicos
        start = next_start if next_start > start else end

    return chunks


def estimate_tokens(text: str) -> int:
    """Aproximação grosseira (~4 caracteres por token) para orçamento de contexto.

    Suficiente para decidir quantos chunks cabem no prompt sem trazer um
    tokenizer inteiro como dependência.
    """
    return max(1, len(text) // 4)
