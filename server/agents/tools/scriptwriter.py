from __future__ import annotations

import os
import re

from langchain.tools import tool
from pydantic import BaseModel, Field, field_validator

from core.utils import get_project_path
from core.character_relations import read_character_relation_lines
from agents.project_content import load_worldview
from agents.story_terminology import get_story_terminology

from .common import ToolExecutionContext, _apply_patch


_INVALID_STORY_TARGET_NAMES = {"null", "none", "undefined", "nil"}


def _validate_story_target_name(value: object, *, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} 必须传入真实字符串，不能为 null。")
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须传入真实字符串。")
    normalized = value.strip()
    if not normalized or normalized.casefold() in _INVALID_STORY_TARGET_NAMES:
        raise ValueError(f"{field_name} 必须传入真实名称，不能使用空值或 null 占位符。")
    return normalized


def _current_story_terminology() -> dict[str, str]:
    """读取当前项目的用户术语；无法读取项目设置时回落到剧本模式。"""

    try:
        from core.project_settings import get_workspace_mode

        user_id, project_name = ToolExecutionContext.get_context()
        return get_story_terminology(get_workspace_mode(user_id, project_name))
    except Exception:
        return get_story_terminology("script")


_STORY_FIELD_COMPAT_NOTE = (
    "这是历史兼容字段：chapter_name/chapter_path 只表示外层 story_group 物理文件夹，"
    "scene_name/scene_path/work_name 只表示内层 story_unit 物理正文文件；字段名可能造成语义混乱，"
    "不可按 chapter/scene 字面推断业务称谓，也不能改名。"
)


class CreateOrRewriteScriptInput(BaseModel):
    overwrite_content: str = Field(description="完整的剧本/小说正文。若目标 story_unit 正文文件尚不存在，系统将自动创建；若已存在则覆盖。必须只包含最终可保存的正文，不得混入解释、确认话术或元话语。")
    chapter_name: str = Field(min_length=1, description=f"{_STORY_FIELD_COMPAT_NOTE} 传入目标 story_group 的可读标题（物理文件夹名称）。剧本模式用户称“剧幕”，小说模式用户称“分卷”。编号由 PreWrite metadata 统一生成。【CRITICAL】正文将保存到该目录下。写作前必须先调用 create_chapter，并在此传入一致的标题。严禁传入 null、字符串 null 或省略此字段。")
    work_name: str = Field(min_length=1, description=f"{_STORY_FIELD_COMPAT_NOTE} 传入目标 story_unit 的可读标题（正文文件名，不含扩展名）。剧本模式用户称“场景”，小说模式用户称“章节”；不要把 work_name 理解成作品名。不要自行编造或修改 chap/scene/order；唯一身份由 PreWrite 签发的文件元数据决定。必须与 PreWrite 的 scene_name 完全一致，严禁传入 null、字符串 null 或省略此字段。")
    target_chars: int | None = Field(default=None, ge=100, le=100000, description="本轮用户或导演明确要求的目标正文字符数。仅作为软目标和落盘回执的统计基准；未传时使用项目默认目标。")

    @field_validator("chapter_name", "work_name", mode="before")
    @classmethod
    def validate_target_names(cls, value: object, info) -> str:
        return _validate_story_target_name(value, field_name=info.field_name)


class PrepareScriptCreationInput(BaseModel):
    task_description: str = Field(description="本次完整场景创作任务，包含目标、冲突、衔接要求与用户意图。")
    chapter_name: str = Field(min_length=1, description=f"{_STORY_FIELD_COMPAT_NOTE} 目标 story_group 物理文件夹的可读标题（剧本模式=剧幕，小说模式=分卷），必须与随后 create_chapter 和 create_or_rewrite_script 使用的 chapter_name 完全一致。编号由系统 metadata 决定。严禁传入 null、字符串 null 或其他占位符。")
    scene_name: str = Field(min_length=1, description=f"{_STORY_FIELD_COMPAT_NOTE} 目标 story_unit 正文文件的可读标题（剧本模式=场景，小说模式=章节），必须与随后 create_or_rewrite_script 使用的 work_name 完全一致；不要把编号当作身份来源。严禁传入 null、字符串 null 或其他占位符。")
    scene_guidance: str = Field(default="", description="大纲中对当前场景的具体指导、关键事件或落点。")
    scene_characters: list[str] = Field(default_factory=list, description="预计在本场出现或必须核对的角色名。")

    @field_validator("chapter_name", "scene_name", mode="before")
    @classmethod
    def validate_target_names(cls, value: object, info) -> str:
        return _validate_story_target_name(value, field_name=info.field_name)


class CreateChapterInput(BaseModel):
    chapter_name: str = Field(description=f"{_STORY_FIELD_COMPAT_NOTE} 要创建的 story_group 物理文件夹可读标题；剧本模式用户称剧幕，小说模式用户称分卷。系统会按 metadata 补充稳定编号。")


class PatchScriptInput(BaseModel):
    search_text: str = Field(description="需要被替换的剧本片段。必须逐字复制 read_chapter_scene 返回的‘已落盘剧本’代码块内部连续原文，不得包含代码围栏、文件标题或解释文字；优先选择可唯一定位的 1-3 句。传入空字符串可将 replace_text 追加到文件末尾")
    replace_text: str = Field(description="修改后的新文本片段")


class ReadCharacterInput(BaseModel):
    character_name: str = Field(description="要查阅的角色名字，例如'张三'")


class OrganizeScenesToChapterInput(BaseModel):
    scene_paths: list[str] = Field(
        description="要归纳的 story_unit 正文文件相对路径列表（相对于 stories 目录；剧本模式用户称场景，小说模式用户称章节）。"
                    "例如：['旧场景.arc', '一 · 开端/1-1 初遇.arc']。"
                    "可通过 list_chapters 或 search_project 获取现有文件路径。"
    )
    new_chapter_name: str = Field(
        description="目标 story_group 物理文件夹名称（剧本模式用户称剧幕，小说模式用户称分卷），格式为「中文数字 · 标题」（如「二 · 发展」「三 · 转折」）。若目录不存在将自动创建。"
    )
    chapter_num: int | None = Field(
        default=None,
        description="历史兼容编号字段：写入文件名元数据 chap=xxx，表示 story_group 身份，不代表固定的用户术语。若不指定，将根据现有 story_group 自动推算。"
    )
    preserve_originals: bool = Field(
        default=False,
        description="是否保留原文件。False=移动（删除原文件），True=复制（保留原文件）。"
    )


class RenameChapterInput(BaseModel):
    chapter_path: str = Field(
        description=f"{_STORY_FIELD_COMPAT_NOTE} 要重命名的 story_group 物理文件夹相对路径（相对于 stories 目录）；剧本模式用户称剧幕，小说模式用户称分卷，例如「一 · 开端」。"
    )
    new_chapter_name: str = Field(
        min_length=1,
        description="新的故事分组目录可读标题；目录编号由现有 chap 元数据保留并自动补到目录名。",
    )


