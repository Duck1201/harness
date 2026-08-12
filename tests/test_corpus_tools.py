import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from test_corpus_store import DIMENSIONS, HashingEmbedder, WordCounter

from harness.application_service import CorpusTurnRetrieval
from harness.config import load_config
from harness.conversation_store import ConversationStore
from harness.corpus_ingestion import build_document, embeddable_texts, extract, source_digest
from harness.corpus_service import (
    CITATION_INSTRUCTION,
    NOTHING_FOUND_INSTRUCTION,
    CorpusLibrary,
    CorpusRetriever,
)
from harness.corpus_tools import CorpusToolExecutor, granted_corpus_id
from harness.domain import Grant, SessionPolicy, ToolCall, ToolResultStatus
from harness.observability_store import ObservabilityStore
from harness.ports import ModelRequest, ModelResponse

CONVERSATION = "conversation-1"


class FakeEmbedder:
    model = "fake"
    dimensions = DIMENSIONS

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return HashingEmbedder().embed(texts)


def _policy(*permissions: tuple[str, str]) -> SessionPolicy:
    return SessionPolicy(
        conversation_id=CONVERSATION,
        grants=tuple(
            Grant(
                id=f"grant-{index}",
                conversation_id=CONVERSATION,
                permission=permission,
                scope=scope,
                granted_at=datetime.now(UTC),
            )
            for index, (permission, scope) in enumerate(permissions)
        ),
    )


def _library(tmp_path: Path) -> CorpusLibrary:
    return CorpusLibrary(
        tmp_path / "corpora",
        embedding_model="fake",
        embedding_dimensions=DIMENSIONS,
    )


async def _fill(library: CorpusLibrary, *, filename: str, data: bytes, origin: str) -> str:
    corpus = await library.create(name="Manual do jogo")
    draft = build_document(
        extract(filename, data),
        origin_kind=origin,
        origin_ref=filename,
        source_digest=source_digest(data),
        counter=WordCounter(),
        chunk_tokens=40,
        overlap_tokens=8,
    )
    embeddings = await FakeEmbedder().embed(embeddable_texts(draft))
    await library.store(corpus.id).add_document(draft, embeddings)
    return corpus.id


def _executor(library: CorpusLibrary, policy: SessionPolicy) -> CorpusToolExecutor:
    config = load_config()
    # O piso de 0,5 do contrato foi medido no bge-m3 e só significa alguma coisa
    # nele. O embedder falso tem outra escala, então o teste declara a sua: o que
    # está sendo verificado é que existe piso e que ele zera o resultado.
    corpus = config.corpus.model_copy(
        update={
            "retrieval": config.corpus.retrieval.model_copy(update={"dense_similarity_floor": 0.3})
        }
    )
    return CorpusToolExecutor(
        registry=config.tool_registry,
        session_policy=policy,
        retriever=CorpusRetriever(
            library=library,
            embedder=FakeEmbedder(),
            counter=WordCounter(),
            config=corpus,
        ),
    )


def _call(query: str) -> ToolCall:
    return ToolCall(id="call-1", name="corpus_search", arguments={"query": query})


