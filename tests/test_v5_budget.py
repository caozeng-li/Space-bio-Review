"""Deterministic delivery accounting; no provider or review runtime is executed."""

import json
from types import SimpleNamespace

import pytest

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ModelError
from spacebio_review.v5.budget import Budget, BudgetLimits, CompletionReserve, SessionAllowance


def count_tokens(text):
    return int(json.loads(text)[0][0]["content"]) - 8


@pytest.fixture
def budget(tmp_path):
    clock = [0.0]
    result = Budget(
        tmp_path,
        BudgetLimits(
            max_input_tokens=1000,
            max_output_tokens=400,
            max_model_calls=10,
            max_seconds=1000,
        ),
        clock=lambda: clock[0],
        token_counters={"budget-test": count_tokens},
    )
    result.register(
        "topic",
        SessionAllowance(
            max_input_tokens=1000,
            max_output_tokens=400,
            completion_input_tokens=160,
            completion_output_tokens=60,
            completion_calls=3,
            completion_seconds=100,
        ),
    )
    return result, clock


def request(budget, *, input_tokens=20, output_tokens=10, purpose="completion", settle=True):
    config = ModelConfig(
        model="budget-test", base_url="https://unused.invalid", max_tokens=output_tokens
    )
    with budget.scope("topic", purpose=purpose):
        ticket = budget.reserve(config, messages=[{"role": "user", "content": str(input_tokens)}])
    if settle:
        budget.settle(
            ticket, SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens)
        )
    return ticket


def reserves(budget):
    return {
        field: budget._reserve_left("topic", field)
        for field in ("input", "output", "calls", "seconds")
    }


@pytest.mark.parametrize("purpose", ["research", "auxiliary"])
def test_completed_owner_can_answer_consultations_without_a_new_delivery_cycle(budget, purpose):
    budget, _ = budget
    request(budget)
    budget.finish("topic")
    account = budget.data["sessions"]["topic"]
    start = account["reserve_start"]
    request(budget, purpose=purpose)
    assert budget.usage("topic")["model_calls"] == 2
    assert not account["active"] and account["reserve_start"] == start
    assert reserves(budget) == dict.fromkeys(("input", "output", "calls", "seconds"), 0)
    with pytest.raises(ValueError, match="inactive_budget_session"):
        request(budget, purpose="completion")


@pytest.mark.parametrize("dynamic_input, protected", [(20, 160), (260, 260)])
@pytest.mark.parametrize("purpose", ["research", "auxiliary"])
def test_readers_preserve_fixed_floor_or_larger_dynamic_plan(
    budget, dynamic_input, protected, purpose
):
    budget, _ = budget
    assert budget.update_completion(
        "topic", CompletionReserve(input_tokens=dynamic_input, calls=1, steps=["write"])
    ) is None
    affordable = [{"input_tokens": 1000 - protected, "output_reserve": 10}]
    assert budget.research_capacity("topic", affordable) is None
    too_large = [{"input_tokens": 1001 - protected, "output_reserve": 10}]
    assert budget.research_capacity("topic", too_large) == "reading_input_capacity_reached"
    request(budget, input_tokens=1000 - protected, purpose=purpose)
    with pytest.raises(ModelError, match="input_token_budget_exhausted"):
        request(budget, input_tokens=8, purpose=purpose)
    request(budget, input_tokens=protected)


def test_fixed_and_dynamic_remainders_use_their_own_origins(budget):
    budget, clock = budget
    request(budget, purpose="research")
    ticket = request(budget, input_tokens=40, output_tokens=20, settle=False)
    clock[0] = 10
    budget.settle(ticket, SimpleNamespace(prompt_tokens=40, completion_tokens=20))
    assert reserves(budget) == {"input": 120, "output": 40, "calls": 2, "seconds": 90}

    assert budget.update_completion(
        "topic", CompletionReserve(input_tokens=180, output_tokens=30, calls=4, seconds=50)
    ) is None
    ticket = request(budget, input_tokens=30, output_tokens=10, settle=False)
    clock[0] = 30
    budget.settle(ticket, SimpleNamespace(prompt_tokens=30, completion_tokens=10))
    assert reserves(budget) == {"input": 150, "output": 30, "calls": 3, "seconds": 70}

    assert budget.update_completion(
        "topic", CompletionReserve(input_tokens=20, output_tokens=10, calls=1, seconds=5)
    ) is None
    assert reserves(budget) == {"input": 90, "output": 30, "calls": 1, "seconds": 70}
    assert budget.data["sessions"]["topic"]["reserve_start"] == 0