class RenameSceneInput(BaseModel):
    scene_path: str = Field(
        description=f"{_STORY_FIELD_COMPAT_NOTE} 要重命名的 story_unit 正文文件相对路径（相对于 stories 目录）；剧本模式用户称场景，小说模式用户称章节。可使用文件树中显示的 .arc/.md 路径。"
    )
    new_scene_name: str = Field(
        min_length=1,
        description="新的正文文件可读标题；原有 chap、scene、order 元数据和排序身份会保留。",
    )


class ReorderChaptersInput(BaseModel):
    chapter_paths: list[str] = Field(
        min_length=1,
        description="按目标顺序排列的 story_group 物理文件夹相对路径列表；应包含 stories 根目录下的全部目录。只更新外层目录显示顺序（写入 stories_order.json），不改目录名、正文元数据或逻辑路径。",
    )


class ReorderScenesInput(BaseModel):
    chapter_path: str = Field(
        description="目标 story_group 物理文件夹相对路径（相对于 stories 目录；剧本模式用户称剧幕，小说模式用户称分卷）。",
    )
    scene_paths: list[str] = Field(
        min_length=1,
        description="按目标顺序排列的 story_unit 正文文件相对路径列表；应包含目标目录内的全部文件。只更新文件名 order 元数据，不写入 stories_order.json，并保留历史 chap、scene、显示名和逻辑路径。",
    )


class BatchChapterRenameInput(BaseModel):
    path: str = Field(
        description="要重命名的 story_group 物理文件夹相对路径（相对于 stories 目录；剧本用户称剧幕，小说用户称分卷）。",
    )
    new_name: str = Field(
        min_length=1,
        description="新的剧幕/分卷可读标题；历史 chap 身份会自动保留。",
    )


class BatchSceneRenameInput(BaseModel):
    path: str = Field(
        description="要重命名的 story_unit 正文文件相对路径（相对于 stories 目录；剧本用户称场景，小说用户称章节）。",
    )
    new_name: str = Field(
        min_length=1,
        description="新的场景/章节可读标题；历史 chap、scene、order 元数据会保留。",
    )


class BatchStoryMetadataInput(BaseModel):
    path: str = Field(
        description="要改写文件名元数据的故事文件相对路径。",
    )
    display_name: str | None = Field(
        default=None,
        description="可选的新显示标题；不传则保留原标题。",
    )
    chapter_num: int | None = Field(
        default=None,
        ge=1,
        description="可选的新 story_group 编号（历史 chapter_num/chap 语义）；不传则保留原编号。",
    )
    scene_num: int | None = Field(
        default=None,
        ge=1,
        description="可选的新 story_unit 编号（历史 scene_num/scene 语义）；不传则保留原编号。",
    )
    order: int | None = Field(
        default=None,
        ge=1,
        description="可选的全局正文排序槽位；只改文件名中的 order，不按 Unicode 标题排序。",
    )


class BatchRenameChaptersInput(BaseModel):
    renames: list[BatchChapterRenameInput] = Field(
        min_length=1,
        description="批量目录重命名操作列表；每项 path 使用执行前的旧目录路径。",
    )


class BatchRenameScenesInput(BaseModel):
    renames: list[BatchSceneRenameInput] = Field(
        min_length=1,
        description="批量正文文件重命名操作列表；每项 path 使用执行前的旧文件路径。",
    )


class BatchUpdateStoryMetadataInput(BaseModel):
    updates: list[BatchStoryMetadataInput] = Field(
        min_length=1,
        description="批量文件名元数据更新列表；只改显示标题、chap、scene、order，不移动正文文件。",
    )


def _ensure_chapter_dir(stories_path: str, chapter_name: str) -> str:
    name = (chapter_name or "").strip()
    if not name:
        return stories_path
    from story.file_naming import parse_chapter_identity_from_title, resolve_chapter_directory

    chapter_dir = resolve_chapter_directory(
        stories_path,
        name,
        chapter_num=parse_chapter_identity_from_title(name),
    )
    os.makedirs(chapter_dir, exist_ok=True)
    return chapter_dir


def _resolve_story_child_path(stories_path: str, relative_path: str, *, directory: bool | None = None) -> str:
    """解析 stories 子路径并拒绝绝对路径与目录穿越。"""
    raw = str(relative_path or "").replace("\\", "/").strip("/")
    if not raw or os.path.isabs(str(relative_path or "")):
        raise ValueError("路径必须是 stories 目录内的相对路径。")
    root = os.path.abspath(os.path.normpath(stories_path))
    candidate = os.path.abspath(os.path.normpath(os.path.join(root, raw.replace("/", os.sep))))
    try:
        if os.path.commonpath((root, candidate)) != root or os.path.normcase(candidate) == os.path.normcase(root):
            raise ValueError("路径必须位于 stories 目录内。")
    except ValueError:
        raise ValueError("路径必须位于 stories 目录内。") from None
    if not os.path.exists(candidate):
        raise ValueError(f"路径不存在：{relative_path}")
    if directory is True and not os.path.isdir(candidate):
        raise ValueError(f"目标不是故事分组文件夹：{relative_path}")
    if directory is False and not os.path.isfile(candidate):
        raise ValueError(f"目标不是正文文件：{relative_path}")
    return candidate


def _assert_story_path_inside(stories_path: str, path: str) -> str:
    """确认解析后的故事文件仍位于 stories 目录内。"""
    root = os.path.abspath(os.path.normpath(stories_path))
    candidate = os.path.abspath(os.path.normpath(path))
    try:
        inside = os.path.commonpath((root, candidate)) == root
    except ValueError:
        inside = False
    if not inside or os.path.normcase(candidate) == os.path.normcase(root):
        raise ValueError("故事文件必须位于 stories 目录内。")
    return candidate


def _chapter_number_for_directory(chapter_dir: str) -> int | None:
    """从 story_group 文件夹名或正文元数据中确定历史 chap 身份。"""
    from story.file_naming import list_story_files, parse_chapter_identity_from_title

    number = parse_chapter_identity_from_title(os.path.basename(os.path.normpath(chapter_dir)))
    if number is not None:
        return number
    numbers: set[int] = set()
    for _, path, parsed in list_story_files(chapter_dir):
        current = (parsed or {}).get("chapter_num") if parsed else None
        if current is not None:
            numbers.add(int(current))
            continue
        fallback = parse_chapter_identity_from_title(os.path.basename(path))
        if fallback is not None:
            numbers.add(fallback)
    return numbers.pop() if len(numbers) == 1 else None


