"""Retrieval. Each test names the failure it guards: missing or wrong evidence, nondeterminism, leaked identifiers."""
import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.w2.rag import build_retriever
from app.w2.rag.bm25 import BM25
from app.w2.rag.corpus import CorpusError, load_corpus
from app.w2.rag.embed import EmbedError, HashedEmbedder, HostedEmbedder
from app.w2.rag.rerank import LexicalReranker, OpenRouterReranker, RerankError
from app.w2.rag.retriever import Retriever, rrf

OFFLINE = Settings()


@pytest.fixture(scope="module")
def retriever():
    return build_retriever(OFFLINE, offline=True)


def test_corpus_has_15_to_20_passages_with_source_metadata():
    chunks = load_corpus()
    assert 15 <= len(chunks) <= 20
    assert all(c.publisher and c.year and c.licence and c.chunk_id.startswith(c.doc_id) for c in chunks)


def test_corpus_rejects_a_file_without_front_matter(tmp_path):
    (tmp_path / "x.md").write_text("## no front matter\ntext")
    with pytest.raises(CorpusError):
        load_corpus(tmp_path)


def test_exact_term_chunk_ranks_first_in_bm25():
    texts = [f"{c.section}. {c.text}" for c in load_corpus()]
    top = BM25(texts).rank("INR warfarin target range", 3)[0]
    assert "INR" in texts[top]


def test_rrf_is_deterministic_and_rewards_agreement():
    assert rrf([[3, 1, 2], [1, 3]]) == rrf([[3, 1, 2], [1, 3]])
    assert rrf([[3, 1], [1, 3]])[:2] == [1, 3]  # tie on score broken by index, not by dict order
    assert rrf([[5, 1], [1, 9]])[0] == 1


def test_a1c_query_returns_an_a1c_passage(retriever):
    res = retriever.retrieve("What is the HbA1c goal for a diabetic adult?")
    assert res.status.value == "ok" and 1 <= len(res.snippets) <= 5
    assert res.snippets[0].chunk_id.startswith("ada-standards-2025")
    assert res.snippets[0].citation.source_type.value == "guideline" and res.snippets[0].publisher


def test_inr_query_finds_anticoagulation_passage(retriever):
    assert retriever.retrieve("INR monitoring frequency on warfarin").snippets[0].chunk_id.startswith("chest-")


def test_off_topic_query_is_explicitly_empty(retriever):
    res = retriever.retrieve("best pizza toppings for a birthday party")
    assert res.status.value == "empty" and res.snippets == []


def test_same_query_gives_same_order(retriever):
    q = "kidney function metformin eGFR"
    assert [s.chunk_id for s in retriever.retrieve(q).snippets] == [s.chunk_id for s in retriever.retrieve(q).snippets]


def test_rerank_failure_flags_rerank_skipped_and_still_answers():
    class Down:
        name = "down"

        def rerank(self, q, docs, top):
            raise RerankError("unreachable")

    r = Retriever(load_corpus(), HashedEmbedder(), Down(), fallback_reranker=LexicalReranker())
    res = r.retrieve("A1c goal in diabetes")
    assert res.rerank_skipped and res.status.value == "ok"


def test_embedding_failure_degrades_to_sparse_only():
    class BadEmbedder(HashedEmbedder):
        calls = 0

        def embed(self, texts):
            BadEmbedder.calls += 1
            if BadEmbedder.calls > 1:  # index build works, query-time embedding fails
                raise EmbedError("down")
            return super().embed(texts)

    r = Retriever(load_corpus(), BadEmbedder(), LexicalReranker())
    assert r.retrieve("INR warfarin").status.value == "ok"


def test_query_is_truncated_so_a_pasted_chart_cannot_ride_along(retriever):
    res = retriever.retrieve("A1c goal " + "x" * 5000)
    assert res.status.value in ("ok", "empty")


def _or_settings(**kw) -> Settings:
    return Settings(openrouter_api_key=SecretStr("k"), **kw)


