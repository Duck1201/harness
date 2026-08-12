"""Turns a file or a page into Chunks, without ever rewriting what it read.

Three stages, the same three for an upload and for the scraper: extract, clean,
chunk. Cleaning is deterministic on purpose — the feature exists because a 4B
model invents facts, and a cleaning stage that paraphrases would be the same
invention, made permanent inside the index and invisible at answer time.

What a Chunk adds to the text is an address, not a summary: the Document title,
the heading path it sits under and the page it came from. That prefix is what
gets embedded and indexed; the passage that gets quoted is the stored text,
byte for byte.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO

from .corpus_store import ChunkDraft, DocumentDraft
from .domain import UNTRUSTED_WEB_TAINT
from .ports import TextTokenCounter
from .web_tools import SUPPRESSED_HTML_TAGS, without_link_menus

ACCEPTED_EXTENSIONS = (".txt", ".md", ".html", ".pdf")

_BLOCK_HTML_TAGS = frozenset(
    {
        "p", "div", "section", "article", "li", "tr", "td", "th", "br",
        "pre", "blockquote", "dd", "dt", "figcaption", "caption",
    }
)  # fmt: skip
_HEADING_HTML_TAGS = {f"h{level}": level for level in range(1, 7)}
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
# "recupera-\nção": hífen de quebra de linha, não hífen de palavra composta. Só
# se a próxima linha começa em minúscula, senão "Vice-\nPresidente" perderia o seu.
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-\n([a-zà-öø-ÿ])")
_PAGE_FURNITURE = re.compile(
    r"^(p[áa]g(ina)?\.?\s*)?\d{1,4}(\s*/\s*\d{1,4})?$|^page\s+\d{1,4}(\s+of\s+\d{1,4})?$",
    re.IGNORECASE,
)
# Um bloco com pouca letra é sopa de navegação, base64 ou moldura de tabela. O piso
# é baixo de propósito: "HP: 320" numa wiki é fato, não ruído.
_MINIMUM_LETTER_RATIO = 0.25
_MINIMUM_BLOCK_CHARACTERS = 3
# Um cabeçalho ou rodapé de PDF se repete em quase toda página; três páginas é o
# mínimo para a repetição significar alguma coisa.
_FURNITURE_PAGE_FLOOR = 3
_FURNITURE_SHARE = 0.6


class UnsupportedSourceError(Exception):
    """The source is not something the harness knows how to read as text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceBlock:
    text: str
    heading_path: tuple[str, ...]
    page: int | None


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    title: str
    blocks: tuple[SourceBlock, ...]


def accepted_extension(filename: str) -> str | None:
    lowered = filename.lower()
    return next((suffix for suffix in ACCEPTED_EXTENSIONS if lowered.endswith(suffix)), None)


def extract(filename: str, data: bytes) -> ExtractedDocument:
    """Reads a supported file into blocks, or refuses it by name.

    The refusal names the accepted formats because the Operator is the one who
    picked the file, and "unsupported" without a list is a dead end.
    """
    suffix = accepted_extension(filename)
    if suffix is None:
        accepted = ", ".join(ACCEPTED_EXTENSIONS)
        raise UnsupportedSourceError(
            "unsupported_extension",
            f"O harness só extrai texto de {accepted}. Converta o arquivo antes de subir.",
        )
    if suffix == ".pdf":
        return extract_pdf(filename, data)
    text = _decoded(data)
    if suffix == ".html":
        return extract_html(text, title_fallback=_stem(filename))
    if suffix == ".md":
        return extract_markdown(text, title_fallback=_stem(filename))
    return extract_plain(text, title=_stem(filename))


def extract_plain(text: str, *, title: str) -> ExtractedDocument:
    blocks = tuple(
        SourceBlock(text=paragraph, heading_path=(), page=None)
        for paragraph in _paragraphs(_reflowed(_normalized(text)))
    )
    return ExtractedDocument(title=title, blocks=blocks)