def test_a_search_without_the_grant_is_blocked_by_the_harness(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = _library(tmp_path)
        await _fill(
            library,
            filename="manual.md",
            data=b"# Manual\n\n## Chefe\n\nO chefe final tem 320 pontos de vida.\n",
            origin="upload",
        )
        executor = _executor(library, _policy())

        preflight = await executor.preflight([_call("chefe final")])
        result = await executor.execute(_call("chefe final"))

        assert not preflight.allowed
        assert preflight.reason_code == "corpus_grant_required"
        assert result.status is ToolResultStatus.BLOCKED
        assert result.meta["producer"] == "harness"
        assert result.data is None

    asyncio.run(scenario())


def test_the_grant_scope_says_which_corpus_is_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = _library(tmp_path)
        corpus_id = await _fill(
            library,
            filename="manual.md",
            data=b"# Manual\n\n## Chefe\n\nO chefe final tem 320 pontos de vida.\n",
            origin="upload",
        )
        policy = _policy(("CorpusGrant", corpus_id))
        assert granted_corpus_id(policy) == corpus_id

        result = await _executor(library, policy).execute(_call("chefe final vida"))

        assert result.status is ToolResultStatus.SUCCESS
        payload = result.data
        assert isinstance(payload, dict)
        passages = payload["passages"]
        assert isinstance(passages, list) and passages
        first = passages[0]
        assert isinstance(first, dict)
        assert first["marker"] == 1
        assert "320" in str(first["text"])
        assert first["untrusted"] is False
        assert payload["instruction"] == CITATION_INSTRUCTION
        assert result.meta["taints"] == []

    asyncio.run(scenario())


def test_a_passage_from_the_scraper_declares_its_taint_on_the_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = _library(tmp_path)
        corpus_id = await _fill(
            library,
            filename="pagina.html",
            data=b"<html><head><title>Wiki</title></head><body><h2>Chefe</h2>"
            b"<p>O chefe final tem 320 pontos de vida e resiste a fogo.</p></body></html>",
            origin="scrape",
        )

        result = await _executor(library, _policy(("CorpusGrant", corpus_id))).execute(
            _call("chefe final vida")
        )

        assert result.status is ToolResultStatus.SUCCESS
        # É isto que faz a policy existente exigir confirmação de data_egress no
        # resto do Turn: o taint viaja no ToolResult, sem código de policy novo.
        assert result.meta["taints"] == ["UntrustedWebTaint"]
        payload = result.data
        assert isinstance(payload, dict)
        passages = payload["passages"]
        assert isinstance(passages, list)
        assert isinstance(passages[0], dict) and passages[0]["untrusted"] is True

    asyncio.run(scenario())


def test_nothing_above_the_floor_is_empty_and_says_so(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = _library(tmp_path)
        corpus_id = await _fill(
            library,
            filename="manual.md",
            data=b"# Manual\n\n## Chefe\n\nO chefe final tem 320 pontos de vida.\n",
            origin="upload",
        )

        result = await _executor(library, _policy(("CorpusGrant", corpus_id))).execute(
            _call("receita de bolo de cenoura com cobertura")
        )

        assert result.status is ToolResultStatus.EMPTY
        payload = result.data
        assert isinstance(payload, dict)
        assert payload["passages"] == []
        assert payload["instruction"] == NOTHING_FOUND_INSTRUCTION

    asyncio.run(scenario())


def test_a_revoked_grant_stops_naming_a_corpus() -> None:
    async def scenario() -> None:
        expired = SessionPolicy(
            conversation_id=CONVERSATION,
            grants=(
                Grant(
                    id="grant-old",
                    conversation_id=CONVERSATION,
                    permission="CorpusGrant",
                    scope="manual",
                    granted_at=datetime(2020, 1, 1, tzinfo=UTC),
                    expires_at=datetime(2020, 1, 2, tzinfo=UTC),
                ),
            ),
        )

        assert granted_corpus_id(expired) is None

    asyncio.run(scenario())


def test_deleting_a_corpus_deletes_its_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        library = _library(tmp_path)
        corpus_id = await _fill(
            library,
            filename="nota.txt",
            data=b"Uma anotacao qualquer com tamanho suficiente.\n",
            origin="upload",
        )
        path = library.store(corpus_id).path
        assert path.is_file()

        await library.delete(corpus_id)

        assert not path.exists()
        assert await library.list() == ()

    asyncio.run(scenario())


def test_a_rewrite_that_came_back_in_portuguese_is_dropped(tmp_path: Path) -> None:
    """Sem reescrita em inglês, sem perna lexical.

    Um 4B pedido para traduzir às vezes devolve a pergunta como veio. Entregar
    isso ao BM25 contra um acervo em inglês mede pior que não buscar lexicalmente
    — medido: uma palavra em comum carrega o casamento e derruba a perna densa,
    que já tinha acertado.
    """

    async def scenario() -> None:
        library = _library(tmp_path)
        corpus_id = await _fill(
            library,
            filename="manual.md",
            data=b"# Manual\n\n## Rede\n\nThe proxy listens on port 8899.\n",
            origin="upload",
        )
        store = ConversationStore(tmp_path / "conversations.sqlite3")
        await store.initialize()
        workspace = await store.create_workspace(str(tmp_path))
        conversation = await store.create_conversation(workspace.workspace_id)
        await store.grant(conversation.id, "CorpusGrant", corpus_id)

        config = load_config()
        retriever = CorpusRetriever(
            library=library,
            embedder=FakeEmbedder(),
            counter=WordCounter(),
            config=config.corpus.model_copy(
                update={
                    "retrieval": config.corpus.retrieval.model_copy(
                        update={"dense_similarity_floor": 0.0}
                    )
                }
            ),
        )
        observability = ObservabilityStore(tmp_path / "observability.sqlite3")
        await observability.initialize()

        def retrieval(answer: str) -> CorpusTurnRetrieval:
            return CorpusTurnRetrieval(
                store=store,
                retriever=retriever,
                runtime=_EchoRuntime(answer),
                observability_store=observability,
            )

        echoed = await retrieval("Em que porta o proxy escuta?").for_turn(
            conversation.id, "Em que porta o proxy escuta?"
        )
        translated = await retrieval("which port does the proxy listen on").for_turn(
            conversation.id, "Em que porta o proxy escuta?"
        )

        assert echoed is not None and echoed["search_query"] is None
        assert translated is not None
        assert translated["search_query"] == "which port does the proxy listen on"

    asyncio.run(scenario())


class _EchoRuntime:
    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content=self._answer)