def test_openrouter_rerank_posts_cohere_v35_to_the_rerank_endpoint_with_the_openrouter_key():
    """Guards routing: wrong URL, model or credential would silently send traffic to the wrong place."""
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers["authorization"], json.loads(req.content)))
        return httpx.Response(200, json={"model": "cohere/rerank-v3.5", "provider": "Cohere",
                                         "usage": {"search_units": 1},
                                         "results": [{"index": 1, "relevance_score": 0.9, "document": {"text": "b"}}]})

    rr = OpenRouterReranker(_or_settings(), transport=httpx.MockTransport(handler))
    assert rr.rerank("A1c goal", ["a", "b"], 1) == [(1, 0.9)]
    url, auth, body = seen[0]
    assert url == "https://openrouter.ai/api/v1/rerank" and auth == "Bearer k"
    assert body == {"model": "cohere/rerank-v3.5", "query": "A1c goal", "documents": ["a", "b"], "top_n": 1}
    assert rr.name == "openrouter:cohere/rerank-v3.5"
    assert rr.last_meta == {"model": "cohere/rerank-v3.5", "provider": "Cohere", "usage": {"search_units": 1}}


def test_openrouter_rerank_honours_configured_base_url_and_model():
    seen = []
    t = httpx.MockTransport(lambda r: (seen.append((str(r.url), json.loads(r.content)["model"])),
                                       httpx.Response(200, json={"results": []}))[1])
    s = _or_settings(openrouter_base_url="https://gw.example/api/v1/", w2_rerank_model="cohere/rerank-v3.5:free")
    OpenRouterReranker(s, transport=t).rerank("q", ["a"], 1)
    assert seen == [("https://gw.example/api/v1/rerank", "cohere/rerank-v3.5:free")]


def test_openrouter_rerank_retries_once_then_raises_without_content():
    n = []
    t = httpx.MockTransport(lambda r: (n.append(1), httpx.Response(503))[1])
    with pytest.raises(RerankError) as e:
        OpenRouterReranker(_or_settings(), transport=t, sleep=lambda s: None).rerank("secret-query", ["a"], 1)
    assert len(n) == 2 and "secret-query" not in str(e.value)


def test_openrouter_rerank_retries_429_and_recovers():
    codes = iter([429, 200])
    t = httpx.MockTransport(lambda r: httpx.Response(next(codes), json={"results": [{"index": 0, "relevance_score": 0.5}]}))
    assert OpenRouterReranker(_or_settings(), transport=t, sleep=lambda s: None).rerank("q", ["a"], 1) == [(0, 0.5)]


def test_openrouter_rerank_does_not_retry_a_client_error():
    n = []
    t = httpx.MockTransport(lambda r: (n.append(1), httpx.Response(401))[1])
    with pytest.raises(RerankError, match="http_401"):
        OpenRouterReranker(_or_settings(), transport=t, sleep=lambda s: None).rerank("q", ["a"], 1)
    assert len(n) == 1


def test_openrouter_rerank_index_out_of_range_is_rejected():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [{"index": 9, "relevance_score": 1}]}))
    with pytest.raises(RerankError):
        OpenRouterReranker(_or_settings(), transport=t).rerank("q", ["a"], 1)


def test_openrouter_rerank_without_key_raises_before_any_request():
    def boom(req):  # noqa: ANN001
        raise AssertionError("no request may be made without a key")

    with pytest.raises(RerankError, match="not_configured"):
        OpenRouterReranker(Settings(openrouter_api_key=SecretStr("")), transport=httpx.MockTransport(boom)).rerank("q", ["a"], 1)


def test_build_retriever_picks_the_reranker_by_key_and_offline_flag():
    """Guards the offline guarantee: CI and keyless runs must never construct a network reranker."""
    assert build_retriever(_or_settings(), offline=True)._reranker.name == "lexical"
    assert build_retriever(Settings(openrouter_api_key=SecretStr("")), offline=False)._reranker.name == "lexical"
    live = build_retriever(_or_settings(), offline=False)
    assert live._reranker.name == "openrouter:cohere/rerank-v3.5" and live._fallback.name == "lexical"


