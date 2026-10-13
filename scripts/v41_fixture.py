"""Retained originals and real domain objects shared by offline/live stage checks."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from spacebio_review.application import ApplicationBundle
from spacebio_review.evidence.knowledge import LiteratureConfig
from spacebio_review.evidence.literature import KnowledgeSession
from spacebio_review.evidence.store import SourceStore
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.records import Records, write_json

FIXTURE = Path(__file__).parent / "fixtures/v41"
APP = "src-175b7510b818"
EXTERNAL = "src-629f1c697892"


def fixture_environment(output, runtime):
    fixture = json.loads((FIXTURE / "input.json").read_text("utf-8"))
    for path in FIXTURE.glob("src-*.json"):
        doc = json.loads(path.read_text("utf-8"))
        write_json(output / "sources" / doc["source_id"] / "source.json", doc)
        write_json(output / "sources" / doc["source_id"] / "candidate.json", doc["candidate"])

    def no_network(request):
        raise RuntimeError("retained_originals_only_no_discovery")

    client = httpx.AsyncClient(transport=httpx.MockTransport(no_network))
    store = SourceStore(output / "sources", client, browser_fallback=False)
    store.restore()
    config = V4Config(
        task_turns=5,
        task_actions=2,
        literature_tasks_per_question=0,
        parallel_questions=1,
        max_questions=10,
    )
    prep_config = load_preparation_config("config/preparation.yaml")
    prep_config.read_actions = 0
    prep_config.max_model_turns = 5
    records = Records(output)
    ex = Execution(
        runtime, records, config, {"prompt": prep_config.evaluation.prompt()}, ReportingConfig()
    )
    prep = Preparation(
        ex, store, ApplicationBundle.model_validate(fixture["application"]), prep_config
    )
    owner = SimpleNamespace(
        store=store, runtime=runtime, discovery=None, index=None, config=LiteratureConfig()
    )
    literature = SimpleNamespace(new_session=lambda q: KnowledgeSession(owner, q))
    return SimpleNamespace(
        ex=ex,
        prep=prep,
        records=records,
        store=store,
        client=client,
        package=fixture["package"],
        literature=literature,
    )


def professional_tasks():
    return [
        {
            "task_id": "research/method/expert-1",
            "expert_id": "expert-1",
            "application_refs": [{"source_id": APP, "passage_id": f"p{i}"} for i in range(25, 29)],
            "proposal": {
                "question": "候选相分离验证方法（包括1,6-己二醇扰动、终点固定和活体观察）"
                "的特异性如何，哪些可支持本项目细胞机制推断？",
                "scope": "体外凝聚体与细胞实验的认识边界",
                "aspects": ["rigor"],
                "information_route": "申请原文和已提供的社区方法文献",
            },
        },
        {
            "task_id": "research/imaging/expert-3",
            "expert_id": "expert-3",
            "application_refs": [
                {"source_id": APP, "passage_id": f"p{i}"} for i in [21, 22, 23, 24, 37]
            ],
            "proposal": {
                "question": "申请成像配置可以支持何种动力学观测，哪些可观测性条件仍需确认？",
                "scope": "空间实验与地面筛选的测量关系",
                "aspects": ["feasibility"],
                "information_route": "申请参数与已提供原始方法资料",
            },
        },
    ]


def originals():
    return [
        {"source_id": EXTERNAL, "passage_ids": ["p12", "p13", "p14", "p15", "p16", "p17", "p18"]}
    ]
