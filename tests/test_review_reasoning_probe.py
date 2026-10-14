import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from model_helpers import offline_model

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ModelError


def small_fixture(case):
    assert case == "A24-1"
    evidence = {
        key: {
            "source": "source-" + key,
            "locator": "Original section",
            "reading_scope": "text",
            "quote": "Original passage " + key,
        }
        for key in ("E6", "E12", "E13", "E20", "E51", "E52")
    }
    opinions = {
        key: {
            "context": group,
            "text": {"parts": [{"text": "Interpretation " + key, "cites": [cite]}]},
        }
        for key, group, cite in (
            ("O1", "G-time", "E6"),
            ("O2", "G-time", "E12"),
            ("O12", "G-method", "E51"),
            ("O13", "G-method", "E52"),
            ("O14", "G-method", "E13"),
        )
    }
    return {
        "evaluation": {"prompt": "Common standard"},
        "materials": {
            "application": ["E6", "E12", "E13", "E20"],
            "opinions": opinions,
            "opinion_contexts": {
                key: {
                    "title": key,
                    "scope": {"parts": [{"text": "Common condition", "cites": ["E20"]}]},
                    "opinion_ids": [oid for oid, row in opinions.items() if row["context"] == key],
                }
                for key in ("G-time", "G-method")
            },
            "evidence": evidence,
            "sources": {
                row["source"]: {"title": row["source"], "kind": "source"}
                for row in evidence.values()
            },
        },
    }, {"frozen-source.json": "unchanged-source-hash"}


@pytest.fixture
def probe(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "probe_review_reasoning_under_test", scripts / "probe_review_reasoning.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "fixture", small_fixture)
    models = {
        key: ModelConfig(
            model=key + "-model",
            base_url="https://example.invalid/v1",
            structured_output="json_object",
            tools=True,
            retries=2,
            max_tokens=16384,
            parameters={"temperature": 0.7},
            extra_body={"enable_thinking": False},
            formal_extra_body={"enable_thinking": False},
        )
        for key in ("specialist", "strong")
    }
    monkeypatch.setattr(module, "load_config", lambda _: SimpleNamespace(models=models))
    return module


def recording_adapter(probe, monkeypatch, respond):
    created = []

    def select(config):
        def construct(actual, *, env_file, budget):
            assert actual == config
            assert env_file == ".env"
            model = offline_model(actual, respond)
            model.budget = budget
            created.append(model)
            return model

        return construct

    monkeypatch.setattr(probe, "model_class", select)
    return created


def test_fixed_topic_requests_differ_only_by_model_configuration(probe):
    for topic in ("time", "method"):
        specialist = probe.request(f"C-{topic}-specialist")
        strong = probe.request(f"C-{topic}-strong")
        assert specialist["messages"] == strong["messages"]
        assert specialist["source_hashes"] == strong["source_hashes"]
        assert specialist["input_estimate"] == strong["input_estimate"]
        assert specialist["messages"][0]["content"].count("Common standard") == 1
        payload = json.loads(specialist["messages"][1]["content"])
        assert payload["question"] == probe.QUESTIONS[topic]
        expected = {"O1", "O2"} if topic == "time" else {"O12", "O13", "O14"}
        assert set(payload["materials"]["opinions"]) == expected
        for row in (specialist, strong):
            assert row["model"]["max_tokens"] == 6000
            assert row["model"]["retries"] == 0
            assert row["model"]["tools"] is False
            assert row["model"]["parameters"] == {}
            assert row["model"]["extra_body"] == {"enable_thinking": True}


def test_preview_freezes_all_four_cases_without_model_access(probe, tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "model_class", lambda _: pytest.fail("Preview invoked a model"))
    sizes = probe.preview(tmp_path)
    path = tmp_path / "reasoning-preview.json"
    before = path.read_bytes()
    assert set(sizes) == set(probe.CASES)
    assert all(count > 0 for count in sizes.values())
    assert set(probe.read(path)) == set(probe.CASES)
    with pytest.raises(ValueError, match="preserve_existing_reasoning_preview"):
        probe.preview(tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / "v4/budget.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_one_shot_retains_raw_answer_and_replays_without_formal_override(
    probe, tmp_path, monkeypatch, finish_reason
):
    output = tmp_path / "revisions/initial"
    probe.preview(output)
    requests = []
    raw = "Free-form analysis, not JSON.\nA condition remains unresolved."

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "tools" not in body and "response_format" not in body
        assert body["enable_thinking"] is True
        assert body["max_tokens"] == 6000
        assert body["model"] == "specialist-model"
        assert "temperature" not in body
        return httpx.Response(
            200,
            json={
                "id": "response-one",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": raw},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
            },
        )

    created = recording_adapter(probe, monkeypatch, respond)
    result = await probe.live(output, tmp_path, "C-time-specialist", "Fixed-evidence limitation")
    path = output / "cases/C-time-specialist/result.json"
    saved = probe.read(path)
    assert result["status"] == "received"
    assert saved["response"]["content"] == raw
    assert saved["finish_reason"] == finish_reason
    assert saved["diagnostic_reason"] == "Fixed-evidence limitation"
    assert saved["usage"] == {"input": 100, "output": 40}
    assert probe.read(tmp_path / "usage.json")["groups"]["C"]["requests"] == 1
    before = path.read_bytes()
    assert await probe.live(output, tmp_path, "C-time-specialist", "Same case") == saved
    assert len(requests) == len(created) == 1
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_failed_request_is_recorded_and_never_retried(probe, tmp_path, monkeypatch):
    output = tmp_path / "revisions/initial"
    probe.preview(output)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "Temporary provider failure"}})

    created = recording_adapter(probe, monkeypatch, respond)
    with pytest.raises(ModelError, match="model_http_500"):
        await probe.live(output, tmp_path, "C-method-strong", "Fixed-evidence error")
    saved = probe.read(output / "cases/C-method-strong/result.json")
    assert saved["status"] == "failed"
    assert await probe.live(output, tmp_path, "C-method-strong", "Same case") == saved
    assert len(requests) == len(created) == 1
    assert probe.read(tmp_path / "usage.json")["groups"]["C"]["requests"] == 1


@pytest.mark.asyncio
async def test_live_requires_unchanged_prior_preview(probe, tmp_path, monkeypatch):
    output = tmp_path / "revisions/initial"
    monkeypatch.setattr(probe, "model_class", lambda _: pytest.fail("Unexpected model creation"))
    with pytest.raises(FileNotFoundError):
        await probe.live(output, tmp_path, "C-time-specialist", "Reason")
    assert not (tmp_path / "v4/budget.json").exists()
    probe.preview(output)
    monkeypatch.setattr(probe, "PROMPT", probe.PROMPT + " Changed instruction.")
    with pytest.raises(ValueError, match="reasoning_request_changed_after_preview"):
        await probe.live(output, tmp_path, "C-time-specialist", "Reason")
    assert not (output / "cases").exists()
    assert not (tmp_path / "v4/budget.json").exists()