def extract_markdown(text: str, *, title_fallback: str) -> ExtractedDocument:
    normalized = _normalized(text)
    title = title_fallback
    heading_path: list[str] = []
    blocks: list[SourceBlock] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraph = _reflowed("\n".join(buffer))
            for piece in _paragraphs(paragraph):
                blocks.append(SourceBlock(text=piece, heading_path=tuple(heading_path), page=None))
            buffer.clear()

    for line in normalized.split("\n"):
        heading = _MARKDOWN_HEADING.match(line)
        if heading is None:
            buffer.append(line)
            continue
        flush()
        level = len(heading.group(1))
        text_of_heading = heading.group(2).strip()
        if level == 1 and title == title_fallback:
            title = text_of_heading
        del heading_path[level - 1 :]
        heading_path.append(text_of_heading)
    flush()
    return ExtractedDocument(title=title, blocks=tuple(blocks))


def extract_html(html: str, *, title_fallback: str) -> ExtractedDocument:
    parser = _BlockHTMLParser()
    parser.feed(html)
    parser.close()
    return ExtractedDocument(
        title=parser.title or title_fallback,
        blocks=tuple(parser.blocks),
    )


def extract_pdf(filename: str, data: bytes) -> ExtractedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, OSError) as error:
        raise UnsupportedSourceError(
            "pdf_unreadable",
            "Não foi possível ler este PDF: o arquivo parece corrompido ou protegido.",
        ) from error
    if not any(page.strip() for page in pages):
        raise UnsupportedSourceError(
            "pdf_without_text_layer",
            "Este PDF não tem camada de texto — provavelmente é digitalizado. "
            "O harness não faz OCR; passe o arquivo por um OCR antes de subir.",
        )
    cleaned_pages = [_normalized(page) for page in pages]
    furniture = _repeated_page_furniture(cleaned_pages)
    title = _pdf_title(reader) or _stem(filename)
    blocks: list[SourceBlock] = []
    for number, page in enumerate(cleaned_pages, start=1):
        kept = [
            stripped
            for line in page.split("\n")
            if (stripped := line.strip())
            and stripped not in furniture
            and not _PAGE_FURNITURE.match(stripped)
        ]
        for paragraph in _paragraphs(_reflowed("\n".join(kept))):
            blocks.append(SourceBlock(text=paragraph, heading_path=(), page=number))
    return ExtractedDocument(title=title, blocks=tuple(blocks))


def build_document(
    extracted: ExtractedDocument,
    *,
    origin_kind: str,
    origin_ref: str,
    source_digest: str,
    counter: TextTokenCounter,
    chunk_tokens: int = 512,
    overlap_tokens: int = 64,
) -> DocumentDraft:
    """Cleans the blocks and cuts them into Chunks, offsets included.

    A Chunk never starts in the middle of a paragraph: blocks accumulate until
    the budget is spent, and the overlap is the trailing blocks of the previous
    Chunk, re-included by range instead of copied.
    """
    blocks = _useful_blocks(extracted.blocks)
    text, spans = _joined(blocks)
    chunks: list[ChunkDraft] = []
    index = 0
    while index < len(blocks):
        used = 0
        end = index
        # Um Chunk pertence a uma seção só: o endereço que ele carrega é o da
        # primeira linha, e atravessar um heading faria a citação apontar para o
        # lugar errado exatamente na metade que interessa conferir.
        section = blocks[index].heading_path
        while end < len(blocks) and blocks[end].heading_path == section:
            block_tokens = counter.count_text(blocks[end].text)
            if used and used + block_tokens > chunk_tokens:
                break
            used += block_tokens
            end += 1
        if end == index:  # um bloco sozinho maior que o orçamento ainda vira Chunk
            end = index + 1
            used = counter.count_text(blocks[index].text)
        start_offset = spans[index][0]
        end_offset = spans[end - 1][1]
        chunks.append(
            ChunkDraft(
                start_offset=start_offset,
                end_offset=end_offset,
                context_prefix=_context_prefix(extracted.title, blocks[index]),
                page=blocks[index].page,
                token_count=used,
            )
        )
        index = _next_index(blocks, index, end, counter, overlap_tokens)
    taints = (UNTRUSTED_WEB_TAINT,) if origin_kind == "scrape" else ()
    return DocumentDraft(
        origin_kind=origin_kind,
        origin_ref=origin_ref,
        title=extracted.title,
        text=text,
        source_digest=source_digest,
        taints=taints,
        chunks=tuple(chunks),
    )


