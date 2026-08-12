import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_corpus_store import DIMENSIONS, HashingEmbedder, WordCounter

from harness.config import load_config
from harness.corpus_scraper import ScrapeError, ScrapePlan, Scraper
from harness.corpus_service import CorpusIngestionService, CorpusLibrary, IngestionJobStatus
from harness.web_tools import EgressGuard, HttpResponse, ResolvedAddress, ResolvedTarget

WIKI = "https://wiki.test/index.php/Inicio"
ENDPOINT = "https://wiki.test/api.php"


class FakeEmbedder:
    model = "fake"
    dimensions = DIMENSIONS

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return HashingEmbedder().embed(texts)


class FakeGuard(EgressGuard):
    """Resolve sem tocar em DNS, mantendo a fronteira que o guard representa."""

    async def resolve(self, url: str) -> ResolvedTarget:
        parsed = self.validate_url(url)
        host = parsed.hostname or ""
        return ResolvedTarget(
            url=url,
            hostname=host,
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            addresses=(ResolvedAddress(host="203.0.113.7", port=443, family=2, proto=6),),
        )


class FakeTransport:
    """Responde por chave, não por URL exata: a ordem dos parâmetros é do scraper."""

    def __init__(
        self,
        pages: Mapping[str, tuple[int, str, bytes]],
        *,
        timeouts: int = 0,
    ) -> None:
        self._pages = pages
        self._timeouts = timeouts
        self.requested: list[str] = []

    async def request(
        self,
        target: ResolvedTarget,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResponse:
        del headers, max_bytes, timeout_seconds
        self.requested.append(target.url)
        if self._timeouts > 0:
            self._timeouts -= 1
            # O que o aiohttp levanta quando a leitura estoura.
            raise TimeoutError
        status, content_type, body = self._pages.get(_key(target.url), (404, "text/plain", b""))
        return HttpResponse(status=status, headers={"Content-Type": content_type}, body=body)


def _key(url: str) -> str:
    """Identidade de uma requisição: o endereço mais o que a API está pedindo."""
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    for parameter in ("meta", "list", "prop"):
        if parameter in query:
            return f"{base}?{parameter}={query[parameter][0]}"
    return base


def _mediawiki_pages() -> dict[str, tuple[int, str, bytes]]:
    def payload(value: object) -> tuple[int, str, bytes]:
        return (200, "application/json", json.dumps(value).encode())

    return {
        "https://wiki.test/api.php?meta=siteinfo": payload(
            {"query": {"general": {"sitename": "Wiki"}}}
        ),
        "https://wiki.test/api.php?list=allpages": payload(
            {
                "query": {
                    "allpages": [
                        {"pageid": 1, "title": "Chefe Final"},
                        {"pageid": 2, "title": "Itens"},
                    ]
                }
            }
        ),
        "https://wiki.test/api.php?prop=extracts": payload(
            {
                "query": {
                    "pages": {
                        # Como a API responde de verdade: heading de wikitext e
                        # um parágrafo por linha, sem linha em branco entre eles.
                        "1": {
                            "extract": "O chefe final tem 320 pontos de vida e resiste a fogo.\n"
                            "\n\n== Fraquezas ==\nEle recua diante de gelo.\n"
                            "A segunda fase ignora veneno."
                        },
                        "2": {"extract": "A espada longa custa 500 moedas na loja da vila."},
                    }
                }
            }
        ),
    }


def _scraper(transport: FakeTransport) -> Scraper:
    async def no_sleep(seconds: float) -> None:
        del seconds

    return Scraper(
        config=load_config().corpus.scraper,
        egress_guard=FakeGuard(),
        transport=transport,
        sleep=no_sleep,
    )


def test_a_mediawiki_seed_is_collected_by_api_without_following_a_single_link() -> None:
    async def scenario() -> None:
        transport = FakeTransport(_mediawiki_pages())
        scraper = _scraper(transport)

        plan = await scraper.plan(WIKI)
        collected = [page async for page in scraper.collect(plan)]

        assert plan.source == "mediawiki"
        assert [page.title for page in collected] == ["Chefe Final", "Itens"]
        assert all(page.filename.endswith(".md") for page in collected)
        # O extrato vira Markdown de verdade: a seção da wiki é um heading, e
        # cada parágrafo fica separado. Sem isso o Chunk perde o endereço da
        # seção e a página inteira é remontada como um parágrafo só.
        assert collected[0].data.decode() == (
            "# Chefe Final\n\n"
            "O chefe final tem 320 pontos de vida e resiste a fogo.\n\n"
            "## Fraquezas\n\n"
            "Ele recua diante de gelo.\n\n"
            "A segunda fase ignora veneno.\n"
        )
        # Nenhuma requisição fora do api.php: a rota da wiki não navega por HTML.
        # As sondas de descoberta também são api.php, em prefixos diferentes.
        assert all(urlsplit(url).path.endswith("api.php") for url in transport.requested)

    asyncio.run(scenario())


def test_a_read_timeout_is_retried_instead_of_ending_the_collection() -> None:
    """Coletando por horas, um timeout é certo — e não pode custar o resto.

    Medido contra a wiki: `aiohttp` levanta `TimeoutError` puro, que passava
    direto pelo scraper e chegava ao job como falha genérica de ingestão. A
    coleta morria na página 23 e ainda dizia o motivo errado.
    """

    async def scenario() -> None:
        transport = FakeTransport(_mediawiki_pages(), timeouts=2)
        scraper = _scraper(transport)

        plan = ScrapePlan(seed=WIKI, source="mediawiki", detail=ENDPOINT)
        collected = [page async for page in scraper.collect(plan)]

        assert [page.title for page in collected] == ["Chefe Final", "Itens"]

    asyncio.run(scenario())


def test_a_wiki_that_stops_answering_fails_by_its_own_name() -> None:
    async def scenario() -> None:
        backoff = load_config().corpus.scraper.mediawiki.backoff_seconds
        transport = FakeTransport(_mediawiki_pages(), timeouts=len(backoff) + 1)
        scraper = _scraper(transport)

        plan = ScrapePlan(seed=WIKI, source="mediawiki", detail=ENDPOINT)
        with pytest.raises(ScrapeError) as error:
            _ = [page async for page in scraper.collect(plan)]

        # Insistir tem fim, e o fim diz o que aconteceu — não "erro de ingestão".
        assert error.value.code == "mediawiki_unreachable"

    asyncio.run(scenario())


def test_a_page_the_corpus_already_has_never_becomes_a_request() -> None:
    """A retomada vale pelo que ela não gasta.

    Pular depois de baixar já era idempotência; o que faz uma wiki maior que o
    teto caber em rodadas sucessivas é a página conhecida não virar requisição.
    """

    async def scenario() -> None:
        transport = FakeTransport(_mediawiki_pages())
        scraper = _scraper(transport)

        plan = await scraper.plan(WIKI)
        collected = [
            page
            async for page in scraper.collect(
                plan, frozenset({"https://wiki.test/index.php?curid=1"})
            )
        ]

        assert [page.title for page in collected] == ["Itens"]
        assert [page.url for page in collected] == ["https://wiki.test/index.php?curid=2"]
        requested = [
            parse_qs(urlsplit(url).query).get("pageids", []) for url in transport.requested
        ]
        assert ["1"] not in requested
        assert ["2"] in requested

    asyncio.run(scenario())


def test_a_site_without_the_api_falls_back_to_a_bounded_crawl() -> None:
    async def scenario() -> None:
        html = (
            b"<html><head><title>Home</title></head><body>"
            b"<p>Texto principal com tamanho suficiente para virar chunk.</p>"
            b'<a href="/segunda">Segunda</a>'
            b'<a href="https://outro.test/fora">Fora</a>'
            b"</body></html>"
        )
        second = b"<html><body><p>A segunda pagina tambem tem texto util aqui.</p></body></html>"
        transport = FakeTransport(
            {
                "https://site.test/": (200, "text/html", html),
                "https://site.test/segunda": (200, "text/html", second),
                "https://site.test/robots.txt": (404, "text/plain", b""),
                "https://site.test/sitemap.xml": (404, "text/plain", b""),
            }
        )
        scraper = _scraper(transport)

        plan = await scraper.plan("https://site.test/")
        collected = [page async for page in scraper.collect(plan)]

        assert plan.source == "html_crawl"
        assert [page.url for page in collected] == [
            "https://site.test/",
            "https://site.test/segunda",
        ]
        # O link para outro domínio nunca vira requisição.
        assert not any("outro.test" in url for url in transport.requested)

    asyncio.run(scenario())


def test_robots_txt_keeps_the_crawler_out_of_what_it_disallows() -> None:
    async def scenario() -> None:
        html = (
            b"<html><body><p>Pagina inicial com texto bastante para indexar.</p>"
            b'<a href="/privado/segredo">Privado</a></body></html>'
        )
        transport = FakeTransport(
            {
                "https://site.test/": (200, "text/html", html),
                "https://site.test/privado/segredo": (200, "text/html", b"<p>segredo</p>"),
                "https://site.test/robots.txt": (
                    200,
                    "text/plain",
                    b"User-agent: *\nDisallow: /privado\n",
                ),
                "https://site.test/sitemap.xml": (404, "text/plain", b""),
            }
        )
        scraper = _scraper(transport)

        collected = [
            page
            async for page in scraper.collect(
                ScrapePlan(seed="https://site.test/", source="html_crawl", detail="site.test")
            )
        ]

        assert [page.url for page in collected] == ["https://site.test/"]
        assert "https://site.test/privado/segredo" not in transport.requested

    asyncio.run(scenario())


def test_redispatching_a_finished_job_skips_what_is_already_indexed(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = CorpusLibrary(
            tmp_path / "corpora",
            embedding_model="fake",
            embedding_dimensions=DIMENSIONS,
        )
        corpus = await library.create(name="Wiki do jogo")
        transport = FakeTransport(_mediawiki_pages())
        service = CorpusIngestionService(
            library=library,
            embedder=FakeEmbedder(),
            counter=WordCounter(),
            config=load_config().corpus,
            scraper=_scraper(transport),
        )

        first = await service.start_scrape(corpus.id, WIKI)
        await _settle(service, first.id)
        after_first = len(transport.requested)
        second = await service.start_scrape(corpus.id, WIKI)
        await _settle(service, second.id)

        finished_first = service.job(first.id)
        finished_second = service.job(second.id)
        assert finished_first is not None and finished_second is not None
        assert finished_first.status is IngestionJobStatus.COMPLETED
        assert (finished_first.seen, finished_first.indexed, finished_first.skipped) == (2, 2, 0)
        # A segunda rodada não pula depois de baixar: ela não chega a ver a
        # página, porque o endereço conhecido sai antes de virar requisição.
        assert (finished_second.seen, finished_second.indexed, finished_second.skipped) == (0, 0, 0)
        assert (await library.read(corpus.id)).document_count == 2
        # O que a segunda rodada gastou foi a descoberta e a listagem, nunca um
        # extrato — é isso que faz uma wiki maior que o teto caber em rodadas.
        spent = transport.requested[after_first:]
        assert spent and not any("extracts" in url for url in spent)

    asyncio.run(scenario())


def test_an_upload_of_a_refused_type_fails_the_job_in_portuguese(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = CorpusLibrary(
            tmp_path / "corpora",
            embedding_model="fake",
            embedding_dimensions=DIMENSIONS,
        )
        corpus = await library.create(name="Manual")
        service = CorpusIngestionService(
            library=library,
            embedder=FakeEmbedder(),
            counter=WordCounter(),
            config=load_config().corpus,
        )

        job = await service.ingest_upload(corpus.id, "planilha.xlsx", b"conteudo")

        assert job.status is IngestionJobStatus.FAILED
        assert job.reason_code == "unsupported_extension"
        assert ".pdf" in str(job.detail)

    asyncio.run(scenario())


async def _settle(service: CorpusIngestionService, job_id: str) -> None:
    for _ in range(500):
        job = service.job(job_id)
        if job is not None and job.status in {
            IngestionJobStatus.COMPLETED,
            IngestionJobStatus.FAILED,
            IngestionJobStatus.CANCELED,
        }:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job de ingestão não terminou")
