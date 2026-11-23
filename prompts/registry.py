"""Ticket 400: the one list of text that can reach the model.

Every entry here is visible in the instructions page. Kind decides how it is shown and stored:

- text   default text in prompts/defaults/<id>.md; Lan's override in persona/instructions/<id>.md
- marker a block the program fills in at request time; visible, never editable

Persona, background, talking samples and his notes are NOT registered here: each already has its own
page. Two editable copies of the same text is how versions get confused (Lan, 2026-11-20).

An entry with editable=False is still listed with its full text; it is only refused as a target of
edits (protocol or safety text the program depends on).
"""

CATEGORIES = (
    ('identity', '人格与身份'),
    ('voice', '说话方式'),
    ('background', '背景资料'),
    ('memory', '记忆'),
    ('autonomy', '自主活动'),
    ('tools', '工具'),
    ('system', '系统与安全'),
    ('runtime', '运行时'),
)

CATEGORY_IDS = tuple(item[0] for item in CATEGORIES)
CATEGORY_TITLES = dict(CATEGORIES)
KINDS = ('text', 'marker')


class Entry:
    __slots__ = ('id', 'category', 'title', 'kind', 'site', 'editable', 'max_bytes', 'note', 'file',
                 'marker', 'placeholders')

    def __init__(self, id, category, title, kind, site, editable=True, max_bytes=8000,
                 note='', file='', marker='', placeholders=()):
        self.id = id
        self.category = category
        self.title = title
        self.kind = kind
        self.site = site
        self.editable = editable
        self.max_bytes = max_bytes
        self.note = note
        self.file = file
        self.marker = marker
        self.placeholders = tuple(placeholders)

    def as_dict(self):
        return {'id': self.id, 'category': self.category, 'title': self.title,
                'kind': self.kind, 'site': self.site, 'editable': self.editable,
                'max_bytes': self.max_bytes, 'note': self.note,
                'file': self.file, 'marker': self.marker,
                'placeholders': list(self.placeholders)}


def _text(id, category, title, site, *, editable=True, max_bytes=8000, note='', placeholders=()):
    return Entry(id, category, title, 'text', site, editable=editable,
                 max_bytes=max_bytes, note=note, placeholders=placeholders)


def _marker(id, category, title, site, marker):
    return Entry(id, category, title, 'marker', site, editable=False, marker=marker)