def _chapter_scene_entries(chapter_dir: str) -> list[tuple[str, str, dict]]:
    """列出 story_group 文件夹内的 story_unit 正文文件及其元数据。"""
    from story.file_naming import list_story_files

    entries: list[tuple[str, str, dict]] = []
    for relative_path, absolute_path, parsed in list_story_files(chapter_dir):
        if parsed:
            entries.append((relative_path, absolute_path, parsed))
    return entries


def _story_order_value(parsed: dict) -> int | None:
    """读取可参与全局排序的 order，缺失或非法值返回 None。"""
    value = parsed.get("order")
    try:
        value = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value > 0 else None


def _scene_order_updates(
    stories_path: str,
    resolved: list[tuple[str, dict]],
) -> list[dict]:
    """为 story_unit 正文重排分配全局唯一 order，同时不触碰稳定身份与展示字段。

    现有 order 没有冲突时复用目标 story_group 原有的全局排序槽位，只交换槽位归属，
    因而不会让无关文件发生物理改名。历史数据若存在缺失或重复 order，则按
    当前 story_sort_key 顺序为全部正文重新编号；重编号仍只改变 order 元数据。
    """
    from story.file_naming import list_story_files, story_sort_key

    all_entries = [
        (absolute_path, parsed)
        for _, absolute_path, parsed in list_story_files(stories_path)
        if parsed
    ]
    target_keys = {os.path.normcase(os.path.abspath(path)) for path, _ in resolved}
    if not target_keys or not all_entries:
        return []

    order_values = [_story_order_value(parsed) for _, parsed in all_entries]
    globally_unique = (
        all(value is not None for value in order_values)
        and len({value for value in order_values if value is not None}) == len(order_values)
    )
    ordered_entries = sorted(
        all_entries,
        key=lambda item: story_sort_key(os.path.relpath(item[0], stories_path).replace(os.sep, "/")),
    )
    ordered_target = [
        item for item in ordered_entries
        if os.path.normcase(os.path.abspath(item[0])) in target_keys
    ]
    if len(ordered_target) != len(resolved):
        raise ValueError("目标场景不在 stories 文件清单中。")

    # 只要原有 order 是全局唯一的，目标场景就可以复用它们原来的排序槽位。
    # 这对 chap 不连续的作品也成立，因为槽位来自全局 story_sort_key，而非
    # chapter_num * 常量这种会产生碰撞的局部公式。chap/scene 只是兼容身份键。
    if globally_unique:
        target_slots = sorted(
            _story_order_value(parsed) for _, parsed in ordered_target
        )
        if len(target_slots) == len(resolved):
            return [
                {"path": path, "order": order}
                for (path, _), order in zip(resolved, target_slots)
            ]

    # 历史文件可能没有 order 或已有碰撞。保留目标章节在当前全局序列中的
    # 位置，仅将它们按调用方顺序放回，再用连续正整数建立唯一排序。
    target_start = min(
        index
        for index, (path, _) in enumerate(ordered_entries)
        if os.path.normcase(os.path.abspath(path)) in target_keys
    )
    without_target = [
        item for item in ordered_entries
        if os.path.normcase(os.path.abspath(item[0])) not in target_keys
    ]
    insertion_index = sum(
        1
        for path, _ in ordered_entries[:target_start]
        if os.path.normcase(os.path.abspath(path)) not in target_keys
    )
    reordered_entries = (
        without_target[:insertion_index]
        + resolved
        + without_target[insertion_index:]
    )
    return [
        {"path": path, "order": index}
        for index, (path, _) in enumerate(reordered_entries, start=1)
    ]


@tool
def read_worldview() -> str:
    """读取世界观全文。"""
    user_id, project_name = ToolExecutionContext.get_context()
    content = load_worldview(user_id, project_name)
    return content if content else "未找到世界观设定。"


