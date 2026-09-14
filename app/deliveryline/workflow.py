from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowSubstage:
    name: str
    objective: str
    acceptance: str


@dataclass(frozen=True)
class WorkflowStage:
    name: str
    substages: tuple[WorkflowSubstage, ...] = ()


WORKFLOW_STAGES = (
    WorkflowStage(
        name="需求提出",
        substages=(
            WorkflowSubstage(
                name="原始需求入库",
                objective="保存原始需求并建立需求档案。",
                acceptance="任意非空原始内容已入库，且不自动解析或改写原始内容。",
            ),
            WorkflowSubstage(
                name="需求整理与档案补全",
                objective="保留原始需求，补全为可提交评审的正式档案。",
                acceptance="补全标题、背景与问题、交付目标、本次范围、不做什么、约束与依赖、验收标准、风险与待确认事项；不自动提交评审。",
            ),
        ),
    ),
    WorkflowStage(name="需求评审"),
    WorkflowStage(name="方案设计"),
    WorkflowStage(name="开发实现"),
    WorkflowStage(name="自动化测试"),
    WorkflowStage(name="测试验收"),
)


def workflow_stage_data() -> list[dict[str, object]]:
    return [
        {
            "name": stage.name,
            "substages": [
                {
                    "name": substage.name,
                    "objective": substage.objective,
                    "acceptance": substage.acceptance,
                }
                for substage in stage.substages
            ],
        }
        for stage in WORKFLOW_STAGES
    ]


def current_substage(stage_name: str, delivery_status: str) -> WorkflowSubstage | None:
    stage = next((item for item in WORKFLOW_STAGES if item.name == stage_name), None)
    if stage is None or not stage.substages:
        return None
    if stage.name == "需求提出" and delivery_status != "已归档":
        return stage.substages[1]
    return stage.substages[0]


def collaboration_substage() -> WorkflowSubstage:
    return WORKFLOW_STAGES[0].substages[1]
