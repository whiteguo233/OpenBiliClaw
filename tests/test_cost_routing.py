"""Cost-aware same-model routing tests (issue #353 follow-up P5)."""

from __future__ import annotations

import types

from openbiliclaw.llm.base import LLMProvider, LLMRegistry, LLMResponse
from openbiliclaw.llm.rate_card import RateCatalog, build_cost_ranker


class _FakeProvider(LLMProvider):
    def __init__(self, name: str, model: str) -> None:
        self._name = name
        self.model = model
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, messages, **kwargs) -> LLMResponse:  # type: ignore[no-untyped-def]
        self.calls += 1
        return LLMResponse(content="ok", model=self.model, provider=self._name)


class _RouteRegistry:
    def __init__(self, routes: dict[str, tuple[str, str]]) -> None:
        self._routes = routes

    def instance_route(self, name: str) -> tuple[str, str]:
        return self._routes[name]


def _catalog(deepseek_output: float, openai_output: float) -> RateCatalog:
    return RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {
                    "models": {
                        "m": {
                            "input": deepseek_output,
                            "output": deepseek_output,
                            "currency": "USD",
                            "source": "test",
                        }
                    }
                },
                "openai": {
                    "models": {
                        "m": {
                            "input": openai_output,
                            "output": openai_output,
                            "currency": "USD",
                            "source": "test",
                        }
                    }
                },
            },
            "fx": {"usd_cny": 1.0},
        }
    )


def test_ranker_puts_the_cheapest_same_model_instance_first() -> None:
    registry = _RouteRegistry(
        {
            "relay-a": ("deepseek", "m"),
            "relay-b": ("openai", "m"),
        }
    )
    rank = build_cost_ranker(registry, _catalog(deepseek_output=10.0, openai_output=1.0))
    assert rank(["relay-a", "relay-b"]) == ["relay-b", "relay-a"]
    assert rank(["relay-b", "relay-a"]) == ["relay-b", "relay-a"]


def test_ranker_never_swaps_different_models() -> None:
    registry = _RouteRegistry(
        {
            "flagship": ("deepseek", "m1"),
            "cheap": ("openai", "m2"),
        }
    )
    rank = build_cost_ranker(
        registry,
        RateCatalog.from_dict(
            {
                "providers": {
                    "deepseek": {"models": {"m1": {"input": 1, "output": 1, "currency": "USD"}}},
                    "openai": {"models": {"m2": {"input": 0.1, "output": 0.1, "currency": "USD"}}},
                },
                "fx": {"usd_cny": 1.0},
            }
        ),
    )
    assert rank(["flagship", "cheap"]) == ["flagship", "cheap"]


def test_ranker_keeps_unknown_prices_in_original_order() -> None:
    registry = _RouteRegistry(
        {
            "a": ("mystery", "nope"),
            "b": ("mystery", "nope"),
        }
    )
    rank = build_cost_ranker(registry, _catalog(1.0, 1.0))
    assert rank(["a", "b"]) == ["a", "b"]


async def test_complete_chain_uses_the_injected_ranker() -> None:
    registry = LLMRegistry()
    first = _FakeProvider("first", "m")
    second = _FakeProvider("second", "m")
    registry.register(first, name="first", provider_type="deepseek")
    registry.register(second, name="second", provider_type="openai")
    registry.configure_chain(["first", "second"])
    registry.route_ranker = lambda order: ["second", "first"]

    response = await registry.complete_chain(
        ["first", "second"],
        [{"role": "user", "content": "hello"}],
    )

    assert response.instance_id == "second"
    assert first.calls == 0
    assert second.calls == 1


async def test_ranker_failure_falls_back_to_the_configured_order() -> None:
    registry = LLMRegistry()
    first = _FakeProvider("first", "m")
    second = _FakeProvider("second", "m")
    registry.register(first, name="first", provider_type="deepseek")
    registry.register(second, name="second", provider_type="openai")
    registry.configure_chain(["first", "second"])

    def _boom(_order: list[str]) -> list[str]:
        raise RuntimeError("catalog unavailable")

    registry.route_ranker = _boom
    response = await registry.complete_chain(
        ["first", "second"],
        [{"role": "user", "content": "hello"}],
    )
    assert response.instance_id == "first"