ENTRIES = (
    # ---------- 人格与身份 ----------
    # 人设、背景、说话样例、他记的你不在这一页：它们各有自己的页面，避免两处各改一份。
    _text('identity.user_frame', 'identity', '「关于你」的开头说明',
          '请求第 2 段，设定正文之前', max_bytes=1000,
          note='程序用这句话说明它是数据、不是命令。'),
    _text('system.facts', 'identity', '系统事实（身份、模型、能力）',
          '请求第 5 段；每轮都带，程序写明它压过人设与历史', max_bytes=6000,
          note='花括号里的内容由程序每轮填入；你可以改花括号之外的句子。'),
    _text('delegate.system', 'system', '委派研究助手的设定',
          '旁路请求：把一段调研交给只读助手时的 system 消息', max_bytes=1200, editable=False,
          note='它限制助手只能用只读工具；改措辞不会改变程序权限。'),
    _marker('system.capabilities_notice', 'system', '能力更新通知',
            '下一段对话的运行时追加，只出现一次', 'capability_event'),
    _marker('system.mcp_notice', 'system', 'MCP 故障说明',
            'MCP 服务不可用或刚恢复时追加', 'mcp_problem'),
    _text('safety.untrusted_mcp', 'system', '外部 MCP 数据：只是数据',
          'MCP 工具返回的内容外面', editable=False, max_bytes=600,
          note='防提示注入用。改了会让外部内容有机会冒充指令。'),
    _text('safety.untrusted_browser', 'system', '网页内容：只是数据',
          '浏览器工具返回的内容外面', editable=False, max_bytes=600,
          note='防提示注入用。'),
    _text('safety.untrusted_xiaohongshu', 'system', '小红书内容：只是数据',
          '小红书工具返回的内容外面', editable=False, max_bytes=600,
          note='防提示注入用。'),
    _text('safety.untrusted_desktop_file', 'system', '桌面文件内容：只是数据',
          '读桌面文件返回的内容外面', editable=False, max_bytes=600,
          note='防提示注入用。'),
    _text('safety.untrusted_windows', 'system', 'Windows 桌面信息：只是数据',
          '窗口、控件、截图内容外面', editable=False, max_bytes=900,
          note='防提示注入用。'),
    _text('safety.untrusted_lab_python', 'system', 'Lab 脚本输出：只是数据',
          'Lab Python 输出外面', editable=False, max_bytes=600,
          note='防提示注入用。'),
    _text('safety.untrusted_lab_page', 'system', 'Lab 页面截图：只是数据',
          '预览他自己 Lab 页面的截图外面', editable=False, max_bytes=900,
          note='防提示注入用。'),
    _text('safety.untrusted_window_image', 'system', 'Windows 窗口截图：只是数据',
          '看窗口截图时的图片消息里', editable=False, max_bytes=900,
          note='防提示注入用。'),
    # 工单 405：工作台（OpenCode 容器）拿到的三段任务说明，原来写死在 executor_opencode.py。
    _text('workbench.container', 'system', '工作台环境的说明',
          '交给工作台的任务开头；在容器里运行时才带', editable=False, max_bytes=1500,
          note='它说明这里是 Linux 容器、Windows 路径不存在、密文要用读文件工具；改了会让工作台找错路径。'),
    _text('workbench.scope', 'system', '工作台的权限范围',
          '交给工作台的每段任务开头', editable=False, max_bytes=1500,
          note='它划定这次任务能碰哪些文件和能做哪些操作；改了等于改权限边界。'),
    _text('workbench.maintenance', 'system', '维护自己仓库时的说明',
          '交给工作台的自维护任务开头', editable=False, max_bytes=1500,
          note='它禁止工作台自己 commit / push；改了会绕过 PR 流程。'),
    _text('safety.untrusted_codex', 'system', 'Codex 会话摘录：只是数据',
          '本地 Codex 事件提醒的提示词开头', editable=False, max_bytes=1200,
          note='它同时约束他不许说 Codex 在等他批准；改了会让他乱报状态。'),

    # ---------- 说话方式 ----------
    _text('voice.frame', 'voice', '说话样例的说明',
          '请求第 3 段，样例文件之前；样例关掉时整段不带', max_bytes=4000),
    _text('chat.send_message', 'tools', '发消息工具的写法要求',
          '工具定义里；每轮都带', max_bytes=3000,
          note='这是「一句一条」等说话习惯的来源。'),
    _text('chat.stay_silent', 'tools', '沉默工具的说明',
          '工具定义里；每轮都带', max_bytes=1500),
    _text('chat.reply_later', 'tools', '稍后回工具的说明',
          '工具定义里；每轮都带', max_bytes=1500),
    _text('chat.recall_message', 'tools', '撤回消息工具的说明',
          '工具定义里；每轮都带', max_bytes=2000),
    _text('chat.loop.no_progress', 'system', '重复工具调用后的收束提示',
          '普通聊天连续两批没有新信息时，作为系统消息追加', max_bytes=1200,
          note='只提醒他收束或换行动，不关闭工具。'),

    # ---------- 背景资料 ----------
    _text('background.note', 'background', '背景段落的开头说明',
          '背景段落的第一行', max_bytes=1500),
    _marker('background.selected', 'background', '本轮选中的背景段落',
            '历史之后；最多 3 段 / 1500 字', 'background'),

    # ---------- 记忆 ----------
    _text('memory.learned.header', 'memory', '记忆笔记的开头',
          '请求第 4 段；有内容才带', max_bytes=1500),
    _text('memory.learned.growth', 'memory', '「关系成长」小标题',
          '请求第 4 段内', max_bytes=800),
    _text('memory.learned.facts', 'memory', '「重要事实」小标题',
          '请求第 4 段内', max_bytes=800),
    _text('memory.relationship_base.header', 'memory', '稳定关系底色的开头',
          '请求第 4 段内；有当前有效的关系底色才带', max_bytes=1200),
    _text('memory.ongoing.header', 'memory', '「正在进行」的开头',
          '请求靠后，用户消息之前；有进行中的事才带', max_bytes=1500),
    _text('memory.ongoing.done', 'memory', '「已完成」的计数行',
          '紧跟在进行中之后；有已完成的条目才带，不带正文', max_bytes=600,
          placeholders=('count',),
          note='只告诉他有几条已完成；他要看内容时用 ongoing_update 的 list。'),
    _text('memory.recall.head', 'memory', '自动想起记忆的开头',
          '历史之后、运行时限制之前', max_bytes=2000),
    _text('memory.recall.empty', 'memory', '没想起任何记忆时的说明',
          '同上，没有命中时替换条目', max_bytes=800),
    _text('memory.association.label', 'memory', '自然浮现那一条的前缀说明',
          '不在检索命中里、由 Peter 自己想起来的旧事', max_bytes=600),
    _text('memory.opening.head', 'memory', '开场记忆的开头',
          '新对话第一次请求', max_bytes=2000),
    _text('memory.opening.empty', 'memory', '没有开场记忆时的说明',
          '新对话第一次请求', max_bytes=800),
    _text('memory.judge.prompt', 'memory', '想起判官的提示词（聊天）',
          '旁路请求：判断哪些记忆和这句话直接相关', max_bytes=2000, editable=False,
          note='它必须只回 JSON；改措辞要同步改解析。'),
    _text('memory.judge.wake', 'memory', '想起判官的提示词（自主活动）',
          '旁路请求：判断哪些记忆和这次醒来相关', max_bytes=2000, editable=False,
          note='同上。'),
    _text('memory.experience.prompt', 'memory', '经历记忆整理的提示词',
          '旁路请求：攒够聊天后写「他会记得的事」', max_bytes=6000, editable=False,
          note='花括号是条数和日期参数，由程序填；它必须只回 JSON。'),
    _text('memory.experience.fix', 'memory', '经历记忆的修稿提示词',
          '旁路请求：草稿不合格时只改正文', max_bytes=2500, editable=False,
          note='同上。'),
    _text('life.calendar.agreement', 'runtime', '聊天约定写入日历的判断提示词',
          '旁路请求：聊天出现日期、确认或取消时判断是否改日历', max_bytes=30000,
          editable=False, placeholders=('today','existing','recent_chat','current_turn'),
          note='它必须只回 JSON；改措辞要同步改解析。'),
    _text('memory.event.group', 'memory', '事件归组的提示词',
          '旁路请求：判断一次保存是否属于已有经历', max_bytes=4000, editable=False,
          note='它必须只回 JSON；改措辞要同步改解析。'),
    _text('memory.event.story', 'memory', '事件故事的整理提示词',
          '旁路请求：把多个阶段写成一段完整经历', max_bytes=4000, editable=False,
          note='要求保留日期/数字/原话，区分事实与猜测，并写出理解变化。'),
    _text('memory.event.story.merge', 'memory', '事件故事的增量合并提示词',
          '旁路请求：把新阶段并进已有故事', max_bytes=4000, editable=False,
          note='保留旧事实，不重复，不丢细节。'),
    _text('memory.short_notes.extract', 'memory', '短期便签提取提示词',
          '旁路请求：从聊天里抽临时便签（当前开关是关的）', max_bytes=4000, editable=False,
          note='它必须只回 JSON。'),
    _text('memory.experience.heading', 'memory', '最近几天经历笔记的开头',
          '请求靠前，聊天历史之前', max_bytes=3000),
    _marker('memory.recalled', 'memory', '自动想起的记忆条目',
            '历史之后；最多 3 条', 'recall'),
    _marker('memory.opening', 'memory', '开场记忆条目',
            '新对话第一次请求', 'opening'),
    _marker('memory.ongoing', 'memory', '正在进行的事',
            '用户消息之前', 'ongoing'),
    _marker('memory.recent_experiences', 'memory', '上次瞄一眼的记录',
            '运行时追加；下一次瞄一眼会替换', 'recent_experiences'),
    _marker('memory.pending_approvals', 'memory', '待批准的动作',
            '自主活动醒来提示里', 'pending_approvals'),
    # 工单 405：生活记录与日程这一段原来写死在 life_state.py 里，界面上既看不到也改不了。
    _text('life.state.frame', 'memory', '生活记录与日程的开头',
          '聊天与醒来提示里；有生活记录、日程或任务时才带', max_bytes=1200,
          note='花括号是程序填的日程与任务数据，可以改花括号之外的句子。',
          placeholders=('data',)),
    _text('life.state.rules', 'memory', '生活记录的使用规则',
          '同上，紧跟在数据后面', max_bytes=1200,
          note='它决定他会不会把暂停或已完成的事当成现在的事。'),
    _text('life.state.more_events', 'memory', '没显示完的日程提示',
          '同上；这几天的日程超过上限时才带', max_bytes=600,
          placeholders=('count',)),

    # ---------- 自主活动 ----------
    _text('autonomy.wake.header', 'autonomy', '醒来提示的过渡句',
          '醒来提示（当轮用户消息）中段', max_bytes=1000),
    _text('autonomy.wake.rules', 'autonomy', '醒来提示的行为段',
          '醒来提示里「Time is yours…」那一段', max_bytes=8000,
          note='这一段可以随便改；它后面四行记录格式是程序加的，不在这条里。'),
    _text('autonomy.wake.protocol', 'autonomy', '醒来记录的四行格式',
          '醒来提示最末尾；程序靠它写活动记录', max_bytes=1200, editable=False,
          note='Decision / Reason / Focus / Next step 四行是程序读活动记录用的，改了会记不下这次活动；所以只读。'),
    _text('autonomy.threads.frame', 'autonomy', '跨唤醒线索的开头',
          '醒来提示里；[autonomy] threads 关闭时不带', max_bytes=1500),
    _text('autonomy.reads.frame', 'autonomy', '已读未变的开头',
          '醒来提示里；同上', max_bytes=1500),
    # 工单 405：以下四段原来写死在 autonomy.py 里。
    _text('autonomy.lab.frame', 'autonomy', 'Lab 文件清单的开头',
          '醒来提示里；Lab 有文件时才带', max_bytes=600),
    _text('autonomy.lab.more', 'autonomy', 'Lab 文件太多时的提示',
          '同上；清单超过上限时才带', max_bytes=300,
          placeholders=('count',)),
    _text('autonomy.lab.newest', 'autonomy', '最新 Lab 文件的开头',
          '同上，跟着文件开头与结尾的内容', max_bytes=300,
          placeholders=('name',)),
    _text('autonomy.earlier.frame', 'autonomy', '更早自主活动的开头',
          '醒来提示里；至少有过两次自主活动时才带', max_bytes=600),
    _text('autonomy.budget.line', 'autonomy', '今日预算这一行',
          '醒来提示里；花括号里是程序算的金额', max_bytes=600,
          placeholders=('used', 'pending', 'left', 'cap')),
    _text('autonomy.wake_count', 'autonomy', '今天醒了几次',
          '醒来提示里；花括号里是程序和配置的数', max_bytes=600,
          placeholders=('used', 'max')),
    _text('autonomy.wake_quiet', 'autonomy', '安静时段的说明',
          '同上；开了安静时段才带', max_bytes=400,
          placeholders=('hours',)),
    _text('autonomy.wake_rhythm', 'autonomy', '自然醒来节奏的说明',
          '同上', max_bytes=400,
          placeholders=('rhythm',)),
    _text('autonomy.wake_scheduled', 'autonomy', '他自己定的闹钟',
          '同上；有排定的醒来时才带', max_bytes=400,
          placeholders=('alarms',)),
    _text('autonomy.glance.rules', 'autonomy', '瞄一眼的说明',
          '瞄一眼请求的 system 上下文', max_bytes=3000),
    _text('autonomy.glance.record', 'autonomy', '瞄一眼记录的开头',
          '下一次请求里的「上次瞄一眼」', max_bytes=1200, editable=False,
          note='程序记录的框架句；改了只会改变措辞，不会改变记录内容。'),
    _text('autonomy.previous.card', 'autonomy', '上一次自主活动的记录卡片',
          '醒来提示里、用户消息之前', max_bytes=1500, editable=False,
          note='程序记录的框架句；花括号由程序填。'),
    _text('autonomy.previous.frame', 'autonomy', '上一次自主活动的简短说明',
          '醒来提示里', max_bytes=900, editable=False),
    _text('autonomy.travel.option', 'autonomy', '乌有乡旅行提示',
          '醒来提示里；只有配置了乌有乡才带', max_bytes=1500,
          note='这一段是行为建议，可以改。'),
    _text('autonomy.glance.note', 'autonomy', '瞄一眼的收尾要求',
          '瞄一眼请求的 system 上下文末尾', max_bytes=1500),
    _marker('autonomy.previous', 'autonomy', '上一次自主活动',
            '醒来提示里；程序记录的 JSON', 'autonomy_previous'),
    _marker('autonomy.earlier', 'autonomy', '更早的自主活动',
            '醒来提示里；最多再往前几次', 'autonomy_earlier'),
    _marker('autonomy.threads', 'autonomy', '跨唤醒线索的条目',
            '醒来提示里', 'threads'),
    _marker('autonomy.reads', 'autonomy', '已读未变的文件清单',
            '醒来提示里', 'reads'),
    _marker('autonomy.task', 'autonomy', '当前长期任务',
            '醒来提示里', 'long_task'),
    _marker('autonomy.life', 'autonomy', '生活记录与日程',
            '醒来提示里', 'life'),
    _marker('autonomy.lab', 'autonomy', 'Lab 最新文件',
            '醒来提示里', 'lab'),
    _marker('autonomy.thoughts', 'autonomy', '想法索引摘录',
            '醒来提示里；最多两条', 'thoughts'),
    _marker('autonomy.budget', 'autonomy', '今日预算',
            '醒来提示里', 'budget'),
    _marker('autonomy.missed_alarms', 'autonomy', '错过的闹钟',
            '醒来提示里；有才带', 'missed_alarms'),

    # ---------- 系统与安全 ----------
    # 工单 405：她多久没说话的这段原来写死在 life_state.py 里，界面上看不到；
    # 其中 life.time_guidance 正是「不必提、可能在忙」这几句的来源。
    _text('life.time_gap', 'runtime', '距她上次说话的时间（数据句）',
          '聊天每轮末尾、自主活动醒来提示的开头；超过一小时才带', max_bytes=800,
          note='花括号是程序算的时长和她最后说话的时间。',
          placeholders=('duration', 'when')),
    _text('life.time_gap.proactive', 'runtime', '这之后他主动找过她几次',
          '同上；他在自主活动里发过消息才带', max_bytes=400,
          placeholders=('count', 'latest')),
    _text('life.time_gap.last', 'runtime', '她最后几条可见消息',
          '同上；有她的消息才带', max_bytes=400,
          placeholders=('messages',)),
    _text('life.time_guidance', 'runtime', '她沉默时他怎么理解',
          '同上，紧跟在时间数据后面', max_bytes=1200,
          note='这一段决定他提不提「你多久没理我」，也是现在让他选择不提的那几句。'),
    _text('runtime.investigation_checkpoint.rules', 'runtime', '调查续接的说明',
          '调查断点那一段里的 instruction 字段', editable=False, max_bytes=800,
          note='它约束他不重放已经拒绝或结果未知的副作用；改了会让他重复执行。'),
    _marker('runtime.time_ledger', 'runtime', '时间与花费账本',
            '每轮最后一条 system 消息；每轮都变', 'time_ledger'),
    _marker('runtime.history', 'runtime', '聊天历史',
            '人设之后、当轮消息之前；默认 80 轮', 'history'),
    _marker('runtime.time_separators', 'runtime', '历史里的时间分隔行',
            '超过一小时的两条消息之间', 'time_separator'),
    _marker('runtime.work_checkpoint', 'runtime', '工作断点',
            '历史之后；新对话或丢 20 轮时再带', 'work_checkpoint'),
    _marker('runtime.investigation_checkpoint', 'runtime', '调查断点',
            '历史之后；命中时带', 'investigation_checkpoint'),
    _marker('runtime.recall_candidates', 'runtime', '撤回候选',
            '历史之后；120 秒内他自己的消息', 'recall_candidates'),
    _marker('runtime.virtual_cards', 'runtime', '虚拟卡片',
            '历史之后；有待处理卡片时', 'virtual_cards'),
    _marker('runtime.screen_pop', 'runtime', '弹幕与屏幕消息记录',
            '历史之后；有记录时', 'screen_pop'),
    _marker('runtime.channel_marker', 'runtime', '渠道来源标记（QQ / 邮件）',
            '你的消息最前面', 'channel_marker'),
)