def embeddable_texts(draft: DocumentDraft) -> tuple[str, ...]:
    """What actually gets embedded: the address plus the passage."""
    return tuple(
        f"{chunk.context_prefix}\n{draft.text[chunk.start_offset : chunk.end_offset]}"
        for chunk in draft.chunks
    )


def source_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _BlockHTMLParser(HTMLParser):
    """HTML into blocks, keeping the heading path the page already declares.

    The web_fetch extractor flattens a page into lines and inlines link targets,
    which is right for a model reading a page once and wrong for an index: the
    heading structure is exactly the address a citation needs, and `(https://…)`
    inside a passage is noise that gets embedded.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[SourceBlock] = []
        self.title: str = ""
        self._suppressed_depth = 0
        self._heading_level: int | None = None
        self._in_title = False
        self._heading_path: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.casefold()
        if self._suppressed_depth:
            if lowered in SUPPRESSED_HTML_TAGS:
                self._suppressed_depth += 1
            return
        if lowered in SUPPRESSED_HTML_TAGS:
            self._suppressed_depth = 1
            return
        if lowered == "title":
            self._in_title = True
            return
        if lowered in _HEADING_HTML_TAGS:
            self._flush()
            self._heading_level = _HEADING_HTML_TAGS[lowered]
            return
        if lowered in _BLOCK_HTML_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if self._suppressed_depth:
            if lowered in SUPPRESSED_HTML_TAGS:
                self._suppressed_depth -= 1
            return
        if lowered == "title":
            self._in_title = False
            self.title = self.title.strip()
            return
        if lowered in _HEADING_HTML_TAGS:
            self._close_heading(_HEADING_HTML_TAGS[lowered])
            return
        if lowered in _BLOCK_HTML_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        if self._in_title:
            self.title += data
            return
        self._buffer.append(data)

    def close(self) -> None:
        super().close()
        if self._heading_level is not None:
            self._close_heading(self._heading_level)
        self._flush()
        self._drop_link_menus()

    def _close_heading(self, level: int) -> None:
        heading = _collapsed("".join(self._buffer))
        self._buffer.clear()
        self._heading_level = None
        if not heading:
            return
        del self._heading_path[level - 1 :]
        self._heading_path.append(heading)
        if not self.title:
            self.title = heading

    def _flush(self) -> None:
        if self._heading_level is not None:
            return
        text = _collapsed("".join(self._buffer))
        self._buffer.clear()
        if text:
            self.blocks.append(
                SourceBlock(text=text, heading_path=tuple(self._heading_path), page=None)
            )

    def _drop_link_menus(self) -> None:
        # A heurística de densidade de links do web_fetch vale igual aqui: uma
        # sequência longa de linhas curtas sem pontuação é menu, não conteúdo.
        kept = set(without_link_menus(block.text for block in self.blocks))
        if len(kept) == len(self.blocks):
            return
        self.blocks = [block for block in self.blocks if block.text in kept]


def _next_index(
    blocks: Sequence[SourceBlock],
    start: int,
    end: int,
    counter: TextTokenCounter,
    overlap_tokens: int,
) -> int:
    """Where the next Chunk begins, once the overlap is paid.

    The overlap exists so the sentence that turns an argument is not cut in half
    by an accident of paragraph length. It never rewinds past the Chunk's own
    start, which would make the loop stand still.
    """
    if end >= len(blocks) or overlap_tokens <= 0:
        return end
    budget = overlap_tokens
    index = end
    while index > start + 1:
        tokens = counter.count_text(blocks[index - 1].text)
        if tokens > budget:
            break
        budget -= tokens
        index -= 1
    return index


def _useful_blocks(blocks: Sequence[SourceBlock]) -> tuple[SourceBlock, ...]:
    kept: list[SourceBlock] = []
    seen: set[str] = set()
    for block in blocks:
        text = block.text.strip()
        if len(text) < _MINIMUM_BLOCK_CHARACTERS or _letter_ratio(text) < _MINIMUM_LETTER_RATIO:
            continue
        key = f"{block.heading_path}\x00{text}"
        if key in seen:
            continue
        seen.add(key)
        kept.append(SourceBlock(text=text, heading_path=block.heading_path, page=block.page))
    return tuple(kept)


def _joined(blocks: Sequence[SourceBlock]) -> tuple[str, tuple[tuple[int, int], ...]]:
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    offset = 0
    for block in blocks:
        if parts:
            parts.append("\n\n")
            offset += 2
        parts.append(block.text)
        spans.append((offset, offset + len(block.text)))
        offset += len(block.text)
    return "".join(parts), tuple(spans)


def _context_prefix(title: str, block: SourceBlock) -> str:
    # O H1 de um documento costuma ser o próprio título: repeti-lo no endereço
    # gastaria contexto para dizer duas vezes a mesma coisa.
    path = [heading for heading in block.heading_path if heading != title]
    prefix = " > ".join(part for part in [title, *path] if part)
    if block.page is not None:
        return f"{prefix} — p. {block.page}"
    return prefix


def _repeated_page_furniture(pages: Sequence[str]) -> frozenset[str]:
    if len(pages) < _FURNITURE_PAGE_FLOOR:
        return frozenset()
    counts: dict[str, int] = {}
    for page in pages:
        lines = [line.strip() for line in page.split("\n") if line.strip()]
        for line in {*lines[:2], *lines[-2:]}:
            counts[line] = counts.get(line, 0) + 1
    threshold = max(_FURNITURE_PAGE_FLOOR - 1, int(len(pages) * _FURNITURE_SHARE))
    return frozenset(line for line, count in counts.items() if count >= threshold)


def _pdf_title(reader: object) -> str:
    metadata = getattr(reader, "metadata", None)
    title = getattr(metadata, "title", None)
    return str(title).strip() if isinstance(title, str) else ""


def _decoded(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalized(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = _CONTROL_CHARACTERS.sub("", normalized)
    normalized = normalized.replace("­", "")
    return "\n".join(_HORIZONTAL_SPACE.sub(" ", line).strip() for line in normalized.split("\n"))


def _reflowed(text: str) -> str:
    """Undoes hard wrapping so a paragraph is one line again."""
    joined = _LINE_BREAK_HYPHEN.sub(r"\1\2", text)
    lines = joined.split("\n")
    out: list[str] = []
    for line in lines:
        if line.strip() and out and out[-1].strip():
            out[-1] = f"{out[-1]} {line.strip()}"
            continue
        out.append(line)
    return "\n".join(out)


def _paragraphs(text: str) -> Iterable[str]:
    for piece in text.split("\n"):
        stripped = piece.strip()
        if stripped:
            yield stripped


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _letter_ratio(text: str) -> float:
    letters = sum(1 for character in text if character.isalpha())
    return letters / len(text) if text else 0.0


def _stem(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1]
    for suffix in ACCEPTED_EXTENSIONS:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


__all__ = [
    "ACCEPTED_EXTENSIONS",
    "ExtractedDocument",
    "SourceBlock",
    "UnsupportedSourceError",
    "accepted_extension",
    "build_document",
    "embeddable_texts",
    "extract",
    "extract_html",
    "extract_markdown",
    "extract_pdf",
    "extract_plain",
    "source_digest",
]
