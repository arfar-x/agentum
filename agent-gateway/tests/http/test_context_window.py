# Covers: FR-RTE-06

from tests.application.fakes import agent


async def models(gw):
    r = await gw.client.get("/v1/models", headers=gw.auth())
    return {m["id"]: m for m in r.json()["data"]}


async def test_agents_report_their_models_context_window_under_the_names_clients_read(make_gateway):
    gw = await make_gateway([agent("router"), agent("big", context_window=500_000)])
    gw.container.context_window = 262_144
    got = await models(gw)
    for field in ("context_window", "context_length", "max_model_len"):
        assert got["router"][field] == 262_144 and got["big"][field] == 500_000


async def test_a_per_model_table_beats_the_default_and_fills_upstream_entries_that_lack_one(make_gateway):
    gw = await make_gateway([agent("router")])
    gw.container.context_window = 100_000
    gw.container.config.context_windows["base-model"] = 128_000
    gw.upstream.models = [{"id": "base-model", "object": "model"}, {"id": "other", "object": "model"},
                          {"id": "vllm", "object": "model", "max_model_len": 4096}]
    got = await models(gw)
    assert got["router"]["context_window"] == 128_000          # its upstream model's table entry
    assert got["base-model"]["context_window"] == 128_000 and got["other"]["context_window"] == 100_000
    assert got["vllm"]["max_model_len"] == 4096 and "context_window" not in got["vllm"]   # stated by the upstream: untouched
    one = await gw.client.get("/v1/models/router", headers=gw.auth())
    assert one.json()["context_window"] == 128_000


async def test_nothing_is_reported_when_nothing_is_known(make_gateway):
    gw = await make_gateway([agent("router")])
    assert "context_window" not in (await models(gw))["router"]