# These four tool descriptions already have their own text entry, so the tool layer skips them.
TEXT_TOOL_IDS = {
    'send_message': 'chat.send_message',
    'stay_silent': 'chat.stay_silent',
    'reply_later': 'chat.reply_later',
    'recall_message': 'chat.recall_message',
}

BY_ID = {entry.id: entry for entry in ENTRIES}


def entries():
    return list(ENTRIES)


def get(entry_id):
    try:
        return BY_ID[entry_id]
    except KeyError:
        raise KeyError('未登记的指令编号：' + str(entry_id)) from None


def by_category():
    result = []
    for category, title in CATEGORIES:
        result.append({'id': category, 'title': title,
                       'entries': [entry for entry in ENTRIES if entry.category == category]})
    return result


def validate(defaults_dir):
    """Every text entry must have a default file; ids and categories must be unique and valid."""
    from pathlib import Path
    seen = set()
    for entry in ENTRIES:
        if entry.id in seen:
            raise ValueError('重复的指令编号：' + entry.id)
        seen.add(entry.id)
        if entry.category not in CATEGORY_IDS:
            raise ValueError('未知分类：' + entry.id)
        if entry.kind not in KINDS:
            raise ValueError('未知类型：' + entry.id)
        if entry.kind == 'text':
            path = Path(defaults_dir) / (entry.id + '.md')
            if not path.is_file():
                raise ValueError('缺少默认文本：' + str(path))

    return True