@tool(args_schema=ReadCharacterInput)
def read_character(character_name: str) -> str:
    """读取角色设定。

    先按姓名反查稳定 ID，再通过统一门面读取角色正文。
    """
    from story.project_files import (
        load_character_content,
        lookup_character_id_by_name,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    char_id = lookup_character_id_by_name(user_id, project_name, character_name)
    if not char_id:
        return f"未找到名为 '{character_name}' 的角色档案。"

    content = load_character_content(user_id, project_name, char_id)
    relation_lines = read_character_relation_lines(user_id, project_name, char_id)
    if relation_lines:
        content = f"{content}\n\n【作者确认关系】\n{chr(10).join(relation_lines)}"
    return content


@tool
def read_synopsis() -> str:
    """读取故事梗概。"""
    user_id, project_name = ToolExecutionContext.get_context()
    synopsis_path = os.path.join(get_project_path(user_id, project_name), "梗概.txt")
    if not os.path.exists(synopsis_path):
        return "未找到故事梗概。"
    with open(synopsis_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return content or "故事梗概文件为空。"


@tool
def read_beat_sheet() -> str:
    """读取节拍表。"""
    user_id, project_name = ToolExecutionContext.get_context()
    beats_path = os.path.join(get_project_path(user_id, project_name), "节拍表.txt")
    if not os.path.exists(beats_path):
        return "未找到节拍表。"
    with open(beats_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return content or "节拍表文件为空。"


@tool(args_schema=PrepareScriptCreationInput)
def prepare_script_creation(
    task_description: str,
    chapter_name: str,
    scene_name: str,
    scene_guidance: str = "",
    scene_characters: list[str] | None = None,
) -> str:
    """执行完整正文单元创作前的 PreWrite，核对任务包并签发本次落盘凭证。"""
    import json

    from agents.scriptwriter_prewrite import (
        ScriptwriterPreWriteRequest,
        prepare_interactive_scriptwriter_prewrite,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    chapter = str(chapter_name or "").strip()
    scene = str(scene_name or "").strip()
    if not chapter or not scene:
        return (
            "PreWrite 失败：chapter_name 与 scene_name 均不能为空（它们是历史兼容字段，"
            f"分别指{terms['group']}文件夹与{terms['unit']}正文文件；字段名可能造成语义混乱）。"
        )

    result = prepare_interactive_scriptwriter_prewrite(ScriptwriterPreWriteRequest(
        user_id=user_id,
        project_name=project_name,
        task_description=str(task_description or "").strip(),
        chapter_name=chapter,
        scene_name=scene,
        scene_guidance=str(scene_guidance or "").strip(),
        scene_characters=[str(item).strip() for item in (scene_characters or []) if str(item).strip()],
    ))
    return json.dumps({
        "status": "ready",
        "message": f"PreWrite 已完成，可以继续补查只读资料，随后创建{terms['group']}文件夹并落盘{terms['unit']}正文文件。",
        "task_pack": result.brief,
        "planning_note": result.planning_note,
    }, ensure_ascii=False)


@tool(args_schema=CreateOrRewriteScriptInput)
def create_or_rewrite_script(
    overwrite_content: str,
    chapter_name: str | None = None,
    work_name: str | None = None,
    target_chars: int | None = None,
) -> str:
    """创建或覆盖 story_unit 正文文件（兼容工具名 create_or_rewrite_script）。"""
    import json

    from core.utils import get_project_stories_path
    from story.file_naming import (
        DuplicateSceneIdentityError,
        build_story_filename,
        canonical_chapter_display_name,
        next_story_order,
        parse_scene_identity_from_title,
        canonical_scene_display_name,
        resolve_planned_scene_file_path,
        sanitize_story_display_name,
    )

    from core.request_context import (
        clear_scriptwriter_prewrite_receipt,
        get_current_export_format,
        get_scriptwriter_prewrite_receipt,
    )
    from agents.scriptwriter_prewrite import has_matching_prewrite_receipt

    effective_format = get_current_export_format()
    # export_format 是系统确定的项目模式；用户称谓由统一术语表决定，不能按工具名“剧本”推断。
    terms = get_story_terminology("novel" if effective_format == "novel" else "script")
    user_id, project_name = ToolExecutionContext.get_context()
    raw_work_name = str(work_name or "").strip()
    if (
        ToolExecutionContext.get_agent_id() == "agent_scriptwriter"
        and raw_work_name.casefold().endswith((".arc", ".md"))
    ):
        return (
            f"创建/重写剧本失败：work_name 必须是不含扩展名的{terms['unit']}可读标题，"
            f"当前收到“{raw_work_name}”。请去掉末尾的 .arc 或 .md，并使用与 PreWrite 的 scene_name 完全一致的标题后重试。"
        )
    if ToolExecutionContext.get_agent_id() == "agent_scriptwriter" and not has_matching_prewrite_receipt(
        user_id=user_id,
        project_name=project_name,
        chapter_name=chapter_name,
        scene_name=work_name,
    ):
        return (
            f"创建/重写剧本失败：当前完整{terms['unit']}尚未完成匹配的 PreWrite。"
            "请先调用 prepare_script_creation，并使用完全一致的 chapter_name 与 scene_name。"
        )
    content = (overwrite_content or "").strip()
    submitted_content = content
    if effective_format != "novel":
        from core.project_settings import (
            get_visual_illustration_settings,
            is_visual_illustration_enabled,
        )
        from story.arc_safety import sanitize_arc_ai_output
        from story.presentation_manifest import get_project_background_catalog

        visual_settings = get_visual_illustration_settings(user_id, project_name)
        allowed_background_ids = {
            item["id"] for item in get_project_background_catalog(user_id, project_name)
        }
        content = sanitize_arc_ai_output(
            content,
            allow_visual_illustration=is_visual_illustration_enabled(user_id, project_name),
            max_per_scene=visual_settings["max_per_scene"],
            min_node_gap=visual_settings["min_node_gap"],
            allowed_background_ids=allowed_background_ids,
        )
    else:
        from story.novel_parser import parse_novel_document, serialize_novel_document

        novel_document = parse_novel_document(content)
        content = serialize_novel_document(
            novel_document["body"],
            novel_document["conception"],
        )
    if not content:
        return f"创建/重写剧本失败：overwrite_content 为空（当前正文单元为{terms['unit']}）。"

    # 保存前执行统一格式与正文校验，避免把未闭合 conception 或元数据块
    # 误计为正文，造成“保存成功但实际正文为空”的伪成功回执。
    from story.text_metrics import find_story_markup_violations, validate_story_document

    raw_markup_reasons = find_story_markup_violations(submitted_content, effective_format)
    if raw_markup_reasons:
        return (
            f"创建/重写剧本失败：正文落盘校验未通过（{'; '.join(raw_markup_reasons)}）。"
            f"请生成合法且包含实际{terms['unit']}正文的最终内容后重试。"
        )

    is_scriptwriter_agent = ToolExecutionContext.get_agent_id() == "agent_scriptwriter"
    has_scene_header = bool(re.search(r"^#\s+\S", content, re.MULTILINE))
    validation_content = content
    # 兼容旧调用：ARC 片段可以省略场景标题，保存时由下方逻辑自动补齐。
    # 校验时构造等价的临时完整文档；纯文本片段视作旁白节点，避免绕过正文校验。
    if effective_format != "novel" and not has_scene_header:
        has_speaker = bool(re.search(r"^\s*\[[^\]\r\n]+\]\s*$", validation_content, re.MULTILINE))
        speaker_prefix = "" if has_speaker else "[旁白]\n"
        validation_content = f"# {str(work_name or '当前场景').strip() or '当前场景'}\n{speaker_prefix}{validation_content}"

    validation_reasons = validate_story_document(
        validation_content,
        effective_format,
        # 自动写作 PreWrite 链路会在调用工具前强制要求 conception；
        # 直接工具调用保留旧兼容行为，小说可不带该块，但任何已出现的标签必须完整闭合。
        require_conception=False,
        require_parseable_arc=effective_format != "novel",
    )
    if validation_reasons:
        if "body_empty" in validation_reasons:
            return (
                "创建/重写剧本失败：正文没有可见内容，不能落盘。"
                f"（正文落盘校验未通过：{'; '.join(validation_reasons)}）"
                f"请生成实际{terms['unit']}正文后重试。"
            )
        return (
            f"创建/重写剧本失败：正文落盘校验未通过（{'; '.join(validation_reasons)}）。"
            f"请生成合法且包含实际{terms['unit']}正文的最终内容后重试。"
        )

    stories_path = get_project_stories_path(user_id, project_name)
    os.makedirs(stories_path, exist_ok=True)

    target_dir = stories_path
    relative_dir = ""

    raw_display = sanitize_story_display_name(work_name.strip() if work_name and work_name.strip() else "")
    if not raw_display:
        return f"创建/重写剧本失败：work_name 不能为空（当前正文单元为{terms['unit']}）。"
    receipt = get_scriptwriter_prewrite_receipt()
    if ToolExecutionContext.get_agent_id() == "agent_scriptwriter":
        try:
            chapter_num = int(receipt.get("chapter_num")) if receipt else 0
            scene_num = int(receipt.get("scene_num")) if receipt else 0
        except (TypeError, ValueError):
            chapter_num = scene_num = 0
        if chapter_num <= 0 or scene_num <= 0:
            # 兼容旧版请求凭证；新版 PreWrite 始终会写入元数据身份。
            chapter_num, scene_num = parse_scene_identity_from_title(raw_display)
            if chapter_num is None or scene_num is None or chapter_num <= 0 or scene_num <= 0:
                return f"创建/重写剧本失败：PreWrite 未签发有效的{terms['unit']}元数据身份。"
    else:
        chapter_num, scene_num = parse_scene_identity_from_title(raw_display)
        if chapter_num is not None or scene_num is not None:
            if chapter_num is None or scene_num is None or chapter_num <= 0 or scene_num <= 0:
                return f"创建/重写剧本失败：正文身份编号必须是大于 0 的 story_group-story_unit 元数据，例如 3-4。"
        else:
            chapter_num = scene_num = None
    if chapter_num is not None and scene_num is not None:
        try:
            display = canonical_scene_display_name(raw_display, chapter_num, scene_num)
            chapter_display = canonical_chapter_display_name(chapter_name, chapter_num)
        except ValueError as exc:
            return f"创建/重写剧本失败：{exc}"
    else:
        display = raw_display
        chapter_display = str(chapter_name or "").strip()
        if chapter_display:
            target_dir = _ensure_chapter_dir(stories_path, chapter_display)
            relative_dir = os.path.relpath(target_dir, stories_path)
    if chapter_num is not None and scene_num is not None:
        try:
            file_path, existed, _ = resolve_planned_scene_file_path(
                stories_path,
                chapter_num,
                scene_num,
                display,
                chapter_dir_name=chapter_display,
                file_format=effective_format,
            )
        except DuplicateSceneIdentityError as exc:
            return f"创建/重写剧本失败：{exc}。请先在作品管理器中确认并整理重复文件。"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        relative_dir = os.path.relpath(os.path.dirname(file_path), stories_path)
        if relative_dir == ".":
            relative_dir = ""
        filename = os.path.basename(file_path)
    else:
        existed = False
        order = next_story_order(stories_path, relative_dir)
        filename = build_story_filename(display, file_format=effective_format, order=order)
        file_path = os.path.join(target_dir, filename)

    import re as _re

    if effective_format != "novel" and not _re.search(r'^#\s+\S', content, _re.MULTILINE):
        content = f"# {display}\n{content}"

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    rel = os.path.join(relative_dir, filename).replace("\\", "/") if relative_dir else filename
    from agents.story_memory import enqueue_scene_memory_write

    enqueue_scene_memory_write(
        user_id=user_id,
        project_name=project_name,
        label="Scriptwriter 工具落盘",
        scene_text=content,
        chapter_index=(chapter_num - 1) if chapter_num is not None else None,
        scene_index=(scene_num - 1) if scene_num is not None else None,
        chapter_title=chapter_display,
        scene_title=display,
        source_path=rel,
        export_format=effective_format,
        scene_characters=[],
    )
    if ToolExecutionContext.get_agent_id() == "agent_scriptwriter":
        clear_scriptwriter_prewrite_receipt()

    format_label = "小说" if effective_format == "novel" else "剧本"
    action_label = "已覆盖" if existed else "已保存"
    from core.project_settings import get_project_story_tags
    from story.text_metrics import count_story_body_chars

    written_chars = count_story_body_chars(content, effective_format)
    project_target_chars = get_project_story_tags(user_id, project_name).get("scene_target_chars")
    effective_target_chars = target_chars if isinstance(target_chars, int) else project_target_chars
    deviation = written_chars - effective_target_chars if isinstance(effective_target_chars, int) else None
    return json.dumps(
        {
            "status": "saved",
            "message": f"{format_label}{action_label}：{rel}",
            "format": effective_format,
            "path": rel,
            "written_chars": written_chars,
            "target_chars": effective_target_chars,
            "target_source": "current_task" if isinstance(target_chars, int) else "project" if isinstance(project_target_chars, int) else None,
            "deviation_chars": deviation,
            "length_policy": (
                f"具体字数仅为软目标。请根据{terms['unit']}完整性自行判断是否需要调整，"
                f"不要因少量偏差重写整个{terms['unit']}，也不要为凑字数注水。"
            ),
            "content_changed_by_sanitizer": content != submitted_content,
        },
        ensure_ascii=False,
    )


@tool(args_schema=CreateChapterInput)
def create_chapter(chapter_name: str) -> str:
    """创建 story_group 物理文件夹（兼容工具名 create_chapter）。"""
    from core.utils import get_project_stories_path

    terms = _current_story_terminology()
    name = (chapter_name or "").strip()
    if not name:
        return f"创建{terms['group']}失败：chapter_name 不能为空（历史兼容字段，表示 story_group 文件夹）。"
    user_id, project_name = ToolExecutionContext.get_context()
    if ToolExecutionContext.get_agent_id() == "agent_scriptwriter":
        from core.request_context import get_scriptwriter_prewrite_receipt
        from story.file_naming import canonical_chapter_display_name

        receipt = get_scriptwriter_prewrite_receipt() or {}
        try:
            chapter_num = int(receipt.get("chapter_num"))
            name = canonical_chapter_display_name(name, chapter_num)
        except (TypeError, ValueError):
            return f"创建{terms['group']}失败：PreWrite 未签发有效的 story_group 元数据身份。"
    stories_path = get_project_stories_path(user_id, project_name)
    chapter_dir = _ensure_chapter_dir(stories_path, name)
    return f"{terms['group']}已创建（story_group 物理文件夹；兼容工具名 create_chapter）：{name}（路径：{chapter_dir}）"


@tool(args_schema=RenameChapterInput)
def rename_chapter(chapter_path: str, new_chapter_name: str) -> str:
    """只改 story_group 文件夹的可读标题，保留正文文件中的历史 chap/scene/order 身份并同步 stories_order.json。"""
    from core.utils import get_project_path, get_project_stories_path
    from story.file_naming import (
        batch_rename_story_directories_with_order,
        canonical_chapter_display_name,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)
    try:
        source_dir = _resolve_story_child_path(stories_path, chapter_path, directory=True)
        chapter_num = _chapter_number_for_directory(source_dir)
        if chapter_num is None:
            return f"重命名{terms['group']}失败：无法从文件夹或正文元数据确定稳定 story_group 身份。"
        target_name = canonical_chapter_display_name(new_chapter_name, chapter_num)
        target_dir = os.path.join(os.path.dirname(source_dir), target_name)
        order_path = os.path.join(get_project_path(user_id, project_name), "stories_order.json")
        batch_rename_story_directories_with_order(
            [(source_dir, target_dir)],
            stories_path=stories_path,
            order_path=order_path,
            order_rename_map={os.path.basename(source_dir): target_name},
        )
    except Exception as exc:
        return f"重命名{terms['group']}失败：{exc}"
    return f"{terms['group']}已重命名（story_group 物理文件夹；历史兼容工具名 rename_chapter）：{chapter_path} → {target_name}（chap={chapter_num} 保留；已同步 stories_order.json）"


@tool(args_schema=RenameSceneInput)
def rename_scene(scene_path: str, new_scene_name: str) -> str:
    """只改 story_unit 正文文件的可读标题，保留历史 chap、scene、order 排序元数据。"""
    from core.utils import get_project_stories_path
    from story.file_naming import (
        batch_update_story_file_metadata,
        canonical_scene_display_name,
        parse_scene_identity_from_title,
        resolve_story_file_path,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)
    try:
        resolved_path, _, parsed = resolve_story_file_path(stories_path, scene_path)
        if not resolved_path or not parsed:
            return f"重命名{terms['unit']}失败：文件不存在或无法解析：{scene_path}"
        resolved_path = _assert_story_path_inside(stories_path, resolved_path)
        chapter_num = parsed.get("chapter_num")
        scene_num = parsed.get("scene_num")
        if chapter_num is None or scene_num is None:
            chapter_num, scene_num = parse_scene_identity_from_title(parsed.get("display_name"))
        if chapter_num is None or scene_num is None or chapter_num <= 0 or scene_num <= 0:
            return f"重命名{terms['unit']}失败：无法从文件名元数据确定稳定的 story_group/story_unit 身份。"
        display_name = canonical_scene_display_name(new_scene_name, chapter_num, scene_num)
        pairs = batch_update_story_file_metadata(
            stories_path,
            [{
                "path": resolved_path,
                "display_name": display_name,
                "chapter_num": chapter_num,
                "scene_num": scene_num,
                "order": parsed.get("order"),
            }],
        )
    except Exception as exc:
        return f"重命名{terms['unit']}失败：{exc}"
    target_path = pairs[0][1] if pairs else resolved_path
    relative_target = os.path.relpath(target_path, stories_path).replace(os.sep, "/")
    return f"{terms['unit']}已重命名（story_unit 正文文件；历史兼容工具名 rename_scene）：{scene_path} → {relative_target}（chap={chapter_num}, scene={scene_num}, order 保留）"


@tool(args_schema=ReorderScenesInput)
def reorder_scenes(chapter_path: str, scene_paths: list[str]) -> str:
    """按给定顺序重排 story_unit 正文文件，只更新全局唯一的 order 元数据。

    正文文件的历史 chap、scene、显示名和逻辑相对路径都是稳定身份，不会因重排改变；本工具不写入 stories_order.json。
    """
    from core.utils import get_project_stories_path
    from story.file_naming import (
        batch_update_story_file_metadata,
        resolve_story_file_path,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)
    try:
        chapter_dir = _resolve_story_child_path(stories_path, chapter_path, directory=True)
        chapter_num = _chapter_number_for_directory(chapter_dir)
        if chapter_num is None or chapter_num <= 0:
            return f"重排{terms['unit']}失败：无法从 story_group 文件夹或正文元数据确定稳定身份。"
        resolved: list[tuple[str, dict]] = []
        seen: set[str] = set()
        chapter_root = os.path.normcase(os.path.abspath(chapter_dir))
        for scene_path in scene_paths:
            actual_path, _, parsed = resolve_story_file_path(stories_path, scene_path)
            if not actual_path or not parsed:
                raise ValueError(f"文件不存在或无法解析：{scene_path}")
            actual_path = _assert_story_path_inside(stories_path, actual_path)
            actual_key = os.path.normcase(os.path.abspath(actual_path))
            if actual_key in seen:
                raise ValueError(f"{terms['unit']}路径重复：{scene_path}")
            seen.add(actual_key)
            try:
                if os.path.commonpath((chapter_root, actual_key)) != chapter_root:
                    raise ValueError(f"{terms['unit']}不属于目标 story_group：{scene_path}")
            except ValueError:
                raise ValueError(f"{terms['unit']}不属于目标 story_group：{scene_path}") from None
            resolved.append((actual_path, parsed))

        all_scene_keys = {
            os.path.normcase(os.path.abspath(actual_path))
            for _, actual_path, _ in _chapter_scene_entries(chapter_dir)
        }
        if not all_scene_keys:
            raise ValueError("目标 story_group 没有可重排的正文文件。")
        if all_scene_keys != seen:
            raise ValueError("scene_paths 必须包含目标 story_group 内的全部故事文件（当前模式下为全部正文文件），避免遗漏后产生身份冲突。")

        updates = _scene_order_updates(stories_path, resolved)
        batch_update_story_file_metadata(stories_path, updates)
    except Exception as exc:
        return f"重排{terms['unit']}失败：{exc}"
    return f"{terms['unit']}重排完成（story_unit 正文文件；历史兼容工具名 reorder_scenes）：story_group「{chapter_path}」已按给定顺序更新 {len(scene_paths)} 个文件的 order；chap、scene、显示名和路径均保持不变，未写入 stories_order.json。"


@tool(args_schema=ReorderChaptersInput)
def reorder_chapters(chapter_paths: list[str]) -> str:
    """按给定顺序更新 story_group 文件夹显示顺序，只写入 stories_order.json。

    文件夹名、正文文件的历史 chap/scene/order、显示名和逻辑相对路径都保持不变。
    """
    from core.utils import get_project_path, get_project_stories_path
    from story.file_naming import load_stories_order, write_stories_order_atomic

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)
    try:
        resolved_dirs: list[str] = []
        seen_dirs: set[str] = set()
        for chapter_path in chapter_paths:
            source_dir = _resolve_story_child_path(stories_path, chapter_path, directory=True)
            if os.path.normcase(os.path.dirname(os.path.normpath(source_dir))) != os.path.normcase(
                os.path.abspath(os.path.normpath(stories_path))
            ):
                raise ValueError("故事分组重排只支持 stories 根目录下的 story_group 文件夹。")
            key = os.path.normcase(os.path.abspath(source_dir))
            if key in seen_dirs:
                raise ValueError(f"{terms['group']}路径重复：{chapter_path}")
            seen_dirs.add(key)
            resolved_dirs.append(source_dir)

        existing_dirs = []
        for item in os.listdir(stories_path) if os.path.isdir(stories_path) else []:
            candidate = os.path.join(stories_path, item)
            if item.startswith("."):
                continue
            if os.path.isdir(candidate) and _chapter_number_for_directory(candidate) is not None:
                existing_dirs.append(os.path.normcase(os.path.abspath(candidate)))
        if set(existing_dirs) != seen_dirs:
            raise ValueError("chapter_paths 必须包含 stories 根目录下全部已有 story_group 文件夹，避免未列出的分组发生身份冲突。")

        order_path = os.path.join(get_project_path(user_id, project_name), "stories_order.json")
        order_data = load_stories_order(order_path)
        order_data[""] = [os.path.basename(os.path.normpath(path)) for path in resolved_dirs]
        write_stories_order_atomic(order_path, order_data)
    except Exception as exc:
        return f"重排{terms['group']}失败：{exc}"

    return f"{terms['group']}重排完成（story_group 物理文件夹；历史兼容工具名 reorder_chapters）：已按给定顺序更新 {len(resolved_dirs)} 个外层文件夹的显示顺序；仅写入 stories_order.json，文件夹名、正文元数据和逻辑路径均保持不变。"


def _batch_path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


@tool(args_schema=BatchRenameChaptersInput)
def batch_rename_chapters(renames: list[BatchChapterRenameInput]) -> str:
    """批量重命名 story_group 文件夹，并同步更新 stories_order.json。"""
    from core.utils import get_project_path, get_project_stories_path
    from story.file_naming import (
        batch_rename_story_directories_with_order,
        canonical_chapter_display_name,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)
    pairs: list[tuple[str, str]] = []
    targets: set[str] = set()
    try:
        for operation in renames:
            source_dir = _resolve_story_child_path(stories_path, operation.path, directory=True)
            if _batch_path_key(os.path.dirname(source_dir)) != _batch_path_key(stories_path):
                raise ValueError("story_group 文件夹必须位于 stories 根目录下。")
            chapter_num = _chapter_number_for_directory(source_dir)
            if chapter_num is None:
                raise ValueError(f"无法确定 story_group 编号：{operation.path}")
            target_name = canonical_chapter_display_name(operation.new_name, chapter_num)
            target_dir = os.path.join(stories_path, target_name)
            target_key = _batch_path_key(target_dir)
            if target_key in targets:
                raise ValueError(f"多个目录指向同一目标：{target_name}")
            targets.add(target_key)
            pairs.append((source_dir, target_dir))
        source_keys = {_batch_path_key(source) for source, _ in pairs}
        if any(os.path.exists(target) and _batch_path_key(target) not in source_keys for _, target in pairs):
            raise ValueError("目标 story_group 文件夹已存在。")
        result = batch_rename_story_directories_with_order(
            pairs,
            stories_path=stories_path,
            order_path=os.path.join(get_project_path(user_id, project_name), "stories_order.json"),
            order_rename_map={os.path.basename(source): os.path.basename(target) for source, target in pairs},
        )
    except Exception as exc:
        return f"批量重命名{terms['group']}失败：{exc}"
    return f"批量重命名{terms['group']}完成（历史兼容工具名 batch_rename_chapters）：已更新 {len(result)} 个 story_group 文件夹，并同步 stories_order.json。"


@tool(args_schema=BatchRenameScenesInput)
def batch_rename_scenes(renames: list[BatchSceneRenameInput]) -> str:
    """批量重命名 story_unit 正文文件，保留历史 chap、scene、order。"""
    from core.utils import get_project_stories_path
    from story.file_naming import (
        batch_update_story_file_metadata,
        canonical_scene_display_name,
        parse_scene_identity_from_title,
        resolve_story_file_path,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)
    updates: list[dict] = []
    try:
        for operation in renames:
            source_path, _, parsed = resolve_story_file_path(stories_path, operation.path)
            if not source_path or not parsed:
                raise ValueError(f"文件不存在或无法解析：{operation.path}")
            chapter_num = parsed.get("chapter_num")
            scene_num = parsed.get("scene_num")
            if chapter_num is None or scene_num is None:
                chapter_num, scene_num = parse_scene_identity_from_title(parsed.get("display_name"))
            if chapter_num is None or scene_num is None:
                raise ValueError(f"无法确定正文文件的 story_group/story_unit 身份：{operation.path}")
            updates.append({
                "path": source_path,
                "display_name": canonical_scene_display_name(
                    operation.new_name, int(chapter_num), int(scene_num),
                ),
                "chapter_num": int(chapter_num),
                "scene_num": int(scene_num),
                "order": parsed.get("order"),
            })
        result = batch_update_story_file_metadata(stories_path, updates)
    except Exception as exc:
        return f"批量重命名{terms['unit']}失败：{exc}"
    return f"批量重命名{terms['unit']}完成（历史兼容工具名 batch_rename_scenes）：已更新 {len(result)} 个 story_unit 正文文件，排序元数据保持不变。"


@tool(args_schema=BatchUpdateStoryMetadataInput)
def batch_update_story_metadata(updates: list[BatchStoryMetadataInput]) -> str:
    """批量改写正文文件名附带的显示标题和历史 chap、scene、order 元数据。"""
    from core.utils import get_project_stories_path
    from story.file_naming import batch_update_story_file_metadata

    user_id, project_name = ToolExecutionContext.get_context()
    stories_path = get_project_stories_path(user_id, project_name)
    try:
        payload = [
            {
                key: value
                for key, value in {
                    "path": operation.path,
                    "display_name": operation.display_name,
                    "chapter_num": operation.chapter_num,
                    "scene_num": operation.scene_num,
                    "order": operation.order,
                }.items()
                if value is not None
            }
            for operation in updates
        ]
        result = batch_update_story_file_metadata(stories_path, payload)
    except Exception as exc:
        return f"批量更新故事元数据失败：{exc}"
    return f"批量更新故事元数据完成：已更新 {len(result)} 个正文文件名元数据；排序按历史 order 数字字段处理，不按 Unicode 标题排序。"


@tool(args_schema=PatchScriptInput)
def patch_script(search_text: str, replace_text: str) -> str:
    """局部修改剧本内容；匹配失败时应重新读取原文并缩短定位片段，不要直接完整重写。"""
    from core.utils import get_project_stories_path

    user_id, project_name = ToolExecutionContext.get_context()
    from core.project_settings import (
        get_visual_illustration_settings,
        is_visual_illustration_enabled,
    )
    from story.arc_safety import (
        sanitize_arc_ai_fragment,
        validate_arc_visual_prompt_candidate,
    )
    from story.presentation_manifest import get_project_background_catalog

    visual_settings = get_visual_illustration_settings(user_id, project_name)
    allowed_background_ids = {
        item["id"] for item in get_project_background_catalog(user_id, project_name)
    }
    raw_replace_text = replace_text
    visual_enabled = is_visual_illustration_enabled(user_id, project_name)
    if not visual_enabled and re.search(r"@(?:show|presentation)\s+(?:img|illustration_prompt|pending|illustration_pending)", str(raw_replace_text or ""), re.I):
        return (
            "局部修改剧本失败：当前项目的「视觉小说」开关尚未开启。\n"
            "请向用户说明：需前往「设定集 / 世界观」页面的项目风格设置中开启「视觉小说」开关后，方可在剧本节点中写入演出构思并调用生图。"
        )
    replace_text = sanitize_arc_ai_fragment(
        replace_text,
        allow_visual_illustration=visual_enabled,
        allowed_background_ids=allowed_background_ids,
    )
    if str(raw_replace_text or "").strip() and not replace_text:
        return "局部修改剧本失败：replace_text 只包含 AI 无权写入的运行时控制字段。"

    def validate_candidate(original: str, candidate: str) -> None:
        validate_arc_visual_prompt_candidate(
            original,
            candidate,
            max_per_scene=visual_settings["max_per_scene"],
            min_node_gap=visual_settings["min_node_gap"],
        )
    stories_path = get_project_stories_path(user_id, project_name)
    if not os.path.exists(stories_path):
        return "局部修改剧本失败：stories 目录不存在。"

    from story.file_naming import list_story_files

    arc_files = list_story_files(stories_path, file_format="arc")

    for rel_path, file_path, _ in arc_files:
        with open(file_path, "r", encoding="utf-8") as f:
            arc_content = f.read()
        if search_text in arc_content:
            return _apply_patch(
                file_path,
                search_text,
                replace_text,
                validate_content=validate_candidate,
                file_label=rel_path,
            )

    for rel_path, file_path, _ in arc_files:
        result = _apply_patch(
            file_path,
            search_text,
            replace_text,
            validate_content=validate_candidate,
            file_label=rel_path,
        )
        if not result.startswith("局部修改失败"):
            return result

    return (
        "局部修改剧本失败：在当前项目所有剧本文件中均未找到与 search_text 匹配的片段。\n"
        "恢复步骤：调用 read_chapter_scene 重新读取目标场景，从‘已落盘剧本’代码块内部逐字复制一段"
        "可唯一定位的连续原文（建议 1-3 句），缩短 search_text 后重试；不要包含代码围栏、文件标题或解释文字，"
        "也不要重复提交同一个失败片段。仅因局部匹配失败时，不得改用完整重写。"
    )


@tool(args_schema=OrganizeScenesToChapterInput)
def organize_scenes_to_chapter(
    scene_paths: list[str],
    new_chapter_name: str,
    chapter_num: int | None = None,
    preserve_originals: bool = False,
) -> str:
    """将指定 story_unit 正文事务性归纳到目标 story_group 并同步历史 chap、scene、order 元数据。"""
    from core.utils import get_project_stories_path
    from story.file_naming import (
        batch_copy_story_files,
        batch_rename_story_files,
        canonical_chapter_display_name,
        canonical_scene_display_name,
        list_story_files,
        parse_scene_identity_from_title,
        resolve_chapter_directory,
        resolve_story_file_path,
    )

    user_id, project_name = ToolExecutionContext.get_context()
    terms = _current_story_terminology()
    stories_path = get_project_stories_path(user_id, project_name)

    if not scene_paths:
        return "整理失败：scene_paths 不能为空。"

    # 预解析全部源文件，任何错误都在写入前返回。
    source_files: list[tuple[str, str, dict]] = []
    source_keys: set[str] = set()
    for rel_path in scene_paths:
        full_path, _, parsed = resolve_story_file_path(stories_path, rel_path)
        if not full_path or not parsed:
            return f"整理失败：文件不存在或无法解析 - {rel_path}"
        try:
            full_path = _assert_story_path_inside(stories_path, full_path)
        except ValueError as exc:
            return f"整理失败：{exc}"
        source_key = os.path.normcase(os.path.abspath(full_path))
        if source_key in source_keys:
            return f"整理失败：{terms['unit']}路径重复 - {rel_path}"
        source_keys.add(source_key)
        source_files.append((rel_path, full_path, parsed))

    target_chapter_name = (new_chapter_name or "").strip()
    if not target_chapter_name:
        return "整理失败：new_chapter_name 不能为空。"

    effective_chap = chapter_num
    if effective_chap is None:
        existing_chaps = [
            int((parsed or {}).get("chapter_num"))
            for _, _, parsed in list_story_files(stories_path)
            if parsed and (parsed.get("chapter_num") or 0) > 0
        ]
        effective_chap = max(existing_chaps, default=0) + 1
    try:
        effective_chap = int(effective_chap)
    except (TypeError, ValueError):
        return "整理失败：chapter_num 必须是大于 0 的整数。"
    if effective_chap <= 0:
        return "整理失败：chapter_num 必须是大于 0 的整数。"

    try:
        target_chapter_name = canonical_chapter_display_name(target_chapter_name, effective_chap)
        target_dir = resolve_chapter_directory(
            stories_path,
            target_chapter_name,
            chapter_num=effective_chap,
        )
    except Exception as exc:
        return f"整理失败：{exc}"

    source_identity_keys = {
        os.path.normcase(os.path.abspath(path))
        for _, path, _ in source_files
    }
    used_scene_numbers: set[int] = set()
    for _, path, parsed in list_story_files(stories_path):
        if os.path.normcase(os.path.abspath(path)) in source_identity_keys:
            continue
        if not parsed or parsed.get("free") or parsed.get("chapter_num") != effective_chap:
            continue
        scene_num = parsed.get("scene_num")
        if scene_num is not None:
            used_scene_numbers.add(int(scene_num))

    rename_pairs: list[tuple[str, str]] = []
    moved_files: list[str] = []
    for source_label, source_path, parsed in source_files:
        scene_num = parsed.get("scene_num")
        if scene_num is None:
            _, scene_num = parse_scene_identity_from_title(
                parsed.get("display_name")
            )
        if scene_num is None or int(scene_num) <= 0 or int(scene_num) in used_scene_numbers:
            scene_num = max(used_scene_numbers, default=0) + 1
            while scene_num in used_scene_numbers:
                scene_num += 1
        scene_num = int(scene_num)
        used_scene_numbers.add(scene_num)
        display_name = canonical_scene_display_name(
            parsed.get("display_name"),
            effective_chap,
            scene_num,
        )
        from story.file_naming import rebuild_story_filename

        new_filename = rebuild_story_filename(
            os.path.basename(source_path),
            display_name=display_name,
            chapter_num=effective_chap,
            scene_num=scene_num,
            order=effective_chap * 1000 + scene_num,
        )
        target_path = os.path.join(target_dir, new_filename)
        rename_pairs.append((source_path, target_path))
        moved_files.append(f"{source_label} → {target_chapter_name}/{new_filename}")

    target_created = False
    try:
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir, exist_ok=True)
            target_created = True
        if preserve_originals:
            batch_copy_story_files(
                rename_pairs,
                stories_path=stories_path,
                ensure_unique_identity=True,
            )
        else:
            batch_rename_story_files(
                rename_pairs,
                stories_path=stories_path,
                ensure_unique_identity=True,
            )
    except Exception as exc:
        if target_created and os.path.isdir(target_dir) and not os.listdir(target_dir):
            try:
                os.rmdir(target_dir)
            except OSError:
                pass
        return f"整理失败：{exc}"

    action = "复制" if preserve_originals else "移动"
    return f"已成功{action} {len(moved_files)} 个{terms['unit']}到{terms['group']}「{target_chapter_name}」：\n" + "\n".join(moved_files)
