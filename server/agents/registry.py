# agents/registry.py
# 本文件用于注册所有可被前端管理的 Agent 及其用途key、显示名、描述等元数据
#
# name / display / description 采用多语言字典结构：
#   { 'zh-CN': '中文', 'en-US': 'English', 'ja-JP': '日本語', 'ko-KR': '한국어' }
# 新增语言时，只需在每个 Agent 条目的 name/display/description 中加一组翻译即可。
# 前端通过 i18n 的 components.agentNames / agentDescriptions 做本地映射，
# 后端通过 _resolve_i18n_field() 按请求 locale 提取对应字段。
#
# icon: Lucide 图标名（PascalCase），前端 AgentAvatar 通过映射表转为组件。
# color: 该 Agent 的专属主题色（hex），用于头像描边/光晕/轮盘扇片渐变。
# 这两个字段是前端所有 Agent 视觉呈现的唯一真相源，禁止在前端各处再硬编码。

AGENT_REGISTRY = [
    {
        "key": "agent_director",
        "name": {
            "zh-CN": "导演",
            "en-US": "Director",
            "ja-JP": "監督",
            "ko-KR": "감독",
        },
        "display": {
            "zh-CN": "负责统筹全局，多轮协调并调度专家 Agent。",
            "en-US": "Coordinates the whole workflow and delegates tasks to specialist agents.",
            "ja-JP": "全体進行を統括し、各 Agent を協調させます。",
            "ko-KR": "전체 진행을 총괄하며 전문가 Agent를 다중 협조 및 조율합니다.",
        },
        "description": {
            "zh-CN": "系统的总入口与协调中枢。通过多轮工具调用自主决策：查阅项目章节结构、读取剧本内容、委派任务给专家 Agent。可直接回答用户问题，也可协调多个专家完成复杂任务。",
            "en-US": "The central entry point and coordination hub. Autonomously decides via multi-turn tool calls: inspect chapter structure, read script content, delegate tasks to specialist agents. Can directly answer user questions or coordinate multiple specialists for complex tasks.",
            "ja-JP": "システムの総合入口と調整ハブ。マルチターンのツール呼び出しで自律的に判断：章構造の確認、脚本内容の読み取り、専門 Agent へのタスク委派。ユーザーの質問に直接回答することも、複数の専門家を調整して複雑なタスクを完了することも可能です。",
            "ko-KR": "시스템의 통합 진입점이자 조율 허브입니다. 다중 턴 툴 호출을 통해 자율적으로 의사를 결정합니다: 프로젝트 장 구조 확인, 시나리오 내용 판독, 전문가 Agent에 태스크 위임 등의 작업을 수행합니다. 사용자의 질문에 직접 답변하거나 여러 전문가를 조율하여 복잡한 작업을 완수할 수 있습니다。",
        },
        "group": "main",
        "participatesInBeaconBus": True,
        "icon": "Compass",
        "color": "#5b8cff"
    },
    {
        "key": "agent_showrunner",
        "name": {
            "zh-CN": "文案策划",
            "en-US": "Showrunner",
            "ja-JP": "ショーランナー",
            "ko-KR": "기획자",
        },
        "display": {
            "zh-CN": "负责故事大纲与分集规划。",
            "en-US": "Plans story architecture and pacing.",
            "ja-JP": "物語構成とテンポ設計を担当します。",
            "ko-KR": "스토리 시놉시스와 회차별 구성을 담당합니다.",
        },
        "description": {
            "zh-CN": "负责创作故事大纲、梗概、剧情结构、分集/分章规划、节拍表（Beat Sheet）。当用户想要讨论整体剧情走向或大纲结构时使用。",
            "en-US": "Creates story outlines, synopses, plot structures, episode/chapter planning, and beat sheets. Used when discussing overall plot direction or outline structure.",
            "ja-JP": "ストーリーのアウトライン、あらすじ、プロット構造、エピソード/章構成、ビートシートを作成します。全体的なプロットの方向性や構成について議論する際に使用します。",
            "ko-KR": "스토리 아웃라인, 시놉시스, 플롯 구조, 회차/장 구성, 비트 시트(Beat Sheet)의 창작을 담당합니다. 사용자가 전체적인 스토리 흐름이나 시놉시스 구조에 대해 논의하고자 할 때 사용됩니다。",
        },
        "group": "main",
        "icon": "Waypoints",
        "color": "#2dd4bf"
    },
    {
        "key": "agent_scriptwriter",
        "name": {
            "zh-CN": "执笔编剧",
            "en-US": "Scriptwriter",
            "ja-JP": "脚本作家",
            "ko-KR": "시나리오 작가",
        },
        "display": {
            "zh-CN": "负责具体场景剧本撰写。",
            "en-US": "Writes scenes and script content.",
            "ja-JP": "本文とシーン脚本の執筆を担当します。",
            "ko-KR": "구체적인 씬과 시나리오 집필을 담당합니다.",
        },
        "description": {
            "zh-CN": "负责具体的正文撰写、场景描写、对话生成、续写、改写一整段。当用户讨论某个具体场景或者剧本的内容时使用。",
            "en-US": "Handles actual text writing, scene descriptions, dialogue generation, continuation, and paragraph rewriting. Used when discussing a specific scene or script content.",
            "ja-JP": "本文の執筆、シーン描写、対話生成、続きの執筆、段落の書き直しを担当します。特定のシーンや脚本の内容について議論する際に使用します。",
            "ko-KR": "구체적인 본문 집필, 씬 묘사, 대사 생성, 이어 쓰기, 문단 개작을 담당합니다. 사용자가 특정 씬이나 시나리오 내용에 대해 논의할 때 사용됩니다。",
        },
        "group": "main",
        "icon": "Feather",
        "color": "#38bdf8"
    },
    {
        "key": "agent_critic",
        "name": {
            "zh-CN": "评审专家",
            "en-US": "Critic",
            "ja-JP": "批評エキスパート",
            "ko-KR": "비평 전문가",
        },
        "display": {
            "zh-CN": "负责剧本/小说审查、AI味诊断与修改建议。",
            "en-US": "Reviews quality and provides rewrite suggestions.",
            "ja-JP": "品質レビューと改稿提案を担当します。",
            "ko-KR": "시나리오/소설 검토, AI 특유의 어투 진단 및 수정 제안을 담당합니다.",
        },
        "description": {
            "zh-CN": "负责对已有剧本、小说段落、具体场景进行严格评审：检查 AI 味残留、对白自然度、文学承载、逻辑与人设问题。既可由用户直接聊天咨询，也可被导演委派读取具体场景后给出审稿意见。",
            "en-US": "Conducts rigorous reviews of existing scripts, novel passages, and specific scenes: checks for AI flavor residue, dialogue naturalness, literary quality, logic and character consistency. Can be consulted directly by users or delegated by the Director to review specific scenes.",
            "ja-JP": "既存の脚本や小説の段落、特定のシーンを厳格にレビュー：AI味の残存、対話の自然さ、文学的品質、論理とキャラクター一貫性をチェックします。ユーザーから直接相談されることも、監督から特定シーンのレビューを委派されることもあります。",
            "ko-KR": "기존 시나리오, 소설 문단, 구체적인 씬에 대한 엄격한 심사를 담당합니다: AI 특유의 어투 잔재 검사, 대사의 자연스러움, 문학적 가치, 논리 및 캐릭터 설정 오류 등을 점검합니다. 사용자가 직접 채팅으로 상담할 수도 있고, 감독의 위임을 받아 특정 씬을 읽고 검토 의견을 제시할 수도 있습니다。",
        },
        "group": "main",
        "icon": "ScanEye",
        "color": "#ff6b6b"
    },
    {
        "key": "agent_muse",
        "name": {
            "zh-CN": "灵感种子",
            "en-US": "Muse Seed",
            "ja-JP": "着想シード",
            "ko-KR": "영감 시드",
        },
        "display": {
            "zh-CN": "负责灵感扩展与创意生成。",
            "en-US": "Expands inspirations and explores ideas.",
            "ja-JP": "着想の拡張と发散支援を担当します。",
            "ko-KR": "영감의 확장과 크리에이티브한 아이디어 생성을 담당합니다.",
        },
        "description": {
            "zh-CN": '负责所有与"灵感"相关的任务：创意点子、脑洞扩展、灵感启发、查看/回顾灵感内容。当用户消息中包含"灵感"二字，或用户卡文需要创意建议时使用。',
            "en-US": 'Handles all "inspiration" related tasks: creative ideas, brainstorming, inspiration prompts, viewing/reviewing inspiration content. Used when the user mentions "inspiration" or needs creative suggestions for writer\'s block.',
            "ja-JP": '「着想」に関連するすべてのタスクを担当：クリエイティブなアイデア、ブレインストーミング、着想の促進、着想内容の確認/振り返り。ユーザーが「着想」に言及した場合や、行き詰まった時にクリエイティブな提案が必要な場合に使用します。',
            "ko-KR": "‘영감’과 관련된 모든 태스크를 담당합니다: 크리에이티브 아이디어, 브레인스토밍, 영감 고취, 영감 내용 확인 및 복기 등의 작업을 수행합니다. 사용자 메시지에 ‘영감’이라는 단어가 포함되어 있거나, 아이디어가 고갈되어 창작에 막힘이 생겼을 때 사용됩니다。",
        },
        "group": "main",
        "icon": "Wand2",
        "color": "#b07cff"
    },
    {
        "key": "agent_lorebook",
        "name": {
            "zh-CN": "设定专家",
            "en-US": "Lore Specialist",
            "ja-JP": "設定エキスパート",
            "ko-KR": "설정 전문가",
        },
        "display": {
            "zh-CN": "负责世界观与角色生成。",
            "en-US": "Maintains worldbuilding consistency and settings.",
            "ja-JP": "世界観と設定の整合性を担当します。",
            "ko-KR": "세계관 구축 및 캐릭터 생성을 담당합니다.",
        },
        "description": {
            "zh-CN": "负责世界观设定、角色档案、人物关系、背景故事、百科。当用户想要增加、修改或查看设定时使用。",
            "en-US": "Handles worldbuilding settings, character profiles, relationships, backstories, and lore encyclopedia. Used when the user wants to add, modify, or view settings.",
            "ja-JP": "世界観の設定、キャラクタープロフィール、人物関係、バックストーリー、百科事典を担当します。ユーザーが設定を追加、変更、確認したい場合に使用します。",
            "ko-KR": "세계관 설정, 캐릭터 프로필, 인물 관계도, 배경 스토리, 백과사전 관리를 담당합니다. 사용자가 설정을 추가, 수정 또는 확인하고자 할 때 사용됩니다。",
        },
        "group": "main",
        "icon": "ScrollText",
        "color": "#f5b942"
    },
    {
        "key": "agent_style",
        "name": {
            "zh-CN": "文风克隆",
            "en-US": "Style Clone",
            "ja-JP": "文体クローン",
            "ko-KR": "문체 클론",
        },
        "display": {
            "zh-CN": "负责风格分析、风格迁移等所有风格相关任务。",
            "en-US": "Unifies writing style and tone.",
            "ja-JP": "文体と表現の統一を担当します。",
            "ko-KR": "스타일 분석, 스타일 전이 등 모든 문체 관련 작업을 담당합니다.",
        },
        "description": {
            "zh-CN": "负责文风分析、风格仿写、语气调优。当用户想要模仿某人写东西或调整语言风格时使用。",
            "en-US": "Handles style analysis, style imitation, and tone tuning. Used when the user wants to imitate someone's writing or adjust language style.",
            "ja-JP": "文体分析、スタイル模写、トーン調整を担当します。誰かの書き方を模倣したい場合や、言語スタイルを調整したい場合に使用します。",
            "ko-KR": "문체 분석, 스타일 모사, 어조 튜닝을 담당합니다. 사용자가 누군가의 필체를 모방하여 집필하고 싶거나 언어 스타일을 조정하고자 할 때 사용됩니다。",
        },
        "group": "style",
        "icon": "Palette",
        "color": "#ec4899"
    },
    {
        "key": "agent_utility",
        "name": {
            "zh-CN": "系统工具",
            "en-US": "System Utility",
            "ja-JP": "システムツール",
            "ko-KR": "시스템 툴",
        },
        "display": {
            "zh-CN": "负责上下文压缩与聊天附件预处理等系统内部任务。",
            "en-US": "Handles internal system tasks such as context compaction and chat attachment preprocessing.",
            "ja-JP": "コンテキスト圧縮やチャット添付の前処理など、内部システムタスクを担当します。",
            "ko-KR": "컨텍스트 압축 및 채팅 첨부 파일 전처리 등 시스템 내부 태스크를 담당합니다.",
        },
        "description": {
            "zh-CN": "系统内部工具门面，不进入聊天入口、不参与信标总线或导演委派；上下文压缩必须复用所属 Agent 的当前模型，附件预处理由本地管线完成。",
            "en-US": "An internal utility facade. It is hidden from chat entry points and delegation; context compaction reuses the owning Agent's current model, while attachment preprocessing stays local.",
            "ja-JP": "内部ユーティリティのファサードです。チャット入口や委任には表示されず、コンテキスト圧縮は所属 Agent の現在のモデルを再利用し、添付前処理はローカル処理で行います。",
            "ko-KR": "시스템 내부 유틸리티 파사드입니다. 채팅 진입점과 감독 위임에는 노출되지 않으며, 컨텍스트 압축은 소유 Agent의 현재 모델을 재사용하고 첨부 전처리는 로컬 파이프라인에서 수행합니다.",
        },
        "group": "system",
        "participatesInBeaconBus": False,
        "visibleInChat": False,
        "visibleInModelBinding": False,
        "icon": "Settings2",
        "color": "#64748b"
    },
    {
        "key": "agent_story_memory",
        "name": {
            "zh-CN": "故事记忆",
            "en-US": "Story Memory",
            "ja-JP": "ストーリーメモリ",
            "ko-KR": "스토리 메모리",
        },
        "display": {
            "zh-CN": "负责整理已保存场景中的叙事状态与跨场事实。",
            "en-US": "Extracts narrative state and cross-scene facts from saved scenes.",
            "ja-JP": "保存済みシーンから物語状態とシーン間の事実を整理します。",
            "ko-KR": "저장된 장면에서 서사 상태와 장면 간 사실을 정리합니다.",
        },
        "description": {
            "zh-CN": "系统内部的故事记忆整理器，在场景保存后异步抽取人物状态、事件、伏笔和事实，供后续创作核对。它不是聊天 Agent，不参与导演委派，也不开放单独模型绑定。",
            "en-US": "An internal story-memory processor that asynchronously extracts character states, events, foreshadowing, and facts after scenes are saved for later continuity checks. It is not a chat agent, is not delegated by the Director, and has no dedicated model binding.",
            "ja-JP": "シーン保存後に人物状態、出来事、伏線、事実を非同期で抽出し、後続の創作確認に使う内部ストーリーメモリ処理器です。チャット Agent ではなく、監督から委任されず、個別モデルの紐付けにも対応しません。",
            "ko-KR": "장면 저장 후 인물 상태, 사건, 복선 및 사실을 비동기로 추출하여 후속 창작의 연속성 확인에 사용하는 내부 스토리 메모리 처리기입니다. 채팅 Agent가 아니며 감독 위임과 개별 모델 바인딩을 지원하지 않습니다.",
        },
        "group": "system",
        "routable": False,
        "participatesInBeaconBus": False,
        "visibleInChat": False,
        "visibleInModelBinding": False,
        "visibleInUsage": True,
        "icon": "Sparkles",
        "color": "#8b9cf6"
    }
]


def _resolve_i18n_field(field_value, locale: str = 'zh-CN') -> str:
    """从多语言字典中提取指定 locale 的文本；若非字典则原样返回。"""
    if isinstance(field_value, dict):
        return field_value.get(locale, field_value.get('zh-CN', ''))
    return field_value


def get_agent_registry(locale: str | None = None):
    """返回 Agent 注册表。若指定 locale，则将 name/display/description 展开为纯字符串。"""
    if locale is None:
        return AGENT_REGISTRY

    resolved = []
    for entry in AGENT_REGISTRY:
        item = {k: v for k, v in entry.items() if k not in ('name', 'display', 'description')}
        item['name'] = _resolve_i18n_field(entry['name'], locale)
        item['display'] = _resolve_i18n_field(entry['display'], locale)
        item['description'] = _resolve_i18n_field(entry['description'], locale)
        resolved.append(item)
    return resolved