def test_next_delivery_cycle_keeps_cumulative_usage(budget):
    budget, _ = budget
    request(budget, input_tokens=40, output_tokens=20)
    budget.update_completion("topic", CompletionReserve(input_tokens=20, calls=1, steps=["parse"]))
    expected_reserves, expected_usage = reserves(budget), budget.usage("topic")
    budget.activate("topic")
    assert reserves(budget) == expected_reserves
    budget.finish("topic")
    assert reserves(budget) == dict.fromkeys(expected_reserves, 0)

    budget.activate("topic")
    assert budget.data["sessions"]["topic"]["reserve_start"] == 1
    assert "completion_plan" not in budget.data["sessions"]["topic"]
    assert reserves(budget) == {"input": 160, "output": 60, "calls": 3, "seconds": 100}
    assert budget.usage("topic") == expected_usage


def test_completion_can_fit_after_actual_usage_exceeds_estimate(budget):
    budget, _ = budget
    ticket = request(budget, input_tokens=840, purpose="research", settle=False)
    # Actual provider usage can leave less than the configured floor in this run.
    budget.settle(ticket, SimpleNamespace(prompt_tokens=900, completion_tokens=10))
    assert budget.update_completion(
        "topic", CompletionReserve(input_tokens=20, calls=1, steps=["parse"])
    ) == "global_completion_input_reserve_unavailable"
    with pytest.raises(ModelError, match="input_token_budget_exhausted"):
        request(budget, input_tokens=8, purpose="research")
    request(budget, input_tokens=20)
    assert budget.usage("topic")["input_tokens"] == 920
    budget.update_completion("topic", CompletionReserve())
    assert reserves(budget) == {"input": 0, "output": 0, "calls": 0, "seconds": 0}


def test_unaffordable_next_delivery_keeps_owner_inactive_and_usage_intact(budget):
    budget, _ = budget
    request(budget, input_tokens=840, purpose="research")
    request(budget, input_tokens=100)
    budget.update_completion("topic", CompletionReserve())
    budget.finish("topic")
    previous = dict(budget.data["sessions"]["topic"])
    expected_usage = budget.usage("topic")

    with pytest.raises(ModelError, match="completion_input_reserve_unavailable"):
        budget.activate("topic")
    assert budget.data["sessions"]["topic"] == previous
    assert budget.usage("topic") == expected_usage
    assert json.loads(budget.path.read_text("utf-8"))["sessions"]["topic"] == previous


def test_empty_remaining_plan_releases_protection_until_next_delivery(budget):
    budget, _ = budget
    budget.update_completion("topic", CompletionReserve())
    all_remaining = [{"input_tokens": 1000, "output_reserve": 400}]
    assert budget.research_capacity("topic", all_remaining) is None
    budget.activate("topic")
    assert reserves(budget) == {"input": 0, "output": 0, "calls": 0, "seconds": 0}
    budget.finish("topic")
    budget.activate("topic")
    assert reserves(budget) == {"input": 160, "output": 60, "calls": 3, "seconds": 100}


def test_translation_with_zero_fixed_floor_uses_only_remaining_dynamic_work(tmp_path):
    budget = Budget(tmp_path, clock=lambda: 0, token_counters={"budget-test": count_tokens})
    budget.register("topic", SessionAllowance(completion_calls=0))
    assert reserves(budget) == {"input": 0, "output": 0, "calls": 0, "seconds": 0}
    budget.update_completion(
        "topic", CompletionReserve(input_tokens=50, output_tokens=20, calls=2, steps=["translate"])
    )
    request(budget, input_tokens=30, output_tokens=10)
    assert reserves(budget) == {"input": 20, "output": 10, "calls": 1, "seconds": 0}
    budget.update_completion("topic", CompletionReserve())
    assert reserves(budget) == {"input": 0, "output": 0, "calls": 0, "seconds": 0}


def test_completion_wall_time_counts_overlapping_requests_once(budget):
    budget, clock = budget
    first = request(budget, settle=False)
    clock[0] = 20
    budget.update_completion("topic", CompletionReserve(seconds=60))
    second = request(budget, settle=False)
    clock[0] = 40
    budget.settle(first, SimpleNamespace(prompt_tokens=20, completion_tokens=10))
    clock[0] = 60
    budget.settle(second, SimpleNamespace(prompt_tokens=20, completion_tokens=10))
    # Fixed work covers [0, 40] U [20, 60]; the later plan covers only [20, 60].
    assert budget.remaining_seconds(session_id="topic", purpose="research") == 900
    assert budget.remaining_seconds(session_id="topic", purpose="completion") == 940
    budget.update_completion("topic", CompletionReserve(seconds=70))
    assert budget.remaining_seconds(session_id="topic", purpose="research") == 870
