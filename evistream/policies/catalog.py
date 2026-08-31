"""Deterministic metadata for supported policy exceptions and requirement types."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequirementCapability:
    modalities: tuple[str, ...]
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExceptionDefinition:
    description: str
    requirement_type: str
    query_terms: tuple[str, ...]


REQUIREMENT_CAPABILITIES = {
    "visual_presence": RequirementCapability(
        ("vision",), ("search_visual_caption", "inspect_clip")
    ),
    "temporal_context": RequirementCapability(
        ("vision", "transcript"),
        ("expand_temporal_context", "get_neighbor_segments"),
    ),
    "speech_content": RequirementCapability(("transcript",), ("search_transcript",)),
    "text_presence": RequirementCapability(("ocr",), ("search_ocr",)),
}


EXCEPTION_CATALOG = {
    "news_report": ExceptionDefinition(
        "内容是否属于新闻报道或纪实说明",
        "temporal_context",
        ("新闻", "报道", "news report"),
    ),
    "educational_context": ExceptionDefinition(
        "内容是否属于明确的教育、科普或安全说明",
        "temporal_context",
        ("教育", "科普", "安全说明", "educational context"),
    ),
    "fictional_prop_context": ExceptionDefinition(
        "武器或暴力内容是否明确属于影视道具或虚构表演",
        "temporal_context",
        ("道具", "虚构", "影视拍摄", "fictional prop"),
    ),
    "professional_supervision": ExceptionDefinition(
        "危险动作是否由专业人员在受控条件下实施",
        "temporal_context",
        ("专业监督", "安全装备", "受控环境", "professional supervision"),
    ),
    "emergency_response": ExceptionDefinition(
        "相关行为是否属于必要的紧急处置",
        "temporal_context",
        ("紧急处置", "救援", "emergency response"),
    ),
    "incidental_background": ExceptionDefinition(
        "受限商品是否只在背景中偶然出现且未被推介",
        "temporal_context",
        ("背景", "偶然出现", "incidental background"),
    ),
}