def test_ranker_cannot_drop_a_fallback() -> None:
    registry = LLMRegistry()
    registry.register(_FakeProvider("a", "m"), name="a")
    registry.register(_FakeProvider("b", "m"), name="b")
    registry.route_ranker = lambda _order: ["b"]
    assert registry._apply_route_ranker(["a", "b"]) == ["b", "a"]


def test_cost_aware_routing_config_round_trips(tmp_path) -> None:
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text("[llm]\ncost_aware_routing = true\n", encoding="utf-8")
    config = load_config(path, consult_environment=False)
    assert config.llm.cost_aware_routing is True

    save_config(config, path)
    assert load_config(path, consult_environment=False).llm.cost_aware_routing is True


def test_primary_route_is_stable_for_cost_routing() -> None:
    """Sanity check that the route helper used by the ranker is duck-typed."""

    from openbiliclaw.llm.rate_card import primary_route

    config = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            default_chain=["a"],
            instances={"a": types.SimpleNamespace(provider_type="openai_compatible", model="m")},
            default_provider="",
        )
    )
    assert primary_route(config) == ("openai_compatible", "m")


def test_ranker_keeps_cross_model_slots_stable() -> None:
    """[A1, B, A2] must become [A2, B, A1], not [A1, A2, B]."""

    registry = _RouteRegistry(
        {
            "a1": ("deepseek", "m"),
            "b": ("openai", "other"),
            "a2": ("openai", "m"),
        }
    )
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {"models": {"m": {"input": 9, "output": 9, "currency": "USD"}}},
                "openai": {
                    "models": {
                        "m": {"input": 1, "output": 1, "currency": "USD"},
                        "other": {"input": 0.1, "output": 0.1, "currency": "USD"},
                    }
                },
            },
            "fx": {"usd_cny": 1.0},
        }
    )
    rank = build_cost_ranker(registry, catalog)
    assert rank(["a1", "b", "a2"]) == ["a2", "b", "a1"]


def test_instance_route_reads_private_model_attribute() -> None:
    registry = LLMRegistry()

    class _PrivateModelProvider(_FakeProvider):
        def __init__(self, name: str, model: str) -> None:
            super().__init__(name, model)
            self._model = model
            del self.model

    registry.register(_PrivateModelProvider("relay", "deepseek-v4-flash"), name="relay")
    assert registry.instance_route("relay") == ("relay", "deepseek-v4-flash")


def test_ranker_prefers_known_free_ollama_over_a_paid_route() -> None:
    registry = _RouteRegistry(
        {
            "local": ("ollama", "deepseek-r1"),
            "paid": ("openrouter", "deepseek-r1"),
        }
    )
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "openrouter": {
                    "models": {
                        "deepseek-r1": {
                            "input": 0.1,
                            "output": 0.2,
                            "currency": "USD",
                        }
                    }
                }
            }
        }
    )
    rank = build_cost_ranker(registry, catalog)
    assert rank(["paid", "local"]) == ["local", "paid"]


async def test_stream_chain_stamps_actual_provider_and_model() -> None:
    from openbiliclaw.llm.base import LLMStreamChunk

    class _StreamProvider:
        model = "glm-4.7-flash"

        async def stream_complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            yield LLMStreamChunk(delta="hello")

    registry = LLMRegistry()
    registry.register(_StreamProvider(), name="paid", provider_type="openrouter")

    async def _stream(provider):  # type: ignore[no-untyped-def]
        async for chunk in provider.stream_complete():
            yield chunk

    chunks = [chunk async for chunk in registry._stream_chain(["paid"], stream_fn=_stream)]
    assert chunks[0].delta == "hello"
    assert chunks[0].provider == "openrouter"
    assert chunks[0].model == "glm-4.7-flash"
    assert chunks[0].instance_id == "paid"