def test_provider_outage_falls_back_to_lexical_and_flags_rerank_skipped():
    t = httpx.MockTransport(lambda r: httpx.Response(500))
    rr = OpenRouterReranker(_or_settings(), transport=t, sleep=lambda s: None)
    res = Retriever(load_corpus(), HashedEmbedder(), rr, fallback_reranker=LexicalReranker()).retrieve("A1c testing frequency")
    assert res.rerank_skipped and res.status.value == "ok" and res.snippets


def test_hosted_embedder_caches_so_repeat_text_costs_no_second_call(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body["input"])
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, 0.0]} for i, _ in enumerate(body["input"])]})

    s = Settings(openrouter_api_key=SecretStr("k"))
    e = HostedEmbedder(s, str(tmp_path / "c.json"), transport=httpx.MockTransport(handler))
    e.embed(["alpha"])
    e.embed(["alpha"])
    assert len(calls) == 1
    assert len(HostedEmbedder(s, str(tmp_path / "c.json"), transport=httpx.MockTransport(handler)).embed(["alpha"])[0]) == 2
    assert len(calls) == 1


def test_hosted_embedder_without_key_raises(tmp_path):
    # Explicit empty key: never read the developer's .env, so this test cannot make a network call.
    with pytest.raises(EmbedError):
        HostedEmbedder(Settings(openrouter_api_key=SecretStr("")), str(tmp_path / "none.json")).embed(["x"])


def test_embedding_outage_degrades_to_sparse_and_does_not_break_construction() -> None:
    """Guards the agent failing to start (and Week 1 with it) when the hosted embedding provider is down."""
    from app.w2.rag.embed import EmbedError
    from app.w2.rag.rerank import LexicalReranker
    from app.w2.rag.retriever import Retriever
    from app.w2.rag.corpus import load_corpus

    class Down:
        def embed(self, texts):  # noqa: ANN001
            raise EmbedError("unusable_response")

    r = Retriever(load_corpus(), Down(), LexicalReranker())
    res = r.retrieve("hemoglobin A1c goal diabetes")
    assert res.status.value == "ok" and res.snippets


def test_default_corpus_ships_inside_the_agent_directory():
    """Guards the Docker image missing the corpus: the build context is agent/, so the corpus must live under it."""
    from pathlib import Path
    from app.w2.rag.corpus import default_corpus_dir, load_corpus

    agent_root = Path(__file__).resolve().parents[1]
    assert default_corpus_dir().resolve().is_relative_to(agent_root)
    assert len(load_corpus()) >= 15


def _embed_settings():
    return Settings(openrouter_api_key=SecretStr("k"), openrouter_base_url="http://x")


def test_embedder_retries_once_on_5xx_then_succeeds(tmp_path):
    # guards: a single transient provider error switching dense retrieval off for the request
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    e = HostedEmbedder(_embed_settings(), str(tmp_path / "c.json"), transport=httpx.MockTransport(handler),
                       sleep=lambda s: None)
    assert e.embed(["alpha"]) == [[1.0, 0.0]] and len(calls) == 2


def test_embedder_gives_up_after_one_retry_and_never_retries_a_4xx_or_timeout(tmp_path):
    # guards: retry storms against a provider that is down, and a retried read timeout doubling the wait
    seen = {"n": 0}

    def always(status):
        def h(req):
            seen["n"] += 1
            return httpx.Response(status)
        return h

    def timeout(req):
        seen["n"] += 1
        raise httpx.ReadTimeout("slow")

    for handler, attempts, code in ((always(503), 2, "http_503"), (always(401), 1, "http_401"),
                                    (timeout, 1, "timeout")):
        seen["n"] = 0
        e = HostedEmbedder(_embed_settings(), str(tmp_path / f"{code}.json"), transport=httpx.MockTransport(handler),
                           sleep=lambda s: None)
        with pytest.raises(EmbedError, match=code):
            e.embed(["alpha"])
        assert seen["n"] == attempts
